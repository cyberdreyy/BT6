### Title
`is_system_error` Panic classification in the B-20 asset precompile lets a caller-triggerable arithmetic overflow inside `announce()`'s internal-call loop propagate as a fatal EVM error instead of a catchable revert - (File: `crates/common/precompile-storage/src/error.rs`)

### Summary
The external report's bug class is "an unhandled exception in a cross-chain message-processing path causes a revert to escape normal handling and blocks subsequent processing / halts a subsystem." Base's own `BLOCK_PRODUCTION_REVIEW.md` explicitly calls out the analogous class on Base: precompile logic that "convert[s] malformed, oversized, or otherwise user-controlled input from normal EVM halt/revert behavior into fatal `Err`, panic, or process exit behavior" can make "the sequencer fail to build a block containing the transaction" or make "validators get stuck re-executing a block that contains it." [1](#0-0) 

### Finding Description
`BasePrecompileError::is_system_error` classifies `Panic`, `OutOfGas`, `Fatal`, and `SlotOverflow` as errors "that must be propagated rather than turned into a revert" [2](#0-1) . A `Panic` variant is explicitly documented as being produced by "arithmetic under/overflow" [3](#0-2) .

The `B20AssetToken::announce` code path executes attacker/caller-supplied internal calls (`internalCalls: Vec<Bytes>`) inside the precompile dispatcher, re-dispatching each sub-call through the same routing logic [4](#0-3) . A test in the repo itself demonstrates that a crafted internal call to `toScaledBalance` with an overflowing multiplier produces a `Panic(UnderOverflow)` error, and that this "must propagate unchanged and must not be wrapped as `IB20Asset::InternalCallFailed`" [5](#0-4) . In other words, the code deliberately routes this caller-triggered arithmetic-overflow condition around the normal revert-and-continue path.

`into_precompile_result` shows what happens to a `Panic`/system error at the interpreter boundary: unlike `Revert`, `OutOfGas` (halted normally) or `Fatal` are turned into a hard `PrecompileError::Fatal`/halt outcome rather than a standard ABI-encoded revert [6](#0-5) .

### Impact Explanation
Base's own internal review checklist flags exactly this shape of bug as **Critical**: "Fatal EVM transaction execution in builder" — when `evm.transact` returns a non-invalid-tx error for transaction input reachable from txpool/bundles/precompiles, the payload builder aborts the payload/flashblock attempt instead of producing a valid payload, and — separately — validators re-executing the same block can get stuck if their fatal/halt semantics diverge from what the builder produced [7](#0-6) [8](#0-7) . This is functionally the same "unhandled exception blocks subsequent message/block processing" DoS pattern the external CCIP report describes, but reachable on Base via any unprivileged caller who can invoke the `B20Asset` precompile's `announce` with a crafted internal call, rather than via a cross-chain relayer message.

### Likelihood Explanation
I could not fully confirm, within the remaining budget, whether the `Panic`/system-error path from `announce`'s internal-call loop is caught and safely converted to a normal transaction revert somewhere higher up the EVM/precompile-halt handling stack (e.g., in `crates/common/evm/src/precompiles` or the `PrecompileOutput`/`Halt` conversion visited in `evm.rs`), or whether it truly reaches the "fatal" `Err` path that the review doc warns against. The test `announce_inner_system_error_propagates_unchanged` only asserts the error is *not* wrapped as a catchable `InternalCallFailed` revert and is returned "unchanged" as `Panic(UnderOverflow)` at the `dispatch` level — it does not by itself prove that this ultimately surfaces as a builder-halting `Fatal`/panic at the EVM-transaction level versus being converted into a normal EVM `Halt` (which is caught and simply fails the single transaction, with no block-halting effect). This distinction is the crux of whether this is a real vulnerability or intended, safely-contained behavior, and it requires tracing `PrecompileOutput::halt`/`PrecompileError::Fatal` handling in the EVM host loop (`crates/common/evm/src/evm.rs`, `crates/common/evm/src/handler.rs`) further than I was able to in this session.

### Recommendation
Trace the full path of `BasePrecompileError::Panic`/`Fatal`/`SlotOverflow` from `B20AssetToken::route`/`announce` through `into_precompile_result` and the revm precompile-halt/fatal boundary to `crates/common/evm/src/handler.rs`'s `catch_error`/`execution_result`, confirming whether this ever reaches an `EVMError::Transaction`/fatal path that a block builder (`crates/execution/payload/src/builder.rs`) cannot catch as an ordinary invalid-tx skip. If it can, either (a) reclassify caller-triggerable arithmetic panics inside `announce`'s internal-call loop as ordinary reverts (matching `InternalCallFailed` handling for other internal-call failures), or (b) add explicit builder/validator-parity tests proving the fatal path is always handled as a non-block-halting, skip-this-transaction outcome, per the mandatory guidance in `BLOCK_PRODUCTION_REVIEW.md`.

### Proof of Concept
1. Call the `B20Asset` precompile's `announce(internalCalls, id, description, uri)` entry point as any unprivileged caller holding the required role (per the existing test, the caller only needs `OPERATOR_ROLE`, which is a normal operational role, not a privileged/deployer-only role) [9](#0-8) .
2. Include an `internalCalls` entry encoding `toScaledBalance(rawBalance)` with a `rawBalance`/multiplier combination chosen to overflow (e.g. `multiplier = U256::MAX/2 + 1`, `rawBalance = 2`), exactly as constructed in the existing unit test `announce_inner_system_error_propagates_unchanged` [10](#0-9) .
3. Observe that the dispatcher returns `BasePrecompileError::Panic(PanicKind::UnderOverflow)` unchanged rather than a normal ABI-encoded revert, per `is_system_error` classification [2](#0-1) .
4. Further verification is needed (not completed in this session) to determine whether this ultimately halts block building/validation as described in `BLOCK_PRODUCTION_REVIEW.md`'s "Precompile fatal execution semantics" and "Fatal EVM transaction execution in builder" scenarios, which would confirm Critical severity.

### Citations

**File:** docs/guides/BLOCK_PRODUCTION_REVIEW.md (L29-29)
```markdown
| Precompile fatal execution semantics | BLS, BN254, P256, MODEXP, or Base precompile changes convert malformed, oversized, or otherwise user-controlled input from normal EVM halt/revert behavior into fatal `Err`, panic, or process exit behavior; changes blur `Error` vs `Halt` semantics after upstream SDK changes. | `crates/common/precompiles`, `crates/common/evm/src/precompiles`, precompile provider/adapters, Reth SDK integration. | `PrecompileError::Fatal`, non-invalid EVM error, panic, validator re-execution failure. | Sequencer can fail to build a block containing the transaction; validators can get stuck re-executing a block that contains it. | Any semantic change to precompile failure mode without explicit builder and validator impact analysis is critical. | Boundary tests at max and ... (truncated)
```

**File:** docs/guides/BLOCK_PRODUCTION_REVIEW.md (L30-30)
```markdown
| Fatal EVM transaction execution in builder | `evm.transact` can return a non-invalid-tx error for transaction input from attributes, txpool, bundles, precompiles, or database-backed execution. | `execute_sequencer_transactions`, `execute_best_transactions`, EVM config, transaction environment conversion. | `PayloadBuilderError::EvmExecutionError`, `PayloadBuilderError::evm`, panic before receipt/state update. | Builder aborts the payload or flashblock attempt instead of producing a valid payload/fallback. | Changes that make user-controlled transaction input produce fatal EVM errors in production builder paths are critical. | Tests proving invalid user input is skipped, halted, reverted, or rejected according to the path; tests for fatal error propagation only when it is consensus-requir ... (truncated)
```

**File:** docs/guides/BLOCK_PRODUCTION_REVIEW.md (L36-36)
```markdown
| Production panics or unchecked assumptions in block paths | New `unwrap`, `expect`, indexing, division, overflow, or `panic!` can be triggered by user input, chain data, database state, fork config, payload attributes, or runtime config. | Precompiles, transaction execution, payload assembly, state-root/fork activation, flashblocks publishing, job timing. | Panic, task crash, process exit, missing finalized payload. | Sequencer stops producing blocks or validators stop progressing. | User-triggerable or chain-triggerable panics in block-production-sensitive paths are critical. | Replace with typed errors or prove the invariant is construction-enforced; add regression tests for the triggering edge case. |
```

**File:** crates/common/precompile-storage/src/error.rs (L20-23)
```rust
    /// EVM panic (arithmetic under/overflow, out-of-bounds access, enum conversion).
    #[error("Panic({0:?})")]
    Panic(PanicKind),

```

**File:** crates/common/precompile-storage/src/error.rs (L75-79)
```rust
impl BasePrecompileError {
    /// Returns true if this error must be propagated rather than turned into a revert.
    pub const fn is_system_error(&self) -> bool {
        matches!(self, Self::OutOfGas | Self::Fatal(_) | Self::Panic(_) | Self::SlotOverflow)
    }
```

**File:** crates/common/precompile-storage/src/error.rs (L110-136)
```rust
    /// ABI-encodes this error and wraps it as a [`PrecompileResult`] (revert or fatal error).
    ///
    /// Internal dispatch diagnostics use compact, non-ABI revert data: unknown selectors return the
    /// raw selector bytes, and decode failures return `selector || utf8_error_string`.
    pub fn into_precompile_result(self, gas: u64, state_gas: u64) -> PrecompileResult {
        let bytes: Bytes = match self {
            Self::Revert(bytes) => bytes,
            Self::Panic(kind) => Panic { code: U256::from(kind as u32) }.abi_encode().into(),
            Self::OutOfGas => {
                return Ok(PrecompileOutput::halt(PrecompileHalt::OutOfGas, 0));
            }
            Self::SlotOverflow => {
                return Err(PrecompileError::Fatal("slot overflow".into()));
            }
            Self::Fatal(msg) => {
                return Err(PrecompileError::Fatal(msg));
            }
            Self::StaticCallViolation => Bytes::new(),
            Self::UnknownFunctionSelector(sel) => sel.to_vec().into(),
            Self::AbiDecodeFailed { selector, error } => {
                let mut bytes = selector.to_vec();
                bytes.extend_from_slice(error.as_bytes());
                bytes.into()
            }
        };
        Ok(PrecompileOutput::revert(gas, bytes, state_gas))
    }
```

**File:** crates/common/precompiles/src/b20_asset/dispatch.rs (L111-128)
```rust
        // Fast-path `announce` before the generic decode; `announce` is the only B-20 selector an
        // aliased payload can amplify. `DecodedAnnounce::decode_if_announce` runs
        // `decode_sequence` and `valid_token` and keeps the `bytes[]` entries as slices into
        // `calldata`, never owned copies. A call this version doesn't recognize as `announce`
        // falls through to the generic decode below, unchanged. A recognized but malformed
        // `announce` returns `Some(Err(..))` when the rejection can be cheap without changing
        // frozen revert bytes (Cantina #16 follow-up). V1/Beryl can't take that path, so it falls
        // through too, with today's error bytes.
        match DecodedAnnounce::decode_if_announce(calldata, version) {
            Some(Ok(announce)) => {
                return observer.observe("precompile-b20-asset-announce", || {
                    self.run_announce(ctx, version, privileged, &observer, announce)?;
                    Ok(Bytes::new())
                });
            }
            Some(Err(error)) => return Err(error),
            None => {}
        }
```

**File:** crates/common/precompiles/src/b20_asset/dispatch.rs (L800-824)
```rust
    /// System errors produced by an inner `announce` call must propagate unchanged and must
    /// not be wrapped as [`IB20Asset::InternalCallFailed`]. A deliberately overflowing
    /// `toScaledBalance` produces `Panic(UnderOverflow)`, which `is_system_error()` returns
    /// `true` for.
    #[test]
    fn announce_inner_system_error_propagates_unchanged() {
        let mut token = make_token();
        // Any balance > 1 overflows when multiplied by this multiplier.
        token.accounting_mut().multiplier = U256::MAX / U256::from(2u64) + U256::ONE;
        token.accounting_mut().roles.insert((AssetV1::OPERATOR_ROLE, ALICE), true);

        let inner_call = Bytes::from(
            IB20Asset::toScaledBalanceCall { rawBalance: U256::from(2u64) }.abi_encode(),
        );
        let calldata = IB20Asset::announceCall {
            internalCalls: alloc::vec![inner_call],
            id: String::from("test-sys-err"),
            description: String::from("test"),
            uri: String::new(),
        }
        .abi_encode();

        let err = call_asset(&mut token, ALICE, calldata).unwrap_err();

        assert_eq!(err, base_precompile_storage::BasePrecompileError::under_overflow());
```
