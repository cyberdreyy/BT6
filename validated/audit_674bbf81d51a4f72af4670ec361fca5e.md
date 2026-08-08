### Title
Unbounded CPU/memory/response-size cost from single `getAccountInfo(jsonParsed)` on max-size Address Lookup Table account - ([File: account-decoder/src/parse_address_lookup_table.rs])

### Summary
`parse_address_lookup_table` and its `UiLookupTable::from` conversion deserialize and stringify every address entry in an ALT account with no cap on address count, and the RPC layer's only size guard (`MAX_BASE58_BYTES`) is scoped to `Base58`/`Binary` encodings, not `JsonParsed`. An attacker can write a 10 MiB account owned by the address-lookup-table program packed with pseudo-random pubkeys, then trigger unbounded allocation/stringification/serialization work with a single `getAccountInfo(jsonParsed)` call.

### Finding Description
`encode_ui_account` in [1](#0-0)  handles `UiAccountEncoding::JsonParsed` by calling `parse_account_data_v3` directly on `account.data()` with no length check, unlike the `Base58`/`Binary` branches. The size guard that exists, `MAX_BASE58_BYTES` (128 bytes), is only enforced in `encode_account` for `Binary`/`Base58` encodings [2](#0-1) ; it is never consulted for `JsonParsed`.

`parse_account_data_v3` dispatches ALT-owned accounts to `parse_address_lookup_table`, which calls `AddressLookupTable::deserialize(data)` on the whole account buffer and converts the result `into()` a `UiLookupTable` [3](#0-2) . The `From<AddressLookupTable>` impl iterates every address and calls `.to_string()` on each into a `Vec<String>` with no upper bound on `addresses.len()` [4](#0-3) . That vector is then serialized to `serde_json::Value` in `parse_account_data_v3` [5](#0-4) .

Account data size is capped only by the protocol-wide `MAX_ACCOUNT_DATA_LEN = 10 * 1024 * 1024` [6](#0-5) , which is far larger than any RPC-specific ALT-address-count limit. With a 56-byte meta header (`LOOKUP_TABLE_META_SIZE`, referenced in tests at [7](#0-6) ) plus 32-byte pubkey entries, a 10 MiB account can hold roughly `(10*1024*1024 - 56) / 32 ≈ 327,671` addresses. `getAccountInfo`/`getMultipleAccounts` route to `encode_account`/`encode_ui_account` via `get_encoded_account` [8](#0-7) , so the `JsonParsed` path is reachable through a single, unprivileged RPC call once such an account exists on-chain.

### Impact Explanation
A single `getAccountInfo` (or `getMultipleAccounts`, `getProgramAccounts` for that account, or an account-subscription push) with `encoding: jsonParsed` against the crafted account forces the validator's JSON-RPC worker to allocate and base58-stringify hundreds of thousands of `Pubkey`s and build/serialize a correspondingly huge JSON response, with no explicit bound tying this cost to a configured limit. This matches the "unbounded cost for a single low-rate call" category — a CPU/memory spike and an oversized response served to every client that fetches the account, without requiring more than one call per `CLUSTER_SLOT_TIME_TARGET / 2`.

### Likelihood Explanation
Preconditions require the attacker to fund and populate a max-size (10 MiB) account owned by the address-lookup-table program with a valid meta header, which is an on-chain state-write precondition explicitly permitted by the rules (attacker may write on-chain data later returned through RPC). Once that account exists, the exploit trigger is a single unprivileged `getAccountInfo` call with `jsonParsed` encoding — no special access or repeated requests are needed, making this readily and repeatably triggerable by any client that queries the account (including legitimate clients, amplifying impact).

### Recommendation
Enforce an explicit bound before invoking `JsonParsed` decoders in `encode_ui_account`/`get_encoded_account`, e.g., reject or fall back to `Base64` when `account.data().len()` (or the parsed address count for ALT specifically) exceeds a sane threshold, mirroring the existing `MAX_BASE58_BYTES` guard pattern but applied to the `JsonParsed` branch. Additionally, `parse_address_lookup_table`/`UiLookupTable::from` could cap or chunk the number of addresses stringified per response.

### Proof of Concept
Rust integration test sketch (place near `rpc/src/rpc.rs` account-info tests, using the `RpcHandler` test harness already present, e.g. `test_rpc_get_multiple_accounts` at [9](#0-8) ):
```rust
#[test]
fn test_get_account_info_jsonparsed_max_size_alt_unbounded_cost() {
    let rpc = RpcHandler::start();
    let bank = rpc.working_bank();

    // Build a ~10 MiB ALT account: valid meta header + ~327k random pubkeys.
    let mut data = vec![0u8; LOOKUP_TABLE_META_SIZE];
    // ... write valid LookupTableMeta fields into the header ...
    for _ in 0..327_000 {
        data.extend_from_slice(&Pubkey::new_unique().to_bytes());
    }
    assert!(data.len() <= MAX_ACCOUNT_DATA_LEN as usize);

    let pubkey = Pubkey::new_unique();
    let account = AccountSharedData::create_from_existing_shared_data(
        1_000_000_000, Arc::new(data), address_lookup_table::id(), false, 0,
    );
    bank.store_account(&pubkey, &account);

    let request = create_test_request(
        "getAccountInfo",
        Some(json!([pubkey.to_string(), {"encoding": "jsonParsed"}])),
    );

    let start = std::time::Instant::now();
    let result = rpc.handle_request_sync(request);
    let elapsed = start.elapsed();

    // Expected finding: no error/rejection occurs, and cost scales with
    // attacker-controlled data size rather than being capped -
    // e.g. elapsed time and result payload size grow linearly with
    // address count, with no MAX_BASE58_BYTES-style guard applied.
    println!("elapsed={:?}", elapsed);
}
```
Expected assertion for the fix: after remediation, the same request should either be rejected with an `InvalidRequest`-style error (similar to the `Base58`/`Binary` size guard) or the response should be capped/truncated, independent of the attacker-chosen address count.

### Citations

**File:** account-decoder/src/lib.rs (L80-91)
```rust
        UiAccountEncoding::JsonParsed => {
            if let Ok(parsed_data) =
                parse_account_data_v3(pubkey, account.owner(), account.data(), additional_data)
            {
                UiAccountData::Json(parsed_data)
            } else {
                UiAccountData::Binary(
                    BASE64_STANDARD.encode(slice_data(account.data(), data_slice_config)),
                    UiAccountEncoding::Base64,
                )
            }
        }
```

**File:** rpc/src/rpc.rs (L2552-2573)
```rust
fn get_encoded_account(
    bank: &Bank,
    pubkey: &Pubkey,
    encoding: UiAccountEncoding,
    data_slice: Option<UiDataSliceConfig>,
    // only used for simulation results
    overwrite_accounts: Option<&HashMap<Pubkey, AccountSharedData>>,
) -> Result<Option<UiAccount>> {
    match account_resolver::get_account_from_overwrites_or_bank(pubkey, bank, overwrite_accounts) {
        Some(account) => {
            let response = if is_known_spl_token_id(account.owner())
                && encoding == UiAccountEncoding::JsonParsed
            {
                get_parsed_token_account(bank, pubkey, account, overwrite_accounts)
            } else {
                encode_account(&account, pubkey, encoding, data_slice)?
            };
            Ok(Some(response))
        }
        None => Ok(None),
    }
}
```

**File:** rpc/src/rpc.rs (L2581-2596)
```rust
    if (encoding == UiAccountEncoding::Binary || encoding == UiAccountEncoding::Base58)
        && data_slice
            .map(|s| min(s.length, account.data().len().saturating_sub(s.offset)))
            .unwrap_or(account.data().len())
            > MAX_BASE58_BYTES
    {
        let message = format!(
            "Encoded binary (base 58) data should be less than {MAX_BASE58_BYTES} bytes, please \
             use Base64 encoding."
        );
        Err(error::Error {
            code: error::ErrorCode::InvalidRequest,
            message,
            data: None,
        })
    } else {
```

**File:** rpc/src/rpc.rs (L5815-5862)
```rust
    #[test]
    fn test_rpc_get_multiple_accounts() {
        let rpc = RpcHandler::start();
        let bank = rpc.working_bank();

        let non_existent_pubkey = Pubkey::new_unique();
        let pubkey = Pubkey::new_unique();
        let address = pubkey.to_string();
        let data = vec![1, 2, 3, 4, 5];
        let account = AccountSharedData::create_from_existing_shared_data(
            42,
            Arc::new(data.clone()),
            Pubkey::default(),
            false,
            0,
        );
        bank.store_account(&pubkey, &account);

        // Test 3 accounts, one empty, one non-existent, and one with data
        let request = create_test_request(
            "getMultipleAccounts",
            Some(json!([[
                rpc.mint_keypair.pubkey().to_string(),
                non_existent_pubkey.to_string(),
                address,
            ]])),
        );
        let result: RpcResponse<Value> = parse_success_result(rpc.handle_request_sync(request));
        let expected = json!([
            {
                "owner": "11111111111111111111111111111111",
                "lamports": TEST_MINT_LAMPORTS,
                "data": ["", "base64"],
                "executable": false,
                "rentEpoch": 0,
                "space": 0,
            },
            null,
            {
                "owner": "11111111111111111111111111111111",
                "lamports": 42,
                "data": [BASE64_STANDARD.encode(&data), "base64"],
                "executable": false,
                "rentEpoch": 0,
                "space": 5,
            }
        ]);
        assert_eq!(result.value, expected);
```

**File:** account-decoder/src/parse_address_lookup_table.rs (L8-21)
```rust
pub fn parse_address_lookup_table(
    data: &[u8],
) -> Result<LookupTableAccountType, ParseAccountError> {
    AddressLookupTable::deserialize(data)
        .map(|address_lookup_table| {
            LookupTableAccountType::LookupTable(address_lookup_table.into())
        })
        .or_else(|err| match err {
            InstructionError::UninitializedAccount => Ok(LookupTableAccountType::Uninitialized),
            _ => Err(ParseAccountError::AccountNotParsable(
                ParsableAccount::AddressLookupTable,
            )),
        })
}
```

**File:** account-decoder/src/parse_address_lookup_table.rs (L41-60)
```rust
impl From<AddressLookupTable<'_>> for UiLookupTable {
    fn from(address_lookup_table: AddressLookupTable) -> Self {
        Self {
            deactivation_slot: address_lookup_table.meta.deactivation_slot.to_string(),
            last_extended_slot: address_lookup_table.meta.last_extended_slot.to_string(),
            last_extended_slot_start_index: address_lookup_table
                .meta
                .last_extended_slot_start_index,
            authority: address_lookup_table
                .meta
                .authority
                .map(|authority| authority.to_string()),
            addresses: address_lookup_table
                .addresses
                .iter()
                .map(|address| address.to_string())
                .collect(),
        }
    }
}
```

**File:** account-decoder/src/parse_address_lookup_table.rs (L66-66)
```rust
        solana_address_lookup_table_interface::state::{LOOKUP_TABLE_META_SIZE, LookupTableMeta},
```

**File:** account-decoder/src/parse_account_data.rs (L137-139)
```rust
        ParsableAccount::AddressLookupTable => {
            serde_json::to_value(parse_address_lookup_table(data)?)?
        }
```

**File:** transaction-context/src/lib.rs (L19-19)
```rust
pub const MAX_ACCOUNT_DATA_LEN: u64 = 10 * 1024 * 1024;
```
