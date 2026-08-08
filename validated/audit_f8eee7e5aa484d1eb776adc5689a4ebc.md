### Title
Unbounded zstd compression CPU cost on `getAccountInfo` with `encoding=base64+zstd` allows attacker-controlled compute amplification - ([File: account-decoder/src/lib.rs])

### Summary
`encode_ui_account`'s `UiAccountEncoding::Base64Zstd` branch runs `zstd::stream::write::Encoder::new(Vec::new(), 0).write_all(slice_data(account.data(), data_slice_config))` directly on the full (or sliced) account data with no size cap before invoking the compressor. Since Solana accounts can be sized up to the runtime's maximum permitted account data length, a single `getAccountInfo` call with `encoding: "base64+zstd"` against a large, attacker-owned account forces the RPC node to spend CPU/memory proportional to that account's size compressing it, with no RPC-side ceiling independent of attacker-chosen data size.

### Finding Description
The relevant code is:

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
``` [1](#0-0) 

Compare this to the `Base58`/`Binary` branches, which explicitly gate on `MAX_BASE58_BYTES` (128 bytes) via `encode_bs58` and refuse to encode data larger than that limit, returning an error string instead: [2](#0-1) 

No equivalent size gate exists for the `Base64Zstd` path — `slice_data` only restricts the data if the caller supplies a `dataSlice` config in the request, and there's no requirement that a caller do so: [3](#0-2) 

An attacker who writes/owns an account up to the runtime's maximum permitted account data size (`MAX_PERMITTED_DATA_LENGTH`, referenced e.g. in `program-runtime/src/serialization.rs` and `programs/system/src/system_processor.rs`) can then call `getAccountInfo(pubkey, {encoding: "base64+zstd"})` once. This reaches `encode_ui_account`, which invokes the zstd encoder over the entire account buffer with no data-size-independent cap, causing CPU/memory work proportional to the attacker-chosen (and attacker-content-controlled, e.g. pathological low-entropy patterns to maximize compressor work) account size.

### Impact Explanation
This falls into the "unbounded cost for a single low-rate call" category: a single unprivileged `getAccountInfo` RPC request can force disproportionate CPU expenditure on the validator's JSON-RPC service, scaling with attacker-controlled account size rather than a fixed, request-cost-bounded ceiling. This matches the "RPC single-call CPU/memory amplification" bounty category rather than a full crash/DoS requiring multiple calls.

### Likelihood Explanation
Feasible and repeatable with modest attacker resources: writing a large low-entropy account on-chain is a normal, permitted on-chain action (bounded only by rent economics and the max account size), and querying it via `getAccountInfo` with `encoding=base64+zstd` is a standard, single, low-rate RPC call satisfying the ≤1 call per `CLUSTER_SLOT_TIME_TARGET/2` constraint. No special privileges, mocked paths, or multiple clients are required.

### Recommendation
Add an explicit size gate for the `Base64Zstd` branch (and ideally shared for `Base64`) before invoking the compressor/encoder, similar to `MAX_BASE58_BYTES` for `encode_bs58` — e.g., reject or fall back to an error string when `slice_data(...).len()` exceeds a fixed RPC-enforced ceiling, independent of the account's actual on-chain size, so that per-request compression cost is bounded.

### Proof of Concept
```rust
// account-decoder/src/lib.rs (test module)
#[test]
fn test_base64_zstd_cost_scales_with_account_size_unbounded() {
    use std::time::Instant;

    // Low-entropy, pathological-for-compressor-work pattern (not literally all zeros,
    // to avoid trivial RLE fast paths while still being cheap for attacker to produce).
    fn make_data(size: usize) -> Vec<u8> {
        (0..size).map(|i| (i % 251) as u8).collect()
    }

    let sizes = [1024usize, 1_000_000, 10_000_000 /* up to MAX_PERMITTED_DATA_LENGTH */];
    let mut timings = Vec::new();

    for &size in &sizes {
        let account = AccountSharedData::from(Account {
            data: make_data(size),
            ..Account::default()
        });
        let start = Instant::now();
        let _ = encode_ui_account(
            &Pubkey::default(),
            &account,
            UiAccountEncoding::Base64Zstd,
            None,
            None, // no dataSlice cap supplied by caller
        );
        timings.push(start.elapsed());
    }

    // Expected: encoding time grows monotonically and roughly proportionally with
    // account size, demonstrating there is no size-independent RPC ceiling applied
    // before the zstd::stream::write::Encoder is invoked.
    assert!(timings[2] > timings[1] && timings[1] > timings[0]);
}
```
This test demonstrates that `encode_ui_account`'s `Base64Zstd` path has no data-size-independent cost ceiling, unlike the `MAX_BASE58_BYTES`-gated `Base58`/`Binary` paths in the same file [4](#0-3) .

### Citations

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

**File:** account-decoder/src/lib.rs (L125-137)
```rust
fn slice_data(data: &[u8], data_slice_config: Option<UiDataSliceConfig>) -> &[u8] {
    if let Some(UiDataSliceConfig { offset, length }) = data_slice_config {
        if offset >= data.len() {
            &[]
        } else if length > data.len() - offset {
            &data[offset..]
        } else {
            &data[offset..offset + length]
        }
    } else {
        data
    }
}
```

**File:** account-decoder/src/lib.rs (L176-199)
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
```
