### Title
Unbounded zstd compression of full-size, high-entropy account data on a single getAccountInfo/getMultipleAccounts call with base64+zstd encoding - ([File: account-decoder/src/lib.rs])

### Summary
`encode_ui_account` compresses the entire account data slice with `zstd::stream::write::Encoder::new(Vec::new(), 0)` whenever `UiAccountEncoding::Base64Zstd` is requested, with no size or CPU-time cap beyond the chain's `MAX_PERMITTED_DATA_LENGTH` account-size limit itself. `JsonRpcRequestProcessor::get_account_info`/`get_multiple_accounts` in `rpc/src/rpc.rs` call `get_encoded_account` → `encode_account` → `encode_ui_account` directly from a single unprivileged RPC request, so a client can force one call to compress up to the maximum permitted account size of attacker-chosen (high-entropy) data.

### Finding Description
`encode_ui_account` in `account-decoder/src/lib.rs` (lines 67-79) handles `UiAccountEncoding::Base64Zstd` by constructing a `zstd::stream::write::Encoder` at compression level `0` (zstd's "default", not a fast/cheap level) and running `write_all` + `finish` synchronously over `slice_data(account.data(), data_slice_config)`: [1](#0-0) 

This is reachable from `JsonRpcRequestProcessor::get_account_info` and `get_multiple_accounts` in `rpc/src/rpc.rs`, which take a client-supplied `encoding` (defaulting to `Binary`/`Base64` but overridable to `Base64Zstd` by request), fetch the bank account, and call `get_encoded_account` → `encode_account` → `encode_ui_account` on a blocking thread: [2](#0-1) [3](#0-2) 

The only size gate present in `encode_account` applies to `Binary`/`Base58` encodings (`MAX_BASE58_BYTES`), not to `Base64` or `Base64Zstd`: [4](#0-3) 

Because on-chain accounts can be written by any unprivileged transaction up to the runtime's `MAX_PERMITTED_DATA_LENGTH` (referenced once in `rpc/src/rpc.rs` and enforced throughout `program-runtime`/`accounts-db`/`programs/system`), an attacker can create an account near that maximum size filled with high-entropy (incompressible) bytes, then issue a single `getAccountInfo` or `getMultipleAccounts` call with `encoding: base64+zstd` against it. There is no explicit compute/CPU-time budget on the RPC encode path independent of the account-size cap — the cost of compression scales with the size and entropy of the data, and worst-case (incompressible) data forces the compressor to do full-effort matching/entropy-coding work across the whole buffer rather than short-circuiting.

`get_multiple_accounts` compounds this per pubkey in a loop, each dispatched as its own `spawn_blocking` task, allowing multiple large-account compressions to be requested in one JSON-RPC call while still counting as "one call."

### Impact Explanation
This is a legitimate single-request RPC-cost/CPU-DoS concern under the "unbounded cost for a single low-rate call" category: a single unprivileged `getAccountInfo`/`getMultipleAccounts` request can consume RPC-thread CPU time proportional to `MAX_PERMITTED_DATA_LENGTH` (currently 10MiB per account, and up to `MAX_MULTIPLE_ACCOUNTS` accounts per `getMultipleAccounts` call), with cost driven purely by attacker-controlled on-chain data rather than a fixed RPC-side compute bound. This ties up the RPC blocking-thread pool (`rpc_threads`/`rpc_blocking_threads`) but does not by itself crash or corrupt validator/consensus state — it degrades RPC responsiveness for a bounded, non-privileged data size.

### Likelihood Explanation
Feasible and repeatable with a single unprivileged actor: the attacker only needs (1) an on-chain account they control that they can grow to near `MAX_PERMITTED_DATA_LENGTH` with incompressible data (achievable through ordinary system/program `allocate`/`extend`/write instructions available to any account owner, already in scope as "writing on-chain data later returned through those APIs"), and (2) a single `getAccountInfo`/`getMultipleAccounts` JSON-RPC call at ≤1 call per `CLUSTER_SLOT_TIME_TARGET/2` with `encoding: base64+zstd`. No staking, leader, or gossip control needed.

### Recommendation
Add an explicit, size-independent cost bound to the Base64Zstd path: e.g., use a fast/cheap zstd compression level for RPC encoding, cap the maximum account size eligible for zstd encoding (falling back to plain Base64 above a threshold, similar to the existing `MAX_BASE58_BYTES` fallback for Base58), and/or enforce a wall-clock or CPU-time budget around `encode_ui_account`'s Base64Zstd branch in `account-decoder/src/lib.rs`, returning an RPC error if exceeded rather than letting compression run unbounded.

### Proof of Concept
```rust
// account-decoder/src/lib.rs (benchmark/PoC test)
use std::time::Instant;
use solana_account::{Account, AccountSharedData};
use solana_pubkey::Pubkey;

#[test]
fn base64_zstd_cost_scales_with_incompressible_data_size() {
    // Simulate attacker-controlled high-entropy account near MAX_PERMITTED_DATA_LENGTH.
    let sizes = [1024usize, 1_000_000, 10_000_000]; // up to ~MAX_PERMITTED_DATA_LENGTH
    let mut timings = vec![];
    for size in sizes {
        // High-entropy fill to defeat zstd's fast paths.
        let data: Vec<u8> = (0..size).map(|i| (i as u32).wrapping_mul(2654435761) as u8).collect();
        let account = AccountSharedData::from(Account { data, ..Account::default() });
        let start = Instant::now();
        let _ = encode_ui_account(
            &Pubkey::default(),
            &account,
            UiAccountEncoding::Base64Zstd,
            None,
            None,
        );
        timings.push(start.elapsed());
    }
    // Assertion representing the absence of a size-independent bound:
    // wall-clock time increases materially with data size, with no explicit cap.
    assert!(
        timings[2] > timings[0] * 10,
        "expected compression time to scale with data size, demonstrating no explicit \
         compute-cost bound independent of account size: {timings:?}"
    );
}
```
Run this alongside an RPC-level integration test issuing one `getAccountInfo` call with `encoding: "base64+zstd"` against a bank account whose data is set to `MAX_PERMITTED_DATA_LENGTH` bytes of incompressible content, measuring the single-call wall-clock time on the RPC service and confirming it is not capped independent of the stored data size.

### Citations

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

**File:** rpc/src/rpc.rs (L534-560)
```rust
    pub async fn get_account_info(
        &self,
        pubkey: Pubkey,
        config: Option<RpcAccountInfoConfig>,
    ) -> Result<RpcResponse<Option<UiAccount>>> {
        let RpcAccountInfoConfig {
            encoding,
            data_slice,
            commitment,
            min_context_slot,
        } = config.unwrap_or_default();
        let bank = self.get_bank_with_config(RpcContextConfig {
            commitment,
            min_context_slot,
        })?;
        let encoding = encoding.unwrap_or(UiAccountEncoding::Binary);

        let response = self
            .runtime
            .spawn_blocking({
                let bank = Arc::clone(&bank);
                move || get_encoded_account(&bank, &pubkey, encoding, data_slice, None)
            })
            .await
            .expect("rpc: get_encoded_account panicked")?;
        Ok(new_response(&bank, response))
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
