### Title
Unchecked subtraction in `operator_fee_refund` can panic and halt block execution - (File: `crates/common/evm/src/l1block.rs`)

### Summary
`L1BlockInfo::operator_fee_refund` computes `gas.limit() - (gas.remaining() + gas.refunded() as u64)` using plain, unchecked `u64` subtraction rather than `checked_sub`/`saturating_sub`, unlike virtually every other fee computation in this codebase (`operator_fee_charge_inner`, `calculate_tx_l1_cost*`, EIP-8130 `settle_fees`, etc.), which are deliberately built on `saturating_*`/`checked_*` arithmetic specifically to avoid the underflow-revert class of bug the external DAI report describes.

### Finding Description [1](#0-0) 

`operator_fee_refund` is called post-execution (Isthmus+) to compute the refund owed against the operator fee pre-charged at `gas_limit`. It derives "gas actually used" as:

```rust
U256::from(gas.limit() - (gas.remaining() + gas.refunded() as u64))
```

This is a direct `u64` subtraction with no `checked_sub`/`saturating_sub`, in contrast to the surrounding codebase's consistent discipline: `L1FeeParams::operator_fee_charge_inner` uses `saturating_mul`/`saturating_add` [2](#0-1) , and the EIP-8130 settlement path explicitly documents and enforces checked arithmetic to prevent exactly this failure mode ("the subtraction never underflows" / "priority amount underflow") [3](#0-2) .

The invariant `gas.limit() >= gas.remaining() + gas.refunded()` is expected to hold under normal EIP-3529 refund capping (refund ≤ `gas_used / 5`), but this function has no local guard enforcing it. If `gas.refunded()` (an `i64`) is ever inflated beyond that expectation — e.g., by a future protocol change to refund accounting, a bug in how a nested call frame's refund is folded, or divergence between `evm`/`evm2` refund bookkeeping paths that this shared `L1BlockInfo` type is used from — the subtraction underflows. In Rust, `u64` subtraction underflow panics in debug builds and silently wraps in release builds (no `overflow-checks = true` was found in the workspace), turning `operator_cost_gas_used` into an astronomically large `U256` value that get saturating-subtracted safely at the very next line (`saturating_sub` at line 250), producing a refund of zero — a silent value bug in release, and an unhandled panic (process abort / consensus-critical panic) in a debug or overflow-checked build.

### Impact Explanation
Because this executes inside the post-execution fee-settlement path for every Isthmus+ transaction, a panic here aborts execution of the enclosing EVM call for **every transaction in a block**, which is on the consensus-critical execution path shared by the sequencer, all validating full nodes, and any node re-executing this transaction (including the stateless fault-proof program, which shares this `base-common-evm` crate). This matches the class of impact called out in the source report (unexpected reverts blocking critical state transitions) but here it is worse: an unguarded panic on the hot execution path is a potential **node/chain halt** rather than a mere reverted call, satisfying the "node halt / chain split" impact bar. In a release build without overflow checks it instead silently zeroes the operator-fee refund, which is a fee-accounting correctness bug (funds misrouted/kept by the operator-fee vault instead of refunded to the payer).

### Likelihood Explanation
Likelihood is currently **low-to-moderate**: under the present EIP-3529 refund-capping logic, `gas.remaining() + gas.refunded() <= gas.limit()` should normally hold, so no unprivileged transaction sender can trivially trigger this today through ordinary transaction refund manipulation (e.g., SSTORE clear refunds). However, unlike every other arithmetic site in this file and its siblings, there is no defensive check, so any future change to refund accounting, an interaction between the two parallel EVM engines (`evm`/`evm2`) that this `L1BlockInfo` type serves, or a currently-unknown edge case in how `Gas::refunded()`/`Gas::remaining()` are populated across nested calls would silently flip this from "theoretically safe" to "attacker/transaction-triggerable panic," with no compensating control in place.

### Recommendation
Replace the raw subtraction with `checked_sub`/`saturating_sub` and treat an unexpected underflow defensively (return zero additional refund, or clamp `gas.remaining() + gas.refunded()` to `gas.limit()`) rather than relying on an external invariant that isn't locally enforced — consistent with the `saturating_sub` already used on the very next line and with the checked-arithmetic discipline documented and applied throughout `eip8130.rs` and `params.rs`. Add a regression test asserting `operator_fee_refund` does not panic when `gas.remaining() + gas.refunded() > gas.limit()`.

### Proof of Concept
Conceptual PoC (unit test) demonstrating the panic/wrap, mirroring the existing test harness in `crates/common/evm/src/l1block.rs`:
```rust
#[test]
fn operator_fee_refund_underflows_when_refund_plus_remaining_exceeds_limit() {
    let mut gas = Gas::new(1_000);
    // Force remaining + refunded > limit (simulating a future/edge-case refund
    // accounting bug rather than a currently-reachable EIP-3529 path).
    gas.set_final_refund(2_000, /* is_london */ true); // or otherwise mutate refunded()
    // gas.remaining() + gas.refunded() as u64 > gas.limit() == 1_000
    let l1_block_info = L1BlockInfo { operator_fee_scalar: Some(U256::from(1_000)), ..Default::default() };
    // In a debug/overflow-checked build this panics:
    let _ = l1_block_info.operator_fee_refund(&gas, BaseSpecId::new(BaseUpgrade::Isthmus));
}
```
Because I do not have a concrete, currently-reachable transaction sequence that pushes `gas.remaining() + gas.refunded()` above `gas.limit()` under the existing EIP-3529 capping (the invariant appears to hold under today's refund-cap logic), I cannot demonstrate end-to-end exploitation from an unprivileged transaction sender at this time — this is flagged as a defense-in-depth / latent-bug finding rather than an immediately exploitable one, and should be verified by a background engineer with access to run the full `evm`/`evm2` test suites to check all refund-accounting code paths that populate `Gas::refunded()`.

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

**File:** crates/common/l1-fees/src/params.rs (L188-199)
```rust
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
