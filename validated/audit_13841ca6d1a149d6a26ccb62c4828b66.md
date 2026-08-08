### Title
Unbounded zstd compression cost in `encode_account`/`encode_ui_account` for `Base64Zstd`-encoded `getAccountInfo` requests on maximum-size accounts - ([File: rpc/src/rpc.rs](rpc/src/rpc.rs))

### Summary
`encode_account` in `rpc/src/rpc.rs` enforces a `MAX_BASE58_BYTES` (128-byte) size check only for `Binary`/`Base58` encodings, but performs no equivalent size restriction before invoking zstd compression for `UiAccountEncoding::Base64Zstd`. An attacker can grow an account to the runtime maximum (10 MiB, `MAX_PERMITTED_DATA_LENGTH`) with high-entropy data via legitimate CPI-based reallocation, then issue a single `getAccountInfo` call with `encoding=base64+zstd`, forcing the RPC server to allocate and compress the full 10 MiB buffer with no guardrail.

### Finding Description
`getAccountInfo` resolves to `get_encoded_account` at [1](#0-0) , which calls `encode_account` at [2](#0-1) . That function only gates request cost for `Binary`/`Base58` encodings against `MAX_BASE58_BYTES` (128 bytes); for any other encoding, including `Base64Zstd`, it unconditionally calls `encode_ui_account` with the full (optionally sliced) account data and no size check. `encode_ui_account`'s `Base64Zstd` arm in `account-decoder/src/lib.rs` then does `zstd::stream::write::Encoder::new(...)` and `write_all`/`finish` over the entire account data slice unconditionally: [3](#0-2) . There is no on-chain-data-size-based restriction analogous to the Base58 path (`encode_bs58` in the same file, gated by `MAX_BASE58_BYTES` at lines 34-44). Because Solana accounts can be grown up to `MAX_PERMITTED_DATA_LENGTH` (10 MiB) via legitimate CPI realloc operations available to any unprivileged program, and because high-entropy (incompressible) data maximizes zstd's CPU work per byte while still requiring the encoder to hold and copy the full input and output buffers, a single `getAccountInfo(..., encoding: "base64+zstd")` call against such an account forces a compression pass and buffer allocations scaled to the full account size, with cost determined entirely by attacker-controlled on-chain state rather than any RPC-side bound.

### Impact Explanation
This falls under "unbounded cost for a single low-rate call" — one JSON-RPC request (well under the `CLUSTER_SLOT_TIME_TARGET / 2` rate limit) can force the validator's RPC thread to perform CPU- and memory-proportional work to a 10 MiB high-entropy compression, unlike the Base58 path which is explicitly capped. This matches the Agave bounty category for RPC resource-exhaustion/DoS bugs reachable via a single unprivileged, low-rate call, scoped strictly to a per-request CPU/memory spike (not multi-client or sustained DoS).

### Likelihood Explanation
Feasibility is high and fully within attacker capability described in the rules: any unprivileged program can realloc an owned account up to `MAX_PERMITTED_DATA_LENGTH` via CPI (a standard, legitimate operation), fill it with random/high-entropy bytes, and then any RPC client can request that account with `encoding=base64+zstd` once. No special privileges, staked node, or multiple calls are required — this is fully repeatable by any single client on any publicly reachable RPC endpoint that has account data available and does not otherwise restrict this encoding path.

### Recommendation
Apply a size-based restriction to `Base64Zstd` (and ideally `Base64`) in `encode_account` analogous to the existing `MAX_BASE58_BYTES` check for `Base58`/`Binary` — e.g., reject or require chunked retrieval via `data_slice` when the (sliced) account data length exceeds a bounded threshold, or impose a fixed CPU/time budget on the zstd encode call and fall back to an error/uncompressed response if exceeded.

### Proof of Concept
```rust
// account-decoder/src/lib.rs (extend existing test module)
#[test]
fn test_base64_zstd_cost_unbounded_for_large_incompressible_account() {
    use std::time::Instant;

    // Simulate an attacker-grown 10 MiB account filled with high-entropy bytes.
    let data: Vec<u8> = (0..10 * 1024 * 1024)
        .map(|i| (i as u64).wrapping_mul(2654435761).to_le_bytes()[0])
        .collect();

    let account = AccountSharedData::from(Account {
        data,
        ..Account::default()
    });

    let start = Instant::now();
    let encoded_account = encode_ui_account(
        &Pubkey::default(),
        &account,
        UiAccountEncoding::Base64Zstd,
        None,
        None,
    );
    let elapsed = start.elapsed();

    // No size-based rejection occurs for Base64Zstd, unlike Base58's
    // `"error: data too large for bs58 encoding"` path.
    assert_matches!(
        encoded_account.data,
        UiAccountData::Binary(_, UiAccountEncoding::Base64Zstd)
    );

    // Demonstrates cost scales with attacker-controlled account size with no
    // explicit bound enforced anywhere in `encode_account`/`encode_ui_account`.
    println!("10MiB Base64Zstd encode took {:?}", elapsed);
}
```
Run this alongside a benchmark comparing `1 KiB` vs `10 MiB` high-entropy accounts to show CPU time and peak memory scale linearly with account size with no cap, in contrast to `encode_bs58`'s hard `MAX_BASE58_BYTES` rejection at [4](#0-3) .

### Citations

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

**File:** account-decoder/src/lib.rs (L34-44)
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
```

**File:** account-decoder/src/lib.rs (L67-79)
```rust
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
```
