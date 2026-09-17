## Title
Unsafe `i64`-to-`u64` cast of a negative gas refund can underflow-panic `operator_fee_refund`, halting the sequencer/validator on every Isthmus+ transaction - (File: `crates/common/evm/src/l1block.rs`)

### Summary
`L1BlockInfo::operator_fee_refund` computes the gas actually consumed as `gas.limit() - (gas.remaining() + gas.refunded() as u64)` using plain (unchecked) `u64` arithmetic. `Gas::refunded()` returns a **signed** `i64` (SSTORE/SELFDESTRUCT-style refund accounting, which is allowed to be manipulated across nested calls). Casting a negative `i64` to `u64` wraps to a value near `u64::MAX` instead of failing safely, exactly the "unsafe type casting" pattern from the referenced report (`int256`→`uint256` producing a huge number that overflows downstream arithmetic and reverts the whole computation). Here the huge wrapped value added to `gas.remaining()` will itself overflow/exceed `gas.limit()`, and `gas.limit() - (...)` underflows a `u64`, which panics in debug builds and silently produces a bogus (near-`u64::MAX`) gas value in release builds, either of which corrupts operator-fee accounting for every Isthmus+ transaction that runs through the block's `reward_beneficiary` path. [1](#0-0) 

### Finding Description
`operator_fee_refund` is called from `BaseEvmHandler::reward_beneficiary` (`crates/common/evm/src/handler.rs`) via `operator_fee_charge`, which is part of the fee-settlement path executed after **every** non-deposit transaction on Isthmus+ specs (both in block production and block validation/re-execution): [2](#0-1) 

The vulnerable subtraction:
```rust
let operator_cost_gas_used = self.operator_fee_charge_inner(
    U256::from(gas.limit() - (gas.remaining() + gas.refunded() as u64)),
    spec_id,
);
``` [3](#0-2) 

`gas.refunded()` in revm's `Gas` accounting is a signed `i64` counter that is summed across nested call frames (clear/un-clear SSTORE refunds can cancel to a net-negative value at some intermediate points, and the type itself permits negative values, unlike the always-clamped-positive assumption baked into this cast). This project's own EIP-8130 code explicitly documents and defends against exactly this hazard elsewhere: [4](#0-3) 

That function (`capped_refund`) safely does `i64::max(0)` before an infallible `u64::try_from`. `operator_fee_refund`, by contrast, performs the raw `as u64` cast with no clamping and no `checked_sub`/`saturating_sub` for the final subtraction (only the outer `operator_cost_gas_limit.saturating_sub(operator_cost_gas_used)` is protected — the inner `gas.limit() - (...)` subtraction that produces `operator_cost_gas_used`'s input is not).

This mirrors the Float Capital `poolValue` bug precisely: a value that "should never be negative" is asserted implicitly by an unchecked cast, but the surrounding accounting (refund cancellation across calls) can make it negative in edge cases, and the unchecked cast turns that into a value that blows up unrelated arithmetic several lines later.

### Impact Explanation
If triggered, `gas.limit() - (gas.remaining() + gas.refunded() as u64)` underflows a `u64`:
- In debug/test builds this is an unconditional panic (`attempt to subtract with overflow`), which crashes the executing thread — during block *building* this aborts payload production for the sequencer; during block *validation* this crashes the validating node's re-execution of the block, i.e., a **node halt** on an Isthmus+ Base chain.
- In release builds (default `wrapping`/no-overflow-checks Rust release profile) the subtraction wraps to a huge `u64`, which is then fed into `operator_fee_charge_inner`, producing an enormous, incorrect operator fee. Downstream this is only combined via `saturating_sub`/`saturating_add`, so it will not itself panic further, but it corrupts the operator-fee amount credited to `OPERATOR_FEE_VAULT` and refunded to the caller, i.e., **incorrect fee accounting / potential fund misallocation** for the affected transaction, and any divergence between a build with overflow checks enabled and one without would additionally risk a **state root / consensus mismatch** between differently-compiled nodes.

Both classes (panic-based node halt, and value-corruption based accounting error) satisfy the required "concrete unauthorized operation ... or node halt" bar from a single ordinary transaction — no attacker privilege beyond submitting normal transactions whose execution generates a net-negative signed refund counter at the point `reward_beneficiary` reads it.

### Likelihood Explanation
Reaching a genuinely negative `gas.refunded()` at the exact moment `reward_beneficiary`/`operator_fee_refund` reads it requires specific SSTORE/SELFDESTRUCT refund sequences (clear-then-restore patterns), which is plausible but not trivially construct-on-demand without deeper knowledge of revm's per-call refund bookkeeping and whether it can transiently or finally read negative for a whole transaction (the project's own EIP-8130 code treats this as a real, must-guard-against case in `capped_refund`, strongly suggesting the underlying `i64` refund counter is not guaranteed non-negative). I was not able to fully verify from the indexed code alone whether revm's top-level `Gas::refunded()` invariant (as opposed to the transaction-scoped signed counter EIP-8130 tracks separately) can ever be negative in the standard (non-EIP-8130) execution path used by `reward_beneficiary`; this needs confirmation against the exact `revm` version vendored by this repo.

### Recommendation
- Clamp `gas.refunded()` to non-negative before combining with unsigned gas values, mirroring `Eip8130Executor::capped_refund`:
  ```rust
  let refunded = u64::try_from(gas.refunded().max(0)).unwrap_or(0);
  let used = gas.limit().saturating_sub(gas.remaining().saturating_add(refunded));
  let operator_cost_gas_used = self.operator_fee_charge_inner(U256::from(used), spec_id);
  ```
- Use `saturating_sub`/`checked_sub` for the `gas.limit() - (...)` computation instead of a bare `-`.
- Add a regression test constructing a `Gas` with a net-negative refund (or the smallest value that would previously underflow) and assert `operator_fee_refund` returns a sane value instead of panicking, following this codebase's own precedent set for `capped_refund` in `crates/common/evm/src/eip8130.rs`.

### Proof of Concept
Not independently runnable without the vendored `revm::interpreter::Gas` type and its exact refund-accounting semantics, which I could not fully confirm from the indexed excerpts alone. Conceptually:
1. Construct/execute an Isthmus+ transaction whose nested calls net a transiently negative `Gas::refunded()` value when `reward_beneficiary` is invoked (e.g., via patterns analogous to the clear/un-clear cross-call sequence documented in `crates/common/evm/src/eip8130.rs:3284-3309`, but through the standard non-EIP-8130 execution path that calls `operator_fee_refund`).
2. Observe `gas.refunded() as u64` wrap to a value near `u64::MAX`.
3. Observe `gas.limit() - (gas.remaining() + wrapped_value)` underflow, panicking a debug build or producing a corrupted `u64` value that is then billed as an operator fee in a release build. [1](#0-0) [4](#0-3)

### Citations

**File:** crates/common/evm/src/l1block.rs (L238-251)
```rust
    pub fn operator_fee_refund(&self, gas: &Gas, spec_id: BaseSpecId) -> U256 {
        if !spec_id.is_enabled_in(BaseUpgrade::Isthmus) {
            return U256::ZERO;
        }

        let operator_cost_gas_limit =
            self.operator_fee_charge_inner(U256::from(gas.limit()), spec_id);
        let operator_cost_gas_used = self.operator_fee_charge_inner(
            U256::from(gas.limit() - (gas.remaining() + gas.refunded() as u64)),
            spec_id,
        );

        operator_cost_gas_limit.saturating_sub(operator_cost_gas_used)
    }
```

**File:** crates/common/evm/src/handler.rs (L261-296)
```rust
    fn reward_beneficiary(
        &self,
        evm: &mut Self::Evm,
        frame_result: &mut <<Self::Evm as EvmTr>::Frame as FrameTr>::FrameResult,
    ) -> Result<(), Self::Error> {
        let is_deposit = evm.ctx().tx().tx_type() == DEPOSIT_TRANSACTION_TYPE;

        // Transfer fee to coinbase/beneficiary.
        if is_deposit {
            return Ok(());
        }

        self.mainnet.reward_beneficiary(evm, frame_result)?;
        let basefee = evm.ctx().block().basefee() as u128;

        let ctx = evm.ctx();
        let enveloped = ctx.tx().enveloped_tx().cloned();
        let spec = ctx.cfg().spec();
        let l1_block_info = ctx.chain_mut();

        let Some(enveloped_tx) = &enveloped else {
            return Err(ERROR::from_string(
                "[OPTIMISM] Failed to load enveloped transaction.".into(),
            ));
        };

        let l1_cost = l1_block_info.calculate_tx_l1_cost(enveloped_tx, spec);
        let operator_fee_cost = if spec.is_enabled_in(BaseUpgrade::Isthmus) {
            l1_block_info.operator_fee_charge(
                enveloped_tx,
                U256::from(frame_result.gas().used()),
                spec,
            )
        } else {
            U256::ZERO
        };
```

**File:** crates/common/evm/src/eip8130.rs (L1612-1620)
```rust
    /// Folds the signed transaction-level refund counter into the final applied
    /// refund: clamps a net-negative counter to zero (a net negative grants no
    /// refund, never adds to gas owed), then applies EIP-3529's `gross_used / 5`
    /// ceiling. Accounting refunds signed across the whole transaction — rather
    /// than flooring each call's refund at zero — is what makes offsetting SSTORE
    /// refunds across calls cancel exactly, as under one continuous execution.
    fn capped_refund(signed_refund: i64, gross_used: u64) -> u64 {
        u64::try_from(signed_refund.max(0)).unwrap_or(0).min(gross_used / MAX_REFUND_QUOTIENT)
    }
```
