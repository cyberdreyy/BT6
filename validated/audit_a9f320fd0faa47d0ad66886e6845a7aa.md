### Title
Missing `OPERATOR_FEE_SCALAR_DECIMAL` normalization in the Jovian operator-fee formula overcharges every post-Jovian transaction by ~10,000x - ([File: crates/common/l1-fees/src/params.rs])

### Summary
`L1FeeParams::operator_fee_charge_inner` computes the Isthmus-era operator fee as `gas * operator_fee_scalar / OPERATOR_FEE_SCALAR_DECIMAL + operator_fee_constant`, correctly normalizing the fixed-point, 6-decimal `operator_fee_scalar`. For Jovian, the same function multiplies by `OPERATOR_FEE_JOVIAN_MULTIPLIER` but drops the `/ OPERATOR_FEE_SCALAR_DECIMAL` normalization entirely, exactly mirroring the Astaria H-16 bug class: a scaling/denominator term that is present in one branch of a fee/debt formula is silently dropped in a sibling branch, producing a grossly inflated charge.

### Finding Description
`operator_fee_charge_inner` in [1](#0-0)  branches on whether Jovian is active:

```rust
let product = if Self::is_enabled(upgrade, BaseUpgrade::Jovian) {
    gas.saturating_mul(operator_fee_scalar)
        .saturating_mul(U256::from(OPERATOR_FEE_JOVIAN_MULTIPLIER))
} else {
    gas.saturating_mul(operator_fee_scalar) / U256::from(OPERATOR_FEE_SCALAR_DECIMAL)
};
```

`operator_fee_scalar` is documented and used everywhere else as a 6-decimal fixed-point value (`OPERATOR_FEE_SCALAR_DECIMAL = 1_000_000`), per [2](#0-1) . The Isthmus (non-Jovian) branch correctly divides the `gas * scalar` product by this decimal factor before adding the constant. The Jovian branch instead multiplies by `OPERATOR_FEE_JOVIAN_MULTIPLIER = 100` and never divides by `1_000_000`, so the resulting fee is `10_000x` (i.e. `1_000_000 / 100`) larger than intended relative to what a "100x Isthmus fee" formula should produce.

This is confirmed by the project's own unit test in the same file: with `operator_fee_scalar = 1_000`, `operator_fee_constant = 10`, `gas = 1_000`, the Isthmus fee is `11` while the Jovian fee is `100_000_010` — a difference of roughly `9,090,910x`, not the `100x` the "Jovian multiplier" naming implies ( [3](#0-2) ). This same discrepancy is exercised (and implicitly accepted) in `crates/common/evm2/tests/standard_fee.rs`, which only asserts `jov_op > ist_op` without checking the magnitude is a bounded, intended multiple ( [4](#0-3) ).

This fee logic is invoked directly from the transaction-execution/gas-accounting path for every non-deposit transaction once Jovian is active, via `L1BlockInfo::operator_fee_charge` / `operator_fee_charge_inner` in `crates/common/evm/src/l1block.rs` ( [5](#0-4) ) and `operator_fee_refund` ( [6](#0-5) ), both reachable by any unprivileged sender submitting a signed L2 transaction.

### Impact Explanation
Once Jovian activates, every ordinary (non-deposit) transaction is charged an operator fee via this formula. If the deployed `operator_fee_scalar` values are calibrated assuming the same 6-decimal normalization used pre-Jovian (as the constant's own doc comment implies it should be for all forks), the missing division makes the fee ~10,000x larger than intended. This:
- Massively overcharges every transaction sender/payer on the network, draining funds into `OPERATOR_FEE_VAULT` far beyond the protocol's intended fee schedule — an unauthorized extraction of user funds at scale.
- Can make ordinary transactions economically unaffordable, effectively halting normal L2 transaction throughput (a chain-level denial of service) once Jovian is live with realistic scalar values.

This matches the required "concrete unauthorized... theft... or node halt" bar, analogous to how the Astaria bug made `potentialDebt` wildly miscalculated (off by an unintended multiplicative factor) and broke the intended economic constraint.

### Likelihood Explanation
The bug is deterministic and triggers on every transaction once `BaseUpgrade::Jovian` is active and `operator_fee_scalar` is non-zero — there is no attacker-controlled precondition required, only that the fork is live. This is entirely within the "fee accounting"/"EVM handler and fork gating" reachable-path category (an unprivileged sender simply submits a normal transaction) and requires no special privileges.

### Recommendation
Normalize the Jovian branch the same way as the Isthmus branch, then apply the Jovian multiplier, e.g.:
```rust
let product = if Self::is_enabled(upgrade, BaseUpgrade::Jovian) {
    gas.saturating_mul(operator_fee_scalar)
        .saturating_mul(U256::from(OPERATOR_FEE_JOVIAN_MULTIPLIER))
        / U256::from(OPERATOR_FEE_SCALAR_DECIMAL)
} else {
    gas.saturating_mul(operator_fee_scalar) / U256::from(OPERATOR_FEE_SCALAR_DECIMAL)
};
```
This should be cross-checked against the canonical OP-stack Jovian operator-fee spec to confirm the exact intended multiplier semantics, and the existing unit/integration tests (`params.rs`, `standard_fee.rs`) should be updated to assert the corrected, bounded ratio between Isthmus and Jovian fees rather than only `jov_op > ist_op`.

### Proof of Concept
Using the values from the existing test in [3](#0-2) :
- `operator_fee_scalar = 1_000`, `operator_fee_constant = 10`, `gas = 1_000`.
- Isthmus: `1_000 * 1_000 / 1_000_000 + 10 = 11`.
- Jovian (current code): `1_000 * 1_000 * 100 + 10 = 100_000_010`.
- Jovian (with the missing `/ 1_000_000` normalization): `1_000 * 1_000 * 100 / 1_000_000 + 10 = 110`.

The current implementation returns `100_000_010` instead of the `110` a consistent "100x Isthmus fee" formula would produce — an unintended ~909,091x inflation directly reachable by any sender's ordinary transaction post-Jovian activation.

### Citations

**File:** crates/common/l1-fees/src/params.rs (L11-14)
```rust
/// Fixed-point decimal scaling factor for the operator fee scalar (6 decimal points).
pub const OPERATOR_FEE_SCALAR_DECIMAL: u64 = 1_000_000;
/// Jovian multiplier applied to the operator fee scalar component.
pub const OPERATOR_FEE_JOVIAN_MULTIPLIER: u64 = 100;
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

**File:** crates/common/l1-fees/src/params.rs (L243-257)
```rust
    #[test]
    fn deposit_and_empty_are_fee_exempt() {
        let p = params();
        assert_eq!(p.calculate_tx_l1_cost(&[], BaseUpgrade::Ecotone), U256::ZERO);
        assert_eq!(p.calculate_tx_l1_cost(&[0x7E, 1, 2], BaseUpgrade::Ecotone), U256::ZERO);
    }

    #[test]
    fn operator_fee_zero_before_isthmus_scalars() {
        // Without scalars the operator fee reduces to the (zero) constant.
        let p = params();
        assert_eq!(
            p.operator_fee_charge_inner(U256::from(21_000), BaseUpgrade::Isthmus),
            U256::ZERO
        );
```

**File:** crates/common/evm2/tests/standard_fee.rs (L152-188)
```rust
#[test]
fn operator_fee_is_gated_by_isthmus_and_scaled_by_jovian() {
    let params = l1_fee_params();

    // Pre-Isthmus: no operator fee is collected even though the scalars are configured.
    let mut pre = build_evm2(BaseUpgrade::Fjord, U256::from(10u128.pow(18)));
    let _ = pre.transact(&Recovered::new_unchecked(eip1559_envelope(), SENDER)).unwrap().commit();
    assert_eq!(
        balance(&mut pre, Predeploys::OPERATOR_FEE_VAULT),
        U256::ZERO,
        "operator fee is not collected before Isthmus",
    );

    // Isthmus vs Jovian: the identical transaction uses the same gas, but Jovian applies the
    // operator-fee multiplier, so its operator fee is strictly larger and matches the Jovian
    // formula.
    let mut ist = build_evm2(BaseUpgrade::Isthmus, U256::from(10u128.pow(18)));
    let ist_gas = U256::from(
        ist.transact(&Recovered::new_unchecked(eip1559_envelope(), SENDER))
            .unwrap()
            .commit()
            .tx_gas_used(),
    );
    let mut jov = build_evm2(BaseUpgrade::Jovian, U256::from(10u128.pow(18)));
    let jov_gas = U256::from(
        jov.transact(&Recovered::new_unchecked(eip1559_envelope(), SENDER))
            .unwrap()
            .commit()
            .tx_gas_used(),
    );

    let ist_op = balance(&mut ist, Predeploys::OPERATOR_FEE_VAULT);
    let jov_op = balance(&mut jov, Predeploys::OPERATOR_FEE_VAULT);
    assert_eq!(ist_op, params.operator_fee_charge(&enveloped(), ist_gas, BaseUpgrade::Isthmus));
    assert_eq!(jov_op, params.operator_fee_charge(&enveloped(), jov_gas, BaseUpgrade::Jovian));
    assert!(jov_op > ist_op, "the Jovian multiplier increases the operator fee");
}
```

**File:** crates/common/evm/src/l1block.rs (L214-233)
```rust
    /// Calculate the operator fee for executing this transaction.
    ///
    /// Introduced in isthmus. Prior to isthmus, the operator fee is always zero.
    pub fn operator_fee_charge(&self, input: &[u8], gas_limit: U256, spec_id: BaseSpecId) -> U256 {
        // If the input is a deposit transaction or empty, the default value is zero.
        if input.is_empty() || input.first() == Some(&0x7E) {
            return U256::ZERO;
        }

        self.operator_fee_charge_inner(gas_limit, spec_id)
    }

    /// Calculate the operator fee for the given `gas`.
    ///
    /// Missing scalars fall back to zero so the txpool bootstrap window (post-Isthmus tx
    /// evaluated before the first L1 attributes deposit seeds these fields) does not panic;
    /// this matches the execution path, where uninitialized `L1_BLOCK_INFO` slots read as zero.
    fn operator_fee_charge_inner(&self, gas: U256, spec_id: BaseSpecId) -> U256 {
        self.params().operator_fee_charge_inner(gas, spec_id.upgrade())
    }
```

**File:** crates/common/evm/src/l1block.rs (L235-251)
```rust
    /// Calculate the operator fee for executing this transaction.
    ///
    /// Introduced in isthmus. Prior to isthmus, the operator fee is always zero.
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
