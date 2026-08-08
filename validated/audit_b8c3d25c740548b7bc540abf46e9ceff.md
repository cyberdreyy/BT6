### Title
Unbounded CPU/memory cost in `UiLookupTable::from` for jsonParsed decoding of maximal-size Address Lookup Table accounts - ([File: account-decoder/src/parse_address_lookup_table.rs])

### Summary
A single unprivileged `getAccountInfo` (or `getProgramAccounts`) call with `encoding=jsonParsed` against an Address Lookup Table (ALT) account triggers `AddressLookupTable::deserialize` followed by `UiLookupTable::from`, which iterates every stored `Pubkey` and calls `.to_string()` (base58 encoding) on each one with no size cap. An attacker can extend an ALT account they control up to `MAX_PERMITTED_DATA_LENGTH` across many prior low-cost/unprivileged transactions, then issue one RPC call that forces the validator to allocate and base58-encode hundreds of thousands of addresses, with cost scaling linearly with account size rather than any fixed RPC-imposed bound.

### Finding Description
The read path is: RPC `getAccountInfo`/`getMultipleAccounts` → `rpc/src/rpc.rs::get_encoded_account` → `encode_account` (for non-SPL-token owners, which ALT accounts are) → `solana_account_decoder::encode_ui_account` in `account-decoder/src/lib.rs:46-101`. For `UiAccountEncoding::JsonParsed` this calls `parse_account_data_v3` unconditionally on `account.data()` with **no data-length guard**, unlike the `Base58`/`Binary` path which explicitly rejects data larger than `MAX_BASE58_BYTES` in `encode_account` (`rpc/src/rpc.rs:2575-2601`).

`parse_account_data_v3` dispatches by account owner to `account_decoder::parse_address_lookup_table::parse_address_lookup_table`, which calls `AddressLookupTable::deserialize(data)` and then converts the result via `UiLookupTable::from`: [1](#0-0) 

This conversion loops over every stored address and heap-allocates a base58 `String` for each one, with no cap on `addresses.len()`: [2](#0-1) 

`encode_ui_account`'s `JsonParsed` branch calls this parser directly with the raw account bytes and no size check before attempting to decode: [3](#0-2) 

Compare this to the `Binary`/`Base58` path, which enforces an explicit `MAX_BASE58_BYTES` bound before encoding is attempted: [4](#0-3) 

An unprivileged attacker can:
1. Call `create_lookup_table` to create an ALT account they control.
2. Repeatedly call `extend_lookup_table` (each a normal, unprivileged transaction) to grow the account toward `MAX_PERMITTED_DATA_LENGTH`, yielding on the order of hundreds of thousands of `Pubkey` entries (32 bytes each).
3. Issue a single `getAccountInfo` call with `encoding=jsonParsed` against that account.

Because no cutoff exists before the `jsonParsed` decode is attempted, this single call forces the RPC-serving thread to deserialize the full account and allocate + base58-encode every address, at a cost proportional to on-chain account size rather than to any RPC-imposed constant.

### Impact Explanation
This is a "single-client CPU/latency amplification on the account-decoder read path" vulnerability. A single low-rate RPC call (well within the "one call per `CLUSTER_SLOT_TIME_TARGET / 2`" limit) can consume CPU and allocate memory proportional to an attacker-controlled, near-maximal account size (~10 MiB / 32 bytes ≈ hundreds of thousands of `String` allocations and base58 conversions), violating the "cost bounded by explicit limits, not on-chain data size" invariant for the RPC read path. This falls into the Agave bounty category of unbounded-cost RPC decoding from a single low-rate request.

### Likelihood Explanation
Feasible and fully repeatable by any unprivileged actor: creating and extending an ALT account requires only normal fee-payer transactions (rent cost scales with data size but no privileged access is required), and the final trigger is a single, unauthenticated `getAccountInfo` call. No commitment, subscription-quota, or parameter-limit guard currently intercepts the `jsonParsed` path for arbitrarily large account data (unlike the `Base58`/`Binary` path's `MAX_BASE58_BYTES` check).

### Recommendation
Add an explicit data-size cutoff before attempting `jsonParsed` decoding in `encode_ui_account` (`account-decoder/src/lib.rs`), mirroring the `MAX_BASE58_BYTES` guard used for `Base58`/`Binary` encodings, and/or bound `UiLookupTable::from` (and other decoders that iterate per-entry collections, e.g. `LoadedAddresses`) by capping the number of addresses converted per request, falling back to raw `Base64` output when the cap is exceeded.

### Proof of Concept
Rust benchmark/unit test in `account-decoder/src/parse_address_lookup_table.rs`:
```rust
#[test]
fn test_ui_lookup_table_from_scales_with_size() {
    use std::time::Instant;
    let num_addresses = 300_000; // approx max for a 10MiB ALT account
    let mut addresses = Vec::with_capacity(num_addresses);
    addresses.resize_with(num_addresses, Pubkey::new_unique);
    let lookup_table = AddressLookupTable {
        meta: LookupTableMeta::default(),
        addresses: Cow::Owned(addresses),
    };
    let start = Instant::now();
    let ui_lookup_table: UiLookupTable = lookup_table.into();
    let elapsed = start.elapsed();
    assert_eq!(ui_lookup_table.addresses.len(), num_addresses);
    // Expected failure: elapsed time/allocations scale linearly with
    // num_addresses instead of staying under a fixed bound, demonstrating
    // unbounded-cost decoding for a single jsonParsed request.
    assert!(elapsed.as_millis() < 5, "decode cost not bounded: {elapsed:?}");
}
```
Integration-level PoC: spin up a `TestValidator`, create an ALT with `create_lookup_table`, repeatedly call `extend_lookup_table` until `data.len()` approaches `MAX_PERMITTED_DATA_LENGTH`, then issue one `getAccountInfo` RPC call with `{"encoding": "jsonParsed"}` and measure wall-clock time/allocation versus a baseline small ALT account, asserting the ratio is not proportional to account size.

### Citations

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
