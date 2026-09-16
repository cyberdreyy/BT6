### Title
Unchecked gas-used subtraction in operator fee refund can underflow and corrupt fee accounting - (File: `crates/common/evm/src/l1block.rs`)

### Summary
`L1BlockInfo::operator_fee_refund` computes the gas actually used with a raw, non-checked `u64` subtraction, unlike every other fee-accounting path in the codebase (`eip8130.rs`, `handler.rs`) which uses `checked_sub`/`saturating_sub` specifically to guard against exactly this class of invariant violation. This mirrors the reported bug class: an arithmetic operation that assumes a monotonic relationship between two independently-derived values (here, `gas.remaining() + gas.refunded()` vs `gas.limit()`, analogous to OUSD's `totalAssets()` vs `_assetsHandledByArchimedes`) without defensive checked arithmetic.

### Finding Description
In `operator_fee_refund`: [1](#0-0) 

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

`gas.limit() - (gas.remaining() + gas.refunded() as u64)` is a plain subtraction. It relies on the invariant `remaining + refunded <= limit`, which normally holds because EIP-3529 caps `refunded` at `gas_used / 5` (`gas_used = limit - remaining`). Everywhere else this codebase treats such invariants as untrusted and uses checked arithmetic with explicit error paths, e.g. in the EIP-8130 fee settlement: [2](#0-1) 

and in the caller-fee deduction path: [3](#0-2) 

`operator_fee_refund` is invoked from `reimburse_caller`, on the hot path for every non-deposit, post-Isthmus transaction: [4](#0-3) 

If `gas.refunded()` and `gas.remaining()` are ever computed by a code path (e.g. the EIP-8130 signed, transaction-level refund accounting in `eip8130.rs`, which is a novel/non-standard refund-tracking mechanism layered on top of `revm`'s `Gas`) such that their sum exceeds `gas.limit()` for the `Gas` object passed into `reimburse_caller`, this subtraction underflows. Depending on build configuration this either panics (halting block execution for every node processing that block — a consensus-critical DoS) or silently wraps to a near-`u64::MAX` value, which is then fed into `operator_fee_charge_inner`'s `saturating_mul`, producing a corrupted, wildly incorrect operator fee refund.

### Impact Explanation
If reachable, this is a **node halt / chain split** class issue: a panic here during block execution would crash every node executing the affected transaction, and if only some node builds have overflow checks enabled while others don't, execution could diverge (some nodes panic/halt, others silently compute a corrupted fee), producing a consensus split. Even without a panic, wraparound corrupts operator-fee vault crediting, which is an unbacked/incorrect balance change.

### Likelihood Explanation
Likelihood is **uncertain and not fully confirmed**: under the standard `revm` gas-accounting path the EIP-3529 cap should keep `remaining + refunded <= limit`, so this would not trigger for ordinary transactions. The residual risk is the EIP-8130 executor's custom signed, cross-call refund accounting (`CallsResult::refund`, `capped_refund`) which computes and caps refunds independently before folding them into `billable_gas`/`settle_fees` — I was not able to fully trace, within the remaining investigation budget, whether the `Gas` object eventually passed to `reimburse_caller`/`operator_fee_refund` for EIP-8130 transactions is guaranteed to satisfy the same invariant, or whether a mismatch between the two refund-accounting schemes could violate it. This should be verified against the actual `Gas` construction used for the EIP-8130 dispatch path before being treated as confirmed exploitable.

### Recommendation
Replace the raw subtraction in `operator_fee_refund` with checked arithmetic (`checked_sub`, returning/propagating an explicit error, or `saturating_sub` consistent with the rest of the fee-accounting code) so that a violated invariant produces a defined, safe error instead of a panic or silent wraparound. Additionally, audit whether the EIP-8130 refund-accounting path can ever produce a `Gas` value where `remaining + refunded > limit`.

### Proof of Concept
Not independently reproduced; this is a code-level defect (missing checked arithmetic on an invariant-dependent subtraction) analogous in structure to the reported OUSD `_takeRebaseFees` underflow. A concrete PoC would require constructing an EIP-8130 (or standard) transaction whose refund/remaining accounting, as reflected in the `Gas` object passed to `reimburse_caller`, causes `gas.remaining() + gas.refunded() > gas.limit()` — which requires deeper tracing of the EIP-8130 gas-tracking machinery than could be completed in this session.

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

**File:** crates/common/evm/src/eip8130.rs (L1569-1585)
```rust
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

**File:** crates/common/evm/src/handler.rs (L159-173)
```rust
        if !cfg.is_fee_charge_disabled() {
            let Some(additional_cost) = chain.tx_cost_with_tx(tx, spec) else {
                return Err(ERROR::from_string(
                    "[OPTIMISM] Failed to load enveloped transaction.".into(),
                ));
            };
            let Some(new_balance) = balance.checked_sub(additional_cost) else {
                return Err(InvalidTransaction::LackOfFundForMaxFee {
                    fee: Box::new(additional_cost),
                    balance: Box::new(balance),
                }
                .into());
            };
            balance = new_balance
        }
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
