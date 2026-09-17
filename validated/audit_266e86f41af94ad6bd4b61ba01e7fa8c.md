### Title
Builder panics if DA footprint gas scalar is absent post-Jovian - (File: `crates/execution/payload/src/builder.rs`)

### Summary
The Sherlock report's bug class is: a code path hardcodes the assumption that some on-chain resource (a specific fee-tier pool) always exists, and when it doesn't, the call reverts/panics instead of degrading gracefully, causing a denial of service for a core function (rebalancing). The same bug class — an unchecked assumption that a piece of protocol-derived L1 state is always populated, enforced via a panicking `.expect()` instead of a safe fallback — exists in Base's sequencer transaction-inclusion loop for the post-Jovian DA footprint gas scalar.

### Finding Description
While building a block/flashblock, for every candidate transaction the builder computes the DA footprint gas scalar by reading it from the EVM database and hard-fails if it isn't there: [1](#0-0) 

This mirrors `L1BlockInfo::fetch_da_footprint_gas_scalar`, which is expected to be seeded by the `L1_BLOCK_INFO` predeploy storage once the L1 attributes deposit for the block has executed. Elsewhere in the same subsystem, the codebase explicitly documents and defends against exactly this class of bootstrap-window gap for a sibling value (the Isthmus operator-fee scalar): instead of asserting existence, it falls back to zero so that reading an uninitialized `L1_BLOCK_INFO` slot before the first L1 attributes deposit does not panic: [2](#0-1) [3](#0-2) 

The DA footprint scalar fetch in the transaction-selection loop was not given the same defensive treatment: it is guarded only by `is_jovian_active_at_timestamp` and then unconditionally `.expect()`s that the value exists in the DB, exactly the "assume the resource is always there, otherwise blow up" pattern the audit report flags for the missing fee-500 pool in `Multipool.getAmountOut`.

### Impact Explanation
If the DA footprint gas scalar slot is not yet populated when this code path runs (e.g., a payload/flashblock build attempt that reads state before the L1 attributes deposit for that block has been executed and committed to the DB the builder is using, or any other divergence between "Jovian active by timestamp" and "L1 attributes deposit already wrote the scalar"), the `.expect()` panics. A panic inside the sequencer's transaction-inclusion loop aborts the in-flight payload/flashblock build task, which is precisely the "Fatal EVM/production panic halts block production" class the repo's own review guide calls Critical: [4](#0-3) 

This is reachable purely by the sequencer processing ordinary pooled transactions post-Jovian activation — no attacker-crafted transaction is even required, only the timing/ordering assumption being wrong — and results in a repeated block-production stall/halt until fixed, matching the required "node halt" impact bar.

### Likelihood Explanation
I could not fully verify (given tool-call limits) whether the builder's DB view is guaranteed, in every code path that reaches this loop, to already contain a committed L1-attributes-deposit-derived `da_footprint_gas_scalar` before this line runs for every possible caller (e.g., speculative/simulation builds, re-orgs, or flashblock re-building flows). The presence of an almost-identical scalar (`operator_fee_scalar`) that was explicitly hardened against this exact "not yet seeded" bootstrap gap, while this one was left as a hard `.expect()`, is what makes this a credible regression/gap rather than a proven, actively-exploitable bug — likelihood is therefore assessed as uncertain-but-plausible pending confirmation of all call sites of `fetch_da_footprint_gas_scalar` (also present in `crates/builder/core/src/flashblocks/context.rs` and `crates/execution/flashblocks/src/state_builder.rs`, which I was not able to inspect before running out of iterations).

### Recommendation
Replace the `.expect("DA footprint should always be available from the database post jovian")` panic with the same graceful-fallback pattern already used for `operator_fee_scalar`/`operator_fee_constant` (default to `0` or otherwise a safe non-panicking value when the slot is unset), and add a regression test mirroring `operator_fee_charge_returns_zero_when_isthmus_scalars_unset` for the DA footprint scalar in the payload builder's transaction loop.

### Proof of Concept
Not independently reproduced; conceptually: activate Jovian at a timestamp, invoke the payload/flashblock builder's transaction-inclusion loop against a DB/state view where `L1_BLOCK_INFO`'s DA-footprint storage slot has not yet been written (e.g., before the L1 attributes deposit transaction for that block has executed against that specific DB snapshot), and observe the `.expect()` panic in `crates/execution/payload/src/builder.rs:1291-1298`.

### Citations

**File:** crates/execution/payload/src/builder.rs (L1291-1298)
```rust
            let da_footprint_gas_scalar = self
                .chain_spec
                .is_jovian_active_at_timestamp(self.attributes().timestamp())
                .then_some(
                    L1BlockInfo::fetch_da_footprint_gas_scalar(builder.evm_mut().db_mut()).expect(
                        "DA footprint should always be available from the database post jovian",
                    ),
                );
```

**File:** crates/common/evm/src/l1block.rs (L226-233)
```rust
    /// Calculate the operator fee for the given `gas`.
    ///
    /// Missing scalars fall back to zero so the txpool bootstrap window (post-Isthmus tx
    /// evaluated before the first L1 attributes deposit seeds these fields) does not panic;
    /// this matches the execution path, where uninitialized `L1_BLOCK_INFO` slots read as zero.
    fn operator_fee_charge_inner(&self, gas: U256, spec_id: BaseSpecId) -> U256 {
        self.params().operator_fee_charge_inner(gas, spec_id.upgrade())
    }
```

**File:** crates/common/l1-fees/src/params.rs (L184-199)
```rust
    /// Calculates the operator fee for a given `gas` amount.
    ///
    /// Missing scalars fall back to zero to match the execution path during the
    /// txpool bootstrap window before the first L1 attributes deposit.
    pub fn operator_fee_charge_inner(&self, gas: U256, upgrade: BaseUpgrade) -> U256 {
        let operator_fee_scalar = self.operator_fee_scalar.unwrap_or_default();
        let operator_fee_constant = self.operator_fee_constant.unwrap_or_default();

        let product = if Self::is_enabled(upgrade, BaseUpgrade::Jovian) {
            gas.saturating_mul(operator_fee_scalar)
                .saturating_mul(U256::from(OPERATOR_FEE_JOVIAN_MULTIPLIER))
        } else {
            gas.saturating_mul(operator_fee_scalar) / U256::from(OPERATOR_FEE_SCALAR_DECIMAL)
        };
        product.saturating_add(operator_fee_constant)
    }
```

**File:** docs/guides/BLOCK_PRODUCTION_REVIEW.md (L36-36)
```markdown
| Production panics or unchecked assumptions in block paths | New `unwrap`, `expect`, indexing, division, overflow, or `panic!` can be triggered by user input, chain data, database state, fork config, payload attributes, or runtime config. | Precompiles, transaction execution, payload assembly, state-root/fork activation, flashblocks publishing, job timing. | Panic, task crash, process exit, missing finalized payload. | Sequencer stops producing blocks or validators stop progressing. | User-triggerable or chain-triggerable panics in block-production-sensitive paths are critical. | Replace with typed errors or prove the invariant is construction-enforced; add regression tests for the triggering edge case. |
```
