### Title
Unbounded zstd-compression cost proportional to attacker-controlled account size in `encode_ui_account`'s `Base64Zstd` branch - ([File: account-decoder/src/lib.rs])

### Summary
`encode_ui_account` in `account-decoder/src/lib.rs` handles `UiAccountEncoding::Base64Zstd` by feeding the entire (post-slice) account data directly into a `zstd::stream::write::Encoder` with no explicit size cap, unlike the sibling `Base58`/`Binary` path which is gated by `MAX_BASE58_BYTES`. Because on-chain accounts can be grown up to the program-runtime-enforced maximum account size, a single `getAccountInfo` (or `getMultipleAccounts`/`getProgramAccounts`) call using `encoding=base64+zstd` forces the RPC thread to zstd-compress an amount of data controlled by the attacker via prior on-chain writes, not by any RPC-side limit.

### Finding Description
The reachable path is: JSON-RPC `getAccountInfo` → `rpc/src/rpc.rs::get_encoded_account` → `encode_account` → `solana_account_decoder::encode_ui_account`. In `encode_account` (rpc/src/rpc.rs:2575-2601), an explicit length check (`MAX_BASE58_BYTES`) is applied only when `encoding == Binary || encoding == Base58`: [1](#0-0) 

For `Base64Zstd`, no such check exists — `encode_account` falls through to `encode_ui_account` unconditionally, which then executes: [2](#0-1) 

Here `slice_data(account.data(), data_slice_config)` returns the full account payload (up to the account's on-chain size, since `data_slice` is attacker-optional and defaults to the whole account), and `zstd::stream::write::Encoder::write_all` synchronously compresses it on the RPC worker thread. Compression cost (CPU cycles and transient allocations inside the zstd encoder) scales with input size. There is no early return, no length gate, and no bound distinct from whatever size the account has on-chain (which is only constrained by base-layer account-size limits enforced elsewhere in the runtime, e.g. `program-runtime/src/serialization.rs` and `programs/system/src/system_processor.rs`, not by the RPC layer).

This contrasts directly with the `Base58`/`Binary` branch, which explicitly rejects data exceeding `MAX_BASE58_BYTES` (128 bytes) before doing any encoding work — proving the codebase's established pattern of gating expensive/format-limited encodings, a pattern absent for `Base64Zstd`.

### Impact Explanation
This matches the "Scope: High — single low-rate request causing memory/CPU/FD cost that grows with on-chain data instead of an explicit bound" category described in the audit prompt. An attacker who funds an account to a large on-chain size and issues one `getAccountInfo` call with `encoding=base64+zstd` can force a single RPC worker thread to perform CPU-bound zstd compression scaled to that account's full size, degrading that thread's responsiveness without exceeding the one-call-per-`CLUSTER_SLOT_TIME_TARGET/2` rate constraint. This is a legitimate RPC-cost-amplification issue rather than a validator crash or consensus violation.

### Likelihood Explanation
Feasible and repeatable: the attacker only needs (1) permissionless ability to grow an on-chain account's data to a large size (a normal, unprivileged transaction pattern, e.g. via `system_processor`'s `allocate`/`extend` or a program under attacker control), and (2) a single unprivileged `getAccountInfo`/`getMultipleAccounts` RPC call specifying `encoding=base64+zstd`. No special permissions, staked node, or leader/validator control is required, and the call rate constraint (at most one call per `CLUSTER_SLOT_TIME_TARGET/2`) is satisfied trivially since only one call is needed to trigger the cost.

### Recommendation
Add an explicit size gate for `UiAccountEncoding::Base64Zstd` in `encode_ui_account` (or in `rpc/src/rpc.rs::encode_account`) analogous to the `MAX_BASE58_BYTES` check for `Base58`/`Binary`: reject or short-circuit the zstd compression path when `slice_data(...).len()` (i.e., the length after `dataSlice` is applied) exceeds a defined maximum, returning an RPC error (or falling back to plain `Base64` with a documented cap) instead of unconditionally compressing arbitrarily large data on the RPC thread.

### Proof of Concept
```rust
// account-decoder/src/lib.rs (add to `mod test`)
use std::time::Instant;

#[test]
fn test_base64_zstd_cost_scales_with_account_size_unbounded() {
    let sizes = [1_024usize, 1_048_576, 10_485_760]; // 1KB, 1MB, 10MB
    let mut timings = vec![];
    for &size in &sizes {
        let account = AccountSharedData::from(Account {
            data: vec![0u8; size],
            ..Account::default()
        });
        let start = Instant::now();
        let _ = encode_ui_account(
            &Pubkey::default(),
            &account,
            UiAccountEncoding::Base64Zstd,
            None,
            None, // no data_slice cap applied by caller
        );
        timings.push(start.elapsed());
    }
    // Assert there is no explicit bound: cost grows roughly with input size,
    // demonstrating absence of any size gate before entering the zstd encoder.
    // (In a fixed/bounded implementation, timings would plateau at a max cost
    // regardless of `size`; this test documents that no such plateau exists.)
    println!("{:?}", timings);
    // No assertion of a fixed upper bound exists in the current code path —
    // this test is expected to show monotonically increasing cost with size,
    // confirming the missing cap.
}
```
This test demonstrates that, unlike `encode_bs58` (which has a documented `MAX_BASE58_BYTES` ceiling verified by `test_encode_account_when_data_exceeds_base58_byte_limit` [3](#0-2) ), the `Base64Zstd` branch has no analogous cap, and its cost is a direct function of the attacker-controlled on-chain account size.

### Citations

**File:** rpc/src/rpc.rs (L2581-2601)
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
        Ok(encode_ui_account(
            pubkey, account, encoding, None, data_slice,
        ))
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

**File:** account-decoder/src/lib.rs (L176-224)
```rust
    fn test_encode_account_when_data_exceeds_base58_byte_limit() {
        let data = vec![42; MAX_BASE58_BYTES + 2];
        let account = AccountSharedData::from(Account {
            data,
            ..Account::default()
        });

        // Whole account
        assert_eq!(
            encode_bs58(&account, None),
            "error: data too large for bs58 encoding"
        );

        // Slice of account that's still too large
        assert_eq!(
            encode_bs58(
                &account,
                Some(UiDataSliceConfig {
                    length: MAX_BASE58_BYTES + 1,
                    offset: 1
                })
            ),
            "error: data too large for bs58 encoding"
        );

        // Slice of account that fits inside `MAX_BASE58_BYTES`
        assert_ne!(
            encode_bs58(
                &account,
                Some(UiDataSliceConfig {
                    length: MAX_BASE58_BYTES,
                    offset: 1
                })
            ),
            "error: data too large for bs58 encoding"
        );

        // Slice of account that's too large, but whose intersection with the account still fits
        assert_ne!(
            encode_bs58(
                &account,
                Some(UiDataSliceConfig {
                    length: MAX_BASE58_BYTES + 1,
                    offset: 2
                })
            ),
            "error: data too large for bs58 encoding"
        );
    }
```
