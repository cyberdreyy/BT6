### Title
Unchecked integer addition on attacker-influenced deposit `opaqueData` length can panic the derivation pipeline (DoS) - ([File: crates/consensus/protocol/src/deposits.rs])

### Summary
`Deposits::decode` parses the `TransactionDeposited` L1 log to build a deposit transaction for L2 derivation. While most length arithmetic in this function is deliberately hardened with `checked_add`/`saturating_add`/`saturating_mul` (e.g. lines 102-103, 107-109), three uses of the derived `opaque_content_len` value are combined with a plain, unchecked `64 + opaque_content_len as usize` addition instead of the same safe arithmetic used everywhere else in the same function. [1](#0-0) [2](#0-1) 

### Finding Description
`opaque_content_len` is read directly from bytes `[32..64)` of the log data and only bounds-checked against fitting in a `u64` (`opaque_content_len.try_into()` at line 96) — it is otherwise treated as untrusted throughout the function, which is why the surrounding code (lines 102-103, 107-109) deliberately uses `saturating_add`/`checked_add` to avoid panicking on adversarial values. However, at lines 113, 124 and 128 the same untrusted value is used in a bare `64 + opaque_content_len as usize` expression:

```rust
let Some(opaque_data) = &log.data.data.get(64..64 + opaque_content_len as usize) else { ... };
...
.get((64 + opaque_content_len) as usize..padding_end as usize)
...
&log.data.data[(64 + opaque_content_len) as usize..],
```

This is exactly the CVE-2018-10998 bug class: a length value taken from untrusted input is combined with a fixed constant using ordinary (unchecked) addition instead of the "Safe::add"-style checked arithmetic used elsewhere in the same code path, so an out-of-range value causes an arithmetic-overflow panic instead of a graceful error (the original CVE was `readMetadata` in `jp2image.cpp` performing an incorrect `Safe::add` on an attacker-controlled length, causing `SIGABRT`).

### Impact Explanation
`Deposits::decode` runs in the derivation pipeline that turns L1 `TransactionDeposited` logs into L2 deposit transactions, invoked from `StatefulAttributesBuilder` when building payload attributes for every new L1 epoch. A panic here on an untrusted length value would abort/crash the node process handling derivation, i.e. a Base node halt — matching the "node halt" impact category explicitly accepted by the validation rules.

### Likelihood Explanation
I was **not able to fully confirm exploitability** in the time available. The `opaque_content_len` field is normally produced by Solidity's ABI encoder when the L1 deposit contract emits the event, so under honest contract execution the field should match the actual trailing byte length and cannot be forged to an arbitrarily large `u64` value purely by a depositor choosing `depositTransaction` calldata — real overflow of `64 + opaque_content_len as usize` (which requires `opaque_content_len` near `u64::MAX`) does not appear directly reachable from a well-formed deposit-contract log. I could not verify (within this session) whether the caller (`StatefulAttributesBuilder` / L1 receipt fetcher) filters logs by originating contract address before calling `Deposits::decode`, or whether any other entry point feeds attacker/adversarial-fetched log bytes (e.g. from an untrusted L1 RPC) into this function without that constraint. Without that confirmation I cannot assert a concrete unprivileged trigger path meeting the "unauthorized operation" bar, only that the code pattern deviates from the safe-arithmetic convention used elsewhere in the same function.

### Recommendation
Replace the unchecked `64 + opaque_content_len as usize` expressions at lines 113, 124, and 128 with `checked_add`/`saturating_add` consistent with the rest of the function (e.g. reuse the already-computed, overflow-checked `padding_end`/`opaque_data_ceil_32` values, or add explicit `checked_add` with an error return), so malformed/adversarial length fields produce a `DepositDecodeError` instead of a panic.

### Proof of Concept
Not verified end-to-end due to inability to confirm the log-address/source filtering in the caller within this session; a background Devin session with full repository and build access would be needed to (a) confirm whether `Deposits::decode` can be reached with a log whose `opaque_content_len` field is not constrained to match actual trailing data length, and (b) construct a concrete log fixture with `opaque_content_len` near `u64::MAX` to demonstrate the arithmetic-overflow panic in `64 + opaque_content_len as usize`.

### Citations

**File:** crates/consensus/protocol/src/deposits.rs (L94-118)
```rust
        // The next 32 bytes indicate the length of the opaqueData content.
        let opaque_content_len: U256 = U256::from_be_slice(&log.data.data[32..64]);
        let opaque_content_len: u64 = opaque_content_len.try_into().map_err(|_| {
            DepositDecodeError::OpaqueContentOverflow(Bytes::copy_from_slice(
                &log.data.data[32..64],
            ))
        })?;

        let opaque_data_ceil_32: u64 =
            (opaque_content_len.saturating_add(31) / 32).saturating_mul(32);

        // Ensure that the remaining data is only zeros.
        // The padding ends at the next multiple of 32 after the opaque data.
        let Some(padding_end): Option<u64> = 64_u64.checked_add(opaque_data_ceil_32) else {
            return Err(DepositDecodeError::OpaqueDataPaddingOverflow);
        };

        // The remaining data is the opaqueData which is tightly packed and then padded to 32 bytes
        // by the EVM.
        let Some(opaque_data) = &log.data.data.get(64..64 + opaque_content_len as usize) else {
            return Err(DepositDecodeError::InvalidOpaqueDataLength {
                expected: opaque_content_len as usize,
                actual: log.data.data.len().saturating_sub(64),
            });
        };
```

**File:** crates/consensus/protocol/src/deposits.rs (L120-130)
```rust
        if !(opaque_content_len.is_multiple_of(32)
            || log
                .data
                .data
                .get((64 + opaque_content_len) as usize..padding_end as usize)
                .is_some_and(|data| data.iter().all(|&b| b == 0)))
        {
            return Err(DepositDecodeError::InvalidOpaqueDataPadding(Bytes::copy_from_slice(
                &log.data.data[(64 + opaque_content_len) as usize..],
            )));
        }
```
