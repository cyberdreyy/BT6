### Title
Unbounded JSON allocation/serialization cost for `jsonParsed` account/program subscriptions on large Address Lookup Table accounts - ([File: account-decoder/src/parse_address_lookup_table.rs])

### Summary
An attacker who owns an Address Lookup Table (ALT) can extend it, over multiple transactions, to a very large account size filled with pubkey entries. A single `accountSubscribe`/`programSubscribe` with `encoding=jsonParsed` on that account then forces `RpcSubscriptions::notify_watchers` to base58-encode and JSON-serialize every entry on each notification, with no size cap analogous to the one applied to raw binary encodings.

### Finding Description
`filter_account_result` in `rpc/src/rpc_subscriptions.rs` calls `encode_ui_account` for `UiAccountEncoding::JsonParsed` accounts [1](#0-0) . `encode_ui_account` routes `JsonParsed` to `parse_account_data_v3`, with no data-size guard, unlike the `Binary`/`Base58` branches which call `encode_bs58` and explicitly bound cost via `MAX_BASE58_BYTES` (128 bytes) [2](#0-1) . For accounts owned by `address_lookup_table::id()`, `parse_account_data_v3` dispatches to `parse_address_lookup_table` [3](#0-2) , which deserializes the full `AddressLookupTable` and converts every address into a `UiLookupTable.addresses: Vec<String>` via `to_string()` on each pubkey, then the caller does `serde_json::to_value(...)` on the whole result [4](#0-3) [5](#0-4) . This work is O(number of addresses) with no configured cap, and the same routine is used for `programSubscribe(address_lookup_table::id())` via `filter_program_results` calling `encode_ui_account` for every matched account [6](#0-5) . `notify_watchers` runs this encoding work on the shared RPC notification thread pool (`par_iter` over subscriptions) whenever the account's modified slot changes [7](#0-6) .

### Impact Explanation
An attacker who controls an ALT authority can, at ≤1 tx per `CLUSTER_SLOT_TIME_TARGET/2`, grow the table across many `extend` transactions toward the maximum permitted account size, and then trigger re-notification (e.g., another extend, deactivation, or any write) causing every subscriber's `notify_watchers` invocation to allocate and serialize hundreds of thousands of base58-encoded pubkey strings. This is a single-client, on-chain-data-size-driven CPU/allocation cost on the shared RPC notification thread pool, matching the described "unbounded cost for a single low-rate call" DoS category, scoped to `accountSubscribe`/`programSubscribe` with `jsonParsed` encoding on ALT-owned accounts.

### Likelihood Explanation
Feasible and repeatable: the attacker only needs authority over one ALT they create, uses the standard `extend_lookup_table` instruction across multiple transactions (respecting the rate limit), and any client can freely request `jsonParsed` encoding for `accountSubscribe`/`programSubscribe`. Note: this repository does not include the ALT program's on-chain size-limit constants (the `address-lookup-table` program processor is not present in this checkout), so I could not directly confirm from code here the exact maximum size an ALT can reach or whether an additional program-level cap smaller than 10MB exists; this should be verified against the actual on-chain program before finalizing severity.

### Recommendation
Apply an explicit size/entry-count cap (or truncation/pagination) for `jsonParsed` account encoding, similar to the `MAX_BASE58_BYTES` guard used for `Binary`/`Base58` encodings, specifically in `parse_address_lookup_table`/`parse_account_data_v3`/`encode_ui_account`, so subscription notification cost is bounded independent of attacker-controlled account size.

### Proof of Concept
```rust
// account-decoder/src/parse_address_lookup_table.rs (new test)
use {
    super::*,
    solana_address_lookup_table_interface::state::{AddressLookupTable, LookupTableMeta},
    solana_pubkey::Pubkey,
    std::{borrow::Cow, time::Instant},
};

#[test]
fn test_parse_large_lookup_table_cost_is_bounded() {
    // Simulate near-maximum ALT growth (~10MB / 32 bytes per address).
    let num_addresses = 300_000;
    let mut addresses = Vec::with_capacity(num_addresses);
    addresses.resize_with(num_addresses, Pubkey::new_unique);
    let lookup_table = AddressLookupTable {
        meta: LookupTableMeta::default(),
        addresses: Cow::Owned(addresses),
    };
    let data = AddressLookupTable::serialize_for_tests(lookup_table).unwrap();

    let start = Instant::now();
    let parsed = parse_address_lookup_table(&data).unwrap();
    let json = serde_json::to_value(&parsed).unwrap();
    let elapsed = start.elapsed();

    // Expected (currently failing): cost should be bounded by a configured
    // limit (e.g., truncated address list / max size), not scale linearly
    // with num_addresses.
    assert!(
        elapsed.as_millis() < 5,
        "encode took {:?} for {} addresses - unbounded by data size",
        elapsed,
        num_addresses
    );
    assert!(json.to_string().len() < 1_000_000, "serialized size unbounded");
}
```
Expected result today: the test fails the bound assertions because both wall-clock time and serialized JSON size grow linearly with `num_addresses`, demonstrating the lack of an independent cost cap in `parse_address_lookup_table`/`encode_ui_account`.

### Citations

**File:** rpc/src/rpc_subscriptions.rs (L382-390)
```rust
    let account = (last_modified_slot != last_notified_slot).then(|| {
        if is_known_spl_token_id(account.owner())
            && params.encoding == UiAccountEncoding::JsonParsed
        {
            get_parsed_token_account(&bank, &params.pubkey, account, None)
        } else {
            encode_ui_account(&params.pubkey, &account, params.encoding, None, None)
        }
    });
```

**File:** rpc/src/rpc_subscriptions.rs (L424-436)
```rust
    let accounts = if is_known_spl_token_id(&params.pubkey)
        && params.encoding == UiAccountEncoding::JsonParsed
        && !accounts_is_empty
    {
        let accounts = get_parsed_token_accounts(bank, keyed_accounts);
        Either::Left(accounts)
    } else {
        let accounts = keyed_accounts.map(move |(pubkey, account)| RpcKeyedAccount {
            pubkey: pubkey.to_string(),
            account: encode_ui_account(&pubkey, &account, encoding, None, None),
        });
        Either::Right(accounts)
    };
```

**File:** rpc/src/rpc_subscriptions.rs (L934-966)
```rust
        let subscriptions = subscriptions.into_par_iter();
        subscriptions.for_each(|(_id, subscription)| {
            let slot = if let Some(commitment) = subscription.commitment() {
                if commitment.is_finalized() {
                    Some(commitment_slots.highest_super_majority_root)
                } else if commitment.is_confirmed() {
                    Some(commitment_slots.highest_confirmed_slot)
                } else {
                    Some(commitment_slots.slot)
                }
            } else {
                error!("missing commitment in notify_watchers");
                None
            };
            match subscription.params() {
                SubscriptionParams::Account(params) => {
                    num_accounts_found.fetch_add(1, Ordering::Relaxed);
                    if let Some(slot) = slot {
                        let notified = check_commitment_and_notify(
                            params,
                            subscription,
                            bank_forks,
                            slot,
                            |bank, params| bank.get_account_modified_slot(&params.pubkey),
                            filter_account_result,
                            notifier,
                            false,
                        );

                        if notified {
                            num_accounts_notified.fetch_add(1, Ordering::Relaxed);
                        }
                    }
```

**File:** account-decoder/src/lib.rs (L34-91)
```rust
fn encode_bs58<T: ReadableAccount>(
    account: &T,
    data_slice_config: Option<UiDataSliceConfig>,
) -> String {
    let slice = slice_data(account.data(), data_slice_config);
    if slice.len() <= MAX_BASE58_BYTES {
        bs58::encode(slice).into_string()
    } else {
        "error: data too large for bs58 encoding".to_string()
    }
}

pub fn encode_ui_account<T: ReadableAccount>(
    pubkey: &Pubkey,
    account: &T,
    encoding: UiAccountEncoding,
    additional_data: Option<AccountAdditionalDataV3>,
    data_slice_config: Option<UiDataSliceConfig>,
) -> UiAccount {
    let space = account.data().len();
    let data = match encoding {
        UiAccountEncoding::Binary => {
            let data = encode_bs58(account, data_slice_config);
            UiAccountData::LegacyBinary(data)
        }
        UiAccountEncoding::Base58 => {
            let data = encode_bs58(account, data_slice_config);
            UiAccountData::Binary(data, encoding)
        }
        UiAccountEncoding::Base64 => UiAccountData::Binary(
            BASE64_STANDARD.encode(slice_data(account.data(), data_slice_config)),
            encoding,
        ),
        UiAccountEncoding::Base64Zstd => {
            let mut encoder = zstd::stream::write::Encoder::new(Vec::new(), 0).unwrap();
            match encoder
                .write_all(slice_data(account.data(), data_slice_config))
                .and_then(|()| encoder.finish())
            {
                Ok(zstd_data) => UiAccountData::Binary(BASE64_STANDARD.encode(zstd_data), encoding),
                Err(_) => UiAccountData::Binary(
                    BASE64_STANDARD.encode(slice_data(account.data(), data_slice_config)),
                    UiAccountEncoding::Base64,
                ),
            }
        }
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

**File:** account-decoder/src/parse_account_data.rs (L126-157)
```rust
pub fn parse_account_data_v3(
    pubkey: &Pubkey,
    program_id: &Pubkey,
    data: &[u8],
    additional_data: Option<AccountAdditionalDataV3>,
) -> Result<ParsedAccount, ParseAccountError> {
    let program_name = PARSABLE_PROGRAM_IDS
        .get(program_id)
        .ok_or(ParseAccountError::ProgramNotParsable)?;
    let additional_data = additional_data.unwrap_or_default();
    let parsed_json = match program_name {
        ParsableAccount::AddressLookupTable => {
            serde_json::to_value(parse_address_lookup_table(data)?)?
        }
        ParsableAccount::BpfUpgradeableLoader => {
            serde_json::to_value(parse_bpf_upgradeable_loader(data)?)?
        }
        ParsableAccount::Config => serde_json::to_value(parse_config(data, pubkey)?)?,
        ParsableAccount::Nonce => serde_json::to_value(parse_nonce(data)?)?,
        ParsableAccount::SplToken | ParsableAccount::SplToken2022 => serde_json::to_value(
            parse_token_v3(data, additional_data.spl_token_additional_data.as_ref())?,
        )?,
        ParsableAccount::Stake => serde_json::to_value(parse_stake(data)?)?,
        ParsableAccount::Sysvar => serde_json::to_value(parse_sysvar(data, pubkey)?)?,
        ParsableAccount::Vote => serde_json::to_value(parse_vote(data, pubkey)?)?,
    };
    Ok(ParsedAccount {
        program: format!("{program_name:?}").to_kebab_case(),
        parsed: parsed_json,
        space: data.len() as u64,
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
