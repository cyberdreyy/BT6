### Title
Unbounded CPU/memory/response-size cost for a single `getAccountInfo(jsonParsed)` call on a maximal-size Address Lookup Table account - ([File: account-decoder/src/parse_address_lookup_table.rs])

### Summary
`parse_address_lookup_table` and `UiLookupTable::from` deserialize an ALT account's raw data and eagerly convert every address entry into a base58 `String`, with no cap on the number of addresses processed. Since the JSON-parsed encoding path in the RPC layer applies no data-size gate (unlike the base58/binary path, which is bounded by `MAX_BASE58_BYTES`), a single `getAccountInfo` call with `encoding: "jsonParsed"` on an attacker-controlled, maximally-sized ALT account can force the validator to allocate and stringify up to ~327,680 `Pubkey`s and serialize the result to JSON in one RPC request.

### Finding Description
The call path is: `rpc/src/rpc.rs::get_account_info` → `get_encoded_account` → `encode_account`. `encode_account` only enforces `MAX_BASE58_BYTES` (128 bytes) when the encoding is `Binary` or `Base58`: [1](#0-0) 

For `UiAccountEncoding::JsonParsed`, `encode_ui_account` unconditionally calls `parse_account_data_v3(pubkey, account.owner(), account.data(), additional_data)` regardless of `account.data().len()`: [2](#0-1) 

When the account is owned by the Address Lookup Table program, this reaches `parse_address_lookup_table`, which deserializes the full account buffer and converts every address into a `UiLookupTable`: [3](#0-2) [4](#0-3) 

The `addresses` vector length is bounded only by the account's data size, not by any explicit RPC-side limit. An ALT account grown (via repeated `ExtendLookupTable` instructions, attacker-authored on-chain data) to the maximum permitted account size (10 MiB) can hold roughly `(10 MiB - LOOKUP_TABLE_META_SIZE) / 32 ≈ 327,680` pubkeys. A single `getAccountInfo(jsonParsed)` call on that account then triggers allocation of ~327k `String`s (`to_string()` per `Pubkey`) plus `serde_json` serialization of the resulting `UiLookupTable`, none of which is checked against `MAX_BASE58_BYTES` or any other size limit before the decode executes — that limit only guards the Base58/Binary branch.

### Impact Explanation
This falls into the "unbounded cost for a single low-rate call" category. A single, one-time `getAccountInfo` request against a pre-prepared attacker-owned account produces a large CPU/memory spike (hundreds of thousands of pubkey-to-base58 conversions plus JSON serialization) and returns an oversized JSON response, imposing load on the RPC node for every client that queries the account — with no bound comparable to `MAX_BASE58_BYTES` gating the `jsonParsed` path.

### Likelihood Explanation
Feasible and fully attacker-controlled: the attacker only needs to own an account under the ALT program's ownership, populate it with a valid meta header, and extend it over multiple (self-paid) `ExtendLookupTable` transactions up to the max account size (this account preparation is on-chain data manipulation by the unprivileged attacker, not a rate-limited RPC call). Once prepared, a single `getAccountInfo(jsonParsed)` call reliably triggers the full decode/serialize cost every time any client queries it.

### Recommendation
Apply a size or address-count gate to the JsonParsed path analogous to `MAX_BASE58_BYTES`/`MAX_MULTIPLE_ACCOUNTS`: before invoking `parse_account_data_v3`/`parse_address_lookup_table`, check `account.data().len()` (or the derived address count) against a defined maximum and fall back to raw Base64 encoding (as already done on parse failure) when exceeded.

### Proof of Concept
Rust unit/integration test plan (in `account-decoder` or `rpc` crate):
1. Construct an `AddressLookupTable` with `addresses` sized so the serialized account data is ~10 MiB (≈327,680 unique `Pubkey`s), matching `AddressLookupTable::serialize_for_tests`.
2. Call `parse_address_lookup_table(&data)` directly and measure wall-clock time and peak heap allocation (e.g., via `dhat` or simple `Instant` timing) for both the deserialize+`UiLookupTable::from` step and the subsequent `serde_json::to_string`/`to_vec` of `LookupTableAccountType`.
3. Assert that the produced JSON size and elapsed time are unbounded (i.e., scale linearly with `addresses.len()` with no truncation), while comparing against `MAX_BASE58_BYTES` (128 bytes) to show the base58/binary path would have been capped at a tiny fraction of this cost.
4. Optionally, drive this through `rpc.rs`'s `encode_account`/`get_encoded_account` with `UiAccountEncoding::JsonParsed` and an in-memory bank holding the oversized ALT account, confirming no `Result::Err` is returned before the expensive decode/serialize executes (unlike the `test_encode_account_throws_when_data_too_large_to_base58_encode` test for the Base58 path).

### Citations

**File:** rpc/src/rpc.rs (L2575-2601)
```rust
fn encode_account<T: ReadableAccount>(
    account: &T,
    pubkey: &Pubkey,
    encoding: UiAccountEncoding,
    data_slice: Option<UiDataSliceConfig>,
) -> Result<UiAccount> {
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
        Ok(encode_ui_account(
            pubkey, account, encoding, None, data_slice,
        ))
    }
}
```

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
