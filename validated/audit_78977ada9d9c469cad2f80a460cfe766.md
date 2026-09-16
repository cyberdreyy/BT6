### Title
Division-by-zero panic in B20 asset scaled-balance math due to debug-only zero check on multiplier - ([File: crates/common/precompiles/src/b20_asset/logic/v2.rs])

### Summary
The B20 asset token's ERC-8056 "scaled balance" logic (`to_scaled_balance`, `to_raw_balance`, `total_supply_ui`, `balance_of_ui`) divides by the token's `multiplier` value without a runtime-enforced non-zero guard. The only protection against a zero multiplier is a `debug_assert!`, which is compiled out entirely in release builds, mirroring the HDF5 CVE's root cause: "incorrect protection against division by zero" (a check that exists in source but does not actually execute at runtime for the affected build).

### Finding Description
`effective_multiplier` computes the multiplier used for all scaled-balance conversions: [1](#0-0) 

Note the zero-protection here is only a `debug_assert!`, which has no effect in release/production builds — the comment "Both setters reject zero, so a stored pending is never zero" is asserted, not enforced, at runtime in production.

That multiplier is then used as a divisor with the plain `/` operator (not `checked_div`) in the scaling helpers: [2](#0-1) [3](#0-2) 

`U256`'s `Div` implementation (Ruint-backed) panics on a zero divisor, just as native integer division does. Every other arithmetic operation in this file consistently uses `checked_add`/`checked_mul().ok_or_else(BasePrecompileError::under_overflow)` to convert overflow into a typed, catchable `BasePrecompileError::Panic`, e.g.: [4](#0-3) 
but the division itself is raw and unguarded — if `multiplier` is ever zero, the divide panics as a raw Rust panic rather than surfacing as the framework's typed `Panic` precompile error.

I was unable to fully confirm, within the available index, whether the token-creation path (`b20_factory/logic/v1.rs`, `B20AssetStorage::initialize`) validates that the initial `multiplier` is non-zero before writing it to storage — `initialize()` writes `init.multiplier` directly with no visible zero-check: [5](#0-4) 
This is the key open question: if factory-side validation is missing or has a gap (e.g., prior to any hardfork/version that added the check, or via a code path not covered by the update-path guard the `debug_assert` refers to), a B20 token creator could deploy a token with a zero multiplier, or reach a zero-multiplier state through the update/cancel/rollback logic, and any subsequent call to `balanceOfUI`, `totalSupplyUI`, or `to_raw_balance` conversions (used by scaled transfer/allowance paths) would panic.

### Impact Explanation
A division-by-zero panic inside precompile-dispatched logic that is not converted into the typed `BasePrecompileError::Panic` revert path is a raw, unhandled panic during transaction execution. Depending on how deep the panic-catching boundary sits relative to this call, this can propagate as a fatal execution error rather than a deterministic revert, which — per the codebase's own block-production review checklist — is treated as critical: any user-triggerable panic in transaction execution/precompile paths can crash the executing thread, cause a sequencer to fail to build a block containing the transaction, or cause validators to diverge/stall on re-execution. [6](#0-5) 

### Likelihood Explanation
Reachability depends entirely on whether a zero multiplier can actually be produced in production. The `update_ui_multiplier` setter explicitly rejects a zero multiplier via a real (non-debug) check: [7](#0-6) 
so the everyday multiplier-update path is safe. The residual risk is the token-creation/initialization path and any other internal writer of `pending_multiplier`/`multiplier`, which I could not fully verify enforces non-zero at every call site within available context. Given the explicit reliance on a `debug_assert!` (a documented anti-pattern for security-relevant invariants) instead of a real runtime check for the "matured pending" case, likelihood should be treated as non-negligible pending confirmation of the factory-side validation.

### Recommendation
- Replace the `debug_assert!(pending != 0, ...)` in `effective_multiplier` with a real runtime check that returns a typed `BasePrecompileError` (e.g., reuse `IB20Asset::InvalidMultiplier`) instead of assuming the invariant holds in release builds.
- Audit `b20_factory/logic/v1.rs` and `B20AssetStorage::initialize` to confirm the initial `multiplier` passed via `B20AssetInit` is validated non-zero (and non-exceeding `MAX_UI_MULTIPLIER`) before being written to storage, consistent with the `update_ui_multiplier` guard.
- Replace the raw `/` operations in `to_scaled_balance`, `to_raw_balance`, and `total_supply_ui` with `checked_div(...).ok_or_else(BasePrecompileError::under_overflow)` (or a dedicated error) so that even if the invariant is ever violated, the failure is a deterministic, catchable revert rather than an unhandled panic.
- Add tests instantiating a B20 asset token with `multiplier = 0` (bypassing any factory guard, if feasible) and calling `balanceOfUI`/`totalSupplyUI` to confirm a typed revert rather than a panic.

### Proof of Concept
Conceptual (not fully confirmed against the factory validation path):
1. Deploy a B20 asset-variant token via the factory, supplying `multiplier = 0` in the creation parameters, if the factory does not reject a zero multiplier.
2. Call `balanceOfUI(account)` or `totalSupplyUI()` on the deployed token.
3. Execution reaches `effective_multiplier` (multiplier = 0, no pending update) → returns `token.accounting().multiplier()` = 0.
4. `total_supply_ui`/`scaled_balance_of` then compute `product / multiplier` i.e. `product / 0`, triggering a raw division-by-zero panic in `crates/common/precompiles/src/b20_asset/logic/v2.rs:897-968` instead of a typed revert.

### Citations

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L897-910)
```rust
    fn to_scaled_balance(&self, token: &B20AssetToken<S, A>, balance: U256) -> Result<U256> {
        let multiplier = self.effective_multiplier(token)?;
        let product =
            balance.checked_mul(multiplier).ok_or_else(BasePrecompileError::under_overflow)?;
        Ok(product / B20AssetStorage::WAD)
    }

    fn to_raw_balance(&self, token: &B20AssetToken<S, A>, balance: U256) -> Result<U256> {
        let multiplier = self.effective_multiplier(token)?;
        let product = balance
            .checked_mul(B20AssetStorage::WAD)
            .ok_or_else(BasePrecompileError::under_overflow)?;
        Ok(product / multiplier)
    }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L928-939)
```rust
    fn effective_multiplier(&self, token: &B20AssetToken<S, A>) -> Result<U256> {
        let now = token.accounting().timestamp()?;
        let effective_at = token.accounting().pending_effective_at()?;
        if effective_at != 0 && now >= U256::from(effective_at) {
            let pending = token.accounting().pending_multiplier()?;
            // Both setters reject zero, so a stored pending is never zero. Do not fall back to WAD:
            // the Solidity reference also returns the raw pending value.
            debug_assert!(pending != 0, "matured pending multiplier must be non-zero");
            return Ok(U256::from(pending));
        }
        token.accounting().multiplier()
    }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L962-968)
```rust
    fn total_supply_ui(&self, token: &B20AssetToken<S, A>) -> Result<U256> {
        let multiplier = self.effective_multiplier(token)?;
        let supply = token.accounting().total_supply()?;
        let product =
            supply.checked_mul(multiplier).ok_or_else(BasePrecompileError::under_overflow)?;
        Ok(product / B20AssetStorage::WAD)
    }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L978-982)
```rust
        let now = token.accounting().timestamp()?;
        self.ensure_operator_role(token, caller, privileged)?;
        if new_multiplier.is_zero() || new_multiplier > Self::MAX_UI_MULTIPLIER {
            return Err(BasePrecompileError::revert(IB20Asset::InvalidMultiplier {}));
        }
```

**File:** crates/common/precompiles/src/b20_asset/storage.rs (L67-75)
```rust
    /// Writes all creation-time fields atomically.
    pub fn initialize(&mut self, init: B20AssetInit) -> Result<()> {
        self.b20.name.write(init.name)?;
        self.b20.symbol.write(init.symbol)?;
        self.b20.supply_cap.write(init.supply_cap)?;
        self.asset.decimals.write(init.decimals)?;
        self.asset.multiplier.write(init.multiplier)?;
        Ok(())
    }
```

**File:** docs/guides/BLOCK_PRODUCTION_REVIEW.md (L29-36)
```markdown
| Precompile fatal execution semantics | BLS, BN254, P256, MODEXP, or Base precompile changes convert malformed, oversized, or otherwise user-controlled input from normal EVM halt/revert behavior into fatal `Err`, panic, or process exit behavior; changes blur `Error` vs `Halt` semantics after upstream SDK changes. | `crates/common/precompiles`, `crates/common/evm/src/precompiles`, precompile provider/adapters, Reth SDK integration. | `PrecompileError::Fatal`, non-invalid EVM error, panic, validator re-execution failure. | Sequencer can fail to build a block containing the transaction; validators can get stuck re-executing a block that contains it. | Any semantic change to precompile failure mode without explicit builder and validator impact analysis is critical. | Boundary tests at max and ... (truncated)
| Fatal EVM transaction execution in builder | `evm.transact` can return a non-invalid-tx error for transaction input from attributes, txpool, bundles, precompiles, or database-backed execution. | `execute_sequencer_transactions`, `execute_best_transactions`, EVM config, transaction environment conversion. | `PayloadBuilderError::EvmExecutionError`, `PayloadBuilderError::evm`, panic before receipt/state update. | Builder aborts the payload or flashblock attempt instead of producing a valid payload/fallback. | Changes that make user-controlled transaction input produce fatal EVM errors in production builder paths are critical. | Tests proving invalid user input is skipped, halted, reverted, or rejected according to the path; tests for fatal error propagation only when it is consensus-requir ... (truncated)
| Strict derived-attributes / `no_tx_pool=true` path | Derived payload attributes contain invalid transactions, blob transactions, unrecoverable transactions, deposit account load failures, or pre-execution failures. | `execute_pre_steps`, `execute_sequencer_transactions`, payload attributes, deposit nonce loading, blob transaction checks. | `BlobTransactionRejected`, `TransactionEcRecoverFailed`, `AccountLoadFailed`, `EvmExecutionError`. | EL rejects the derived payload; wrong changes can either halt sync/building or diverge from proof-executor behavior. | Do not silently skip consensus-derived invalid input unless proof-executor parity is explicitly preserved. Divergence is critical. | Tests covering `no_tx_pool=true` and `no_tx_pool=false` behavior for the same invalid input; proof-exec ... (truncated)
| State/provider/finalization failures | Missing parent block, failed `state_by_block_hash`, failed state root/trie update, failed Isthmus withdrawals root, missing parent beacon block root, invalid fork extra-data derivation, or inconsistent block number context. | Payload job creation, `build_payload`, `build_block`, `finalize_payload`, state provider and trie APIs. | `MissingParentBlock`, provider error, state-root error, `PayloadBuilderError::Other`, invalid finalized payload fields. | No valid payload can be finalized; repeated failures stop block production until state/config/fork issue is fixed. | Changes that widen these failures, drop context, retry incorrectly, or allow an invalid finalized payload are critical. | Tests for parent/state-root/fork-boundary errors where practical;  ... (truncated)
| Flashblock loop, deadline, and publish failures | Flashblock build errors, websocket serialization failures, closed payload handler channel, cancellation/deadline races, late FCU or clock skew producing zero flashblocks, or publish/finalize ordering changes. | `build_next_flashblock`, `finalize_payload`, `payload_tx`, websocket publisher, publish guard, job deadline/cancellation code. | Flashblock build returns `Err`, `failed to publish flashblock`, closed channel, missing finalized payload, missed cancellation. | Flashblocks stop, payload finalization can be skipped or delayed, or external consumers see stale/missing updates. | Changes that can stop building/publishing without finalizing a valid payload or clear fallback are critical. | Cancellation race tests or reasoning; tests for cl ... (truncated)
| Payload/data size limits across I/O boundaries | Block, payload, flashblock, batch, frame, transaction, request, response, or commit data crosses an internal or external I/O boundary with an explicit or implicit maximum size; serialized, compressed, uncompressed, encoded, JSON/RPC, websocket, channel, database, gossip, batcher/blob/calldata, conductor payload-commit, or external-service payloads can grow past the downstream limit; code lacks preflight bounds, backpressure, chunking, or deterministic rejection before production. | Payload and flashblock serialization/publishing, `payload_tx`, conductor/payload commit APIs, gossip and RPC request bodies, batch/channel/frame encoding, `max_uncompressed_block_size`, DA limits, txpool forwarding, storage and DB writes. | Oversized message rej ... (truncated)
| Resource starvation and transaction-pool exclusion | Metering data is pending for all fresh transactions, DA/gas/uncompressed/state-root-gas/execution-time limits reject most candidates, permanent rejection cache poisons valid transactions, or `mark_invalid`/`mark_rejected` changes exclude valid nonce chains. | `TxnExecutionError`, `ResourceLimits::is_tx_over_limits`, metering wait logic, `NextBestFlashblocksTxs`, rejection cache, txpool pruning. | `MeteringDataPending`, `TransactionDASizeExceeded`, `BlockDASizeExceeded`, `DAFootprintLimitExceeded`, `TransactionGasLimitExceeded`, `BlockUncompressedSizeExceeded`, `ExecutionMeteringLimitExceeded`, empty flashblocks despite valid txs. | Builder can repeatedly produce empty or underfilled flashblocks/blocks, or valid transactions can be excl ... (truncated)
| Production panics or unchecked assumptions in block paths | New `unwrap`, `expect`, indexing, division, overflow, or `panic!` can be triggered by user input, chain data, database state, fork config, payload attributes, or runtime config. | Precompiles, transaction execution, payload assembly, state-root/fork activation, flashblocks publishing, job timing. | Panic, task crash, process exit, missing finalized payload. | Sequencer stops producing blocks or validators stop progressing. | User-triggerable or chain-triggerable panics in block-production-sensitive paths are critical. | Replace with typed errors or prove the invariant is construction-enforced; add regression tests for the triggering edge case. |
```
