### Title
Uncapped/unchecked gas-refund subtraction in `operator_fee_refund` can underflow — ([File: crates/common/evm/src/l1block.rs])

### Summary
`L1BlockInfo::operator_fee_refund` computes gas actually used with a raw (non-`checked_`/non-`saturating_`) `u64` subtraction, `gas.limit() - (gas.remaining() + gas.refunded() as u64)`, and is invoked from `reimburse_caller` before the transaction's gas refund counter has been EIP-3529 capped. This is the same bug class as the reported `VaultTokenized::onSlash` issue: an intermediate "excess" quantity is folded into a subtraction without first validating that the subtrahend cannot exceed the minuend, risking underflow.

### Finding Description
`operator_fee_refund` is defined as: [1](#0-0) 

It subtracts `gas.remaining() + gas.refunded() as u64` from `gas.limit()` using plain `-`, unlike virtually every other fee/accounting computation in this codebase, which uses `checked_sub`/`saturating_sub` explicitly to guard against underflow (see the disciplined pattern in `settle_fees` and `prepay` in the EIP-8130 executor): [2](#0-1) 

`operator_fee_refund` is called from the standard (non-EIP-8130) transaction post-execution hook `reimburse_caller`, whose position in the `Handler` trait's call order is *before* `refund`, the step that applies the EIP-3529 `gross_used / 5` cap to the transaction's refund counter (analogous to `capped_refund` in the EIP-8130 path): [3](#0-2) [4](#0-3) 

Because `gas.refunded()` at the point `reimburse_caller` runs has not yet been capped to `gross_used / 5`, and `gas.refunded()` returns a signed `i64` refund counter that is cast directly to `u64`, `gas.remaining() + gas.refunded() as u64` can exceed `gas.limit()` under crafted SSTORE-refund-heavy call sequences (or if the signed counter is ever negative at this point, the `as u64` cast produces an enormous positive value). The plain subtraction then underflows — panicking in debug builds and silently wrapping to a near-`u64::MAX` value in release builds, which is subsequently fed into `operator_fee_charge_inner`.

### Impact Explanation
This is on the Isthmus-onward fee-accounting path executed for **every included non-deposit transaction** (`reward_beneficiary`/`reimburse_caller` in `crates/common/evm/src/handler.rs`), reachable by any unprivileged EOA sender who can craft a transaction with a large uncapped SSTORE/refund pattern. A wrapped value corrupts the operator fee refund and, transitively, the operator-fee-vault credit computed in `reward_beneficiary`, i.e. incorrect (potentially wildly inflated) fee accounting — a consensus-relevant state divergence if it triggers non-deterministically between debug/release builds, or an unbacked/incorrect balance credit if it silently wraps. A debug-build panic on this path would also halt block execution for affected nodes.

### Likelihood Explanation
This requires confirming precisely how large the *uncapped* refund counter (`gas.refunded()`) can be relative to `gas.remaining()`/`gas.limit()` at the moment `reimburse_caller` runs, and revm's exact `Gas::refunded()` semantics (this type is defined in the external `revm` dependency, not in this repository, so I could not fully verify the pre-cap value range from the indexed files). This uncertainty means the likelihood cannot be conclusively rated without further investigation of the revm `Gas` implementation and the ordering guarantees of `Handler::reimburse_caller` vs `Handler::refund`.

### Recommendation
Replace the raw subtraction in `operator_fee_refund` with `checked_sub`/`saturating_sub`, consistent with the rest of the codebase's checked-arithmetic discipline (as already done in `settle_fees`/`prepay`), and confirm/document whether `gas.refunded()` is guaranteed capped before this call; if not, apply the EIP-3529 cap prior to using it here.

### Proof of Concept
Not independently reproduced — reproducing this requires exercising `revm`'s `Gas::refunded()` accumulation (external crate) with a transaction whose net signed refund counter, prior to EIP-3529 capping, exceeds `gas.limit() - gas.remaining()` at the point `reimburse_caller` executes, then observing the underflow/wraparound in `operator_fee_refund`.

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

**File:** crates/common/evm/src/eip8130.rs (L1563-1585)
```rust
        let fee = U256::from(billable_gas)
            .checked_mul(U256::from(outcome.effective))
            .ok_or_else(|| BaseTransactionError::eip8130("EIP-8130 fee overflow"))?;
        let base_fee_amount = U256::from(billable_gas)
            .checked_mul(U256::from(outcome.base_fee))
            .ok_or_else(|| BaseTransactionError::eip8130("EIP-8130 base-fee amount overflow"))?;
        let priority_amount = fee
            .checked_sub(base_fee_amount)
            .ok_or_else(|| BaseTransactionError::eip8130("EIP-8130 priority amount underflow"))?;
        let l1_cost = ctx.chain_mut().calculate_tx_l1_cost(encoded, spec);
        let operator_cost =
            ctx.chain().operator_fee_charge(encoded, U256::from(billable_gas), spec);

        // Refund the surplus of the worst-case pre-charge. The pre-charge bounds
        // every component above (full `gas_limit`/operator at the effective
        // price), so the subtraction never underflows.
        let total_cost = fee
            .checked_add(l1_cost)
            .and_then(|v| v.checked_add(operator_cost))
            .ok_or_else(|| BaseTransactionError::eip8130("EIP-8130 settled cost overflow"))?;
        let refund_amount = prepay.checked_sub(total_cost).ok_or_else(|| {
            BaseTransactionError::eip8130("settled fee exceeds the worst-case pre-charge")
        })?;
```

**File:** crates/common/evm/src/handler.rs (L229-244)
```rust
    fn reimburse_caller(
        &self,
        evm: &mut Self::Evm,
        frame_result: &mut <<Self::Evm as EvmTr>::Frame as FrameTr>::FrameResult,
    ) -> Result<(), Self::Error> {
        let mut additional_refund = U256::ZERO;

        if evm.ctx().tx().tx_type() != DEPOSIT_TRANSACTION_TYPE
            && !evm.ctx().cfg().is_fee_charge_disabled()
        {
            let spec = evm.ctx().cfg().spec();
            additional_refund = evm.ctx().chain().operator_fee_refund(frame_result.gas(), spec);
        }

        reimburse_caller(evm.ctx(), frame_result.gas(), additional_refund).map_err(From::from)
    }
```

**File:** crates/common/evm/src/handler.rs (L246-259)
```rust
    fn refund(
        &self,
        evm: &mut Self::Evm,
        exec_result: &mut <<Self::Evm as EvmTr>::Frame as FrameTr>::FrameResult,
        eip7702_refund: i64,
    ) -> Result<(), Self::Error> {
        // Base: this used to be customized for pre-Regolith deposits, but since
        // Base has always had regolith active, this now matches the revm source
        // exactly.

        post_execution::refund(evm.ctx().cfg().gas_params(), exec_result.gas_mut(), eip7702_refund);

        Ok(())
    }
```
