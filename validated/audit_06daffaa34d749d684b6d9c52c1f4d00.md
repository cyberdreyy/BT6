### Title
Unbounded zstd compression cost in `encode_ui_account`'s `Base64Zstd` branch enables cheap RPC CPU-exhaustion via `getAccountInfo` - ([File: account-decoder/src/lib.rs])

### Finding Description
`encode_ui_account` in `account-decoder/src/lib.rs` dispatches on the requested `UiAccountEncoding`. For `Binary`/`Base58` it calls `encode_bs58`, which explicitly checks `slice.len() <= MAX_BASE58_BYTES` (128 bytes) and returns an error string instead of encoding oversized data: [1](#0-0) 

For `Base64Zstd`, however, there is no equivalent size guard: the full (possibly sliced) account data is fed directly into `zstd::stream::write::Encoder::new(Vec::new(), 0).write_all(...)` regardless of size: [2](#0-1) 

Account data length is bounded by the runtime's `MAX_PERMITTED_DATA_LENGTH` (10 MiB), enforced during account creation/realloc via System/BPF loader instructions, but nothing in this path re-checks that bound against a cheaper cost budget before running zstd compression. An unprivileged attacker can create/own an account (e.g., via `SystemInstruction::CreateAccount`/`Allocate` combined with writes, or a BPF program they deploy) sized close to the 10 MiB maximum and fill it with incompressible (random) bytes, which maximizes zstd's CPU work (no early-exit from redundancy) while producing output roughly the same size as input. A single `getAccountInfo` RPC call with `encoding: "base64+zstd"` against that account then forces the validator's RPC handler to allocate and compress the entire buffer, whereas the same size request via `base58`/`binary` is rejected immediately by the `MAX_BASE58_BYTES` check with negligible cost. This creates an asymmetric cost path: cheap for the attacker (one JSON-RPC request), expensive for the server (compression of megabytes of incompressible data), and repeatable at will.

### Impact Explanation
This is a resource-exhaustion / DoS-amplification issue against the **public RPC service**, not against consensus, funds, or validator core operation. Repeated `getAccountInfo(..., encoding: base64+zstd)` calls against a maximal-size, incompressible-data account force full-size zstd compression per request with no cost/size guard, unlike the base58 path. This falls under the "RPC crashes / non-RPC remote resource exhaustion" impact category noted in scope, though the concrete effect here is CPU load-based degradation of the RPC node rather than a hard crash or panic.

### Likelihood Explanation
- Precondition is trivial: any unprivileged actor can create and fund an account they own and grow it up to `MAX_PERMITTED_DATA_LENGTH` with random content.
- The exploit itself is a single, unauthenticated `getAccountInfo` RPC call — no special permissions, no transaction is needed, and it can be repeated cheaply and rapidly against any RPC endpoint that exposes this account.
- The bug is fully attacker-reachable through a standard, documented RPC encoding option (`base64+zstd`), requiring no dependency bug or misconfiguration — it's a pure logic gap comparing the `Base58`/`Binary` and `Base64Zstd` branches in `encode_ui_account`.
- Feasibility is high; the asymmetry (guard present for one encoding, absent for the other, on the same underlying account-data-length input) is directly visible in the code.

### Recommendation
Add a size/cost guard to the `Base64Zstd` branch of `encode_ui_account`, mirroring the intent of `MAX_BASE58_BYTES`: e.g., cap the pre-compression slice length that will be passed to the zstd encoder (returning an encoding error or falling back to raw/base64 for oversized data), or impose a per-request compression cost/time budget in the RPC layer (`rpc/src/rpc.rs`) before invoking `encode_ui_account`, consistent with existing RPC rate/cost limiting patterns.

### Proof of Concept
```rust
// account-decoder/src/lib.rs (added to #[cfg(test)] mod test)
use std::time::Instant;

#[test]
fn base64_zstd_has_no_size_guard_unlike_base58() {
    let max_data_len = 10 * 1024 * 1024; // ~ MAX_PERMITTED_DATA_LENGTH
    let mut data = vec![0u8; max_data_len];
    // Fill with incompressible pseudo-random bytes.
    for (i, b) in data.iter_mut().enumerate() {
        *b = (i as u32).wrapping_mul(2654435761).to_le_bytes()[0];
    }
    let account = AccountSharedData::from(Account {
        data: data.clone(),
        ..Account::default()
    });

    // Base58/Binary path: rejected instantly, no encoding work performed.
    let bs58_result = encode_bs58(&account, None);
    assert_eq!(bs58_result, "error: data too large for bs58 encoding");

    // Base64Zstd path: no guard -- full compression is performed.
    let start = Instant::now();
    let encoded = encode_ui_account(
        &Pubkey::default(),
        &account,
        UiAccountEncoding::Base64Zstd,
        None,
        None,
    );
    let elapsed = start.elapsed();
    // Compression actually ran on the full 10MB incompressible buffer.
    assert_matches!(encoded.data, UiAccountData::Binary(_, UiAccountEncoding::Base64Zstd));
    println!("zstd compression of {} bytes took {:?}", max_data_len, elapsed);
    // elapsed is nontrivial (multi-millisecond+ CPU work) vs. the ~0-cost
    // rejection on the base58 path, demonstrating the asymmetric cost.
}
```
Expected result: the `Base58` path rejects the oversized data essentially for free, while the `Base64Zstd` path performs full zstd compression over the entire incompressible buffer with measurable, non-trivial CPU time — and this can be triggered repeatedly via `getAccountInfo` with `"encoding": "base64+zstd"` against the attacker's own maximal-size account.

### Citations

**File:** account-decoder/src/lib.rs (L32-44)
```rust
pub const MAX_BASE58_BYTES: usize = 128;

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
