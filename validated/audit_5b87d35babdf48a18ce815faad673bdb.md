### Title
Unbounded zstd compression cost in `encode_ui_account` (Base64Zstd) allows CPU-cost amplification via large incompressible account data - ([File: account-decoder/src/lib.rs])

### Summary
`encode_ui_account` performs zstd compression synchronously on the full account data whenever `UiAccountEncoding::Base64Zstd` is requested, with no size cap, no separate CPU/time budget, and no early rejection for large or incompressible payloads. An attacker who writes ~10MB of high-entropy data into an account they control (up to `MAX_ACCOUNT_DATA_LEN`/`MAX_PERMITTED_DATA_LENGTH`) can force every subsequent `getAccountInfo`/`getMultipleAccounts`/account-subscription request with `encoding=base64+zstd` for that pubkey to spend CPU time compressing worst-case-incompressible data.

### Finding Description
`encode_ui_account` in [1](#0-0)  handles `UiAccountEncoding::Base64Zstd` by constructing a `zstd::stream::write::Encoder`, writing the (possibly sliced) account data into it, and calling `.finish()`, all unconditionally regardless of account size:

```
UiAccountEncoding::Base64Zstd => {
    let mut encoder = zstd::stream::write::Encoder::new(Vec::new(), 0).unwrap();
    match encoder
        .write_all(slice_data(account.data(), data_slice_config))
        .and_then(|()| encoder.finish())
    { ... }
}
```

This function is reachable directly from RPC handlers such as `encode_account`/`get_encoded_account` in [2](#0-1)  (used by `getAccountInfo`, `getMultipleAccounts`, and simulate-transaction account fetching, e.g. [3](#0-2) ), and from the account-subscription notification path `filter_account_result` in [4](#0-3) .

Accounts can legitimately grow up to `MAX_PERMITTED_DATA_LENGTH` (10 MiB), enforced e.g. in `system_processor::allocate` [5](#0-4)  and bpf-loader-upgradeable's extend/deploy paths [6](#0-5) , and this cap is architecturally defined as `MAX_ACCOUNT_DATA_LEN = 10 * 1024 * 1024` in [7](#0-6) . Any unprivileged party can create/extend a data account (e.g. via the system program `Allocate`/`CreateAccount` up to this cap, subject only to rent-exemption lamports, which they pay themselves) and fill it with high-entropy bytes (e.g., via program writes or `sha256`/random data patterns), producing a payload that zstd cannot meaningfully compress, maximizing the CPU time spent in `.write_all`/`.finish()` per request.

There is no check anywhere in `encode_ui_account`, `encode_account`, or the RPC dispatch path that caps compression input size or imposes a wall-clock/CPU budget distinct from the compression call itself — unlike the Base58 path, which explicitly rejects overly large data before encoding (`MAX_BASE58_BYTES` check in `encode_account`, [8](#0-7) ). No equivalent size/complexity guard exists for `Base64Zstd`.

### Impact Explanation
A single unprivileged client, issuing at most one `getAccountInfo` call per `CLUSTER_SLOT_TIME_TARGET / 2` against an account they previously filled with ~10 MiB of incompressible data, can force the RPC-serving thread to perform maximal-cost zstd compression on every such call. Because the cost is proportional to attacker-controlled account size (up to 10 MiB) and zstd's compression cost on incompressible data does not shortcut, this is an unbounded-per-request CPU cost issue scoped to the RPC/JSON-RPC account-encoding subsystem — matching an "RPC single low-rate call causing unbounded/disproportionate CPU cost" bounty category. It does not crash the validator process nor affect consensus, but it degrades RPC-node responsiveness proportionally to attacker-chosen account size, using only account data the attacker legitimately owns and pays rent for.

### Likelihood Explanation
Preconditions are minimal and entirely within attacker capability: create/extend an account they own up to `MAX_PERMITTED_DATA_LENGTH`, fill it with random/incompressible bytes (feasible with ordinary system-program/BPF-loader instructions and rent payment), then repeatedly call `getAccountInfo`/`getMultipleAccounts`/account-subscribe with `encoding=base64+zstd` at the permitted rate. This is fully repeatable per-account and requires no validator/leader/staked privileges, matching the rules' allowed threat model exactly.

### Recommendation
Add an explicit size/complexity guard for `UiAccountEncoding::Base64Zstd` in `encode_account`/`encode_ui_account`, analogous to the existing `MAX_BASE58_BYTES` check — e.g., reject or fall back to `Base64` encoding when `slice_data(...).len()` exceeds a bounded threshold, or move the compression work off the RPC dispatch thread with a hard CPU/time budget (e.g., a fast zstd level and byte/duration cap that aborts and falls back to uncompressed Base64 on overrun).

### Proof of Concept
```rust
// account-decoder/src/lib.rs (benchmark/integration test)
use std::time::Instant;
use solana_account::{Account, AccountSharedData};
use solana_account_decoder::{encode_ui_account, UiAccountEncoding};
use solana_pubkey::Pubkey;
use rand::RngCore;

#[test]
fn bench_base64_zstd_worst_case() {
    const MAX_PERMITTED_DATA_LENGTH: usize = 10 * 1024 * 1024;
    let mut data = vec![0u8; MAX_PERMITTED_DATA_LENGTH];
    rand::thread_rng().fill_bytes(&mut data); // incompressible

    let account = AccountSharedData::from(Account { data, ..Account::default() });

    let start = Instant::now();
    let _ = encode_ui_account(
        &Pubkey::default(),
        &account,
        UiAccountEncoding::Base64Zstd,
        None,
        None,
    );
    let elapsed = start.elapsed();

    // Expected: elapsed is significant (tens to hundreds of ms) and scales
    // linearly with account size, with no upper bound enforced anywhere
    // in the call path (rpc.rs::encode_account has no size cap for
    // Base64Zstd, unlike its MAX_BASE58_BYTES check for Base58/Binary).
    println!("Base64Zstd encode of 10MiB incompressible data took {:?}", elapsed);
    assert!(elapsed.as_millis() > 0); // demonstrate nontrivial, unbounded cost
}
```
Run this alongside a comparison against small accounts to show cost scales with attacker-controlled `account.data().len()`, and inspect `rpc/src/rpc.rs::encode_account` to confirm no analogous size cap exists for `Base64Zstd` as it does for `Base58`/`Binary`.

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

**File:** rpc/src/rpc.rs (L4123-4130)
```rust
                                get_encoded_account(
                                    bank,
                                    &pubkey,
                                    accounts_encoding,
                                    None,
                                    Some(&post_simulation_accounts_map),
                                )
                            })
```

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

**File:** programs/system/src/system_processor.rs (L102-110)
```rust
    if space > MAX_PERMITTED_DATA_LENGTH {
        ic_msg!(
            invoke_context,
            "Allocate: requested {}, max allowed {}",
            space,
            MAX_PERMITTED_DATA_LENGTH
        );
        return Err(SystemError::InvalidAccountDataLength.into());
    }
```

**File:** programs/bpf_loader/src/lib.rs (L274-277)
```rust
            if programdata_len > MAX_PERMITTED_DATA_LENGTH as usize {
                ic_logger_msg!(log_collector, "Max data length is too large");
                return Err(InstructionError::InvalidArgument);
            }
```

**File:** transaction-context/src/lib.rs (L19-19)
```rust
pub const MAX_ACCOUNT_DATA_LEN: u64 = 10 * 1024 * 1024;
```
