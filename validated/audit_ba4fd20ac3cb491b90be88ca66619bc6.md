### Title
Isthmus `OperatorFee` system-config update accepts unbounded scalar/constant with no cap, allowing an uncapped per-transaction fee to be levied on every L2 sender - (File: crates/common/genesis/src/updates/operator_fee.rs)

### Summary
This finding is analogous to the C4 report on `LiquidStakingManager._updateDAORevenueCommission()`, where a privileged actor could set a commission percentage to 100% with no cap, letting the DAO seize the entirety of node-runner rewards. In Base, the equivalent "commission-like" parameter is the Isthmus **operator fee** (`operator_fee_scalar` / `operator_fee_constant`), which is derived from an L1 `ConfigUpdate` log and applied verbatim to every L2 transaction's execution cost, with no bound enforced anywhere in the decode/apply path.

### Finding Description
`OperatorFeeUpdate::try_from` decodes the scalar and constant straight from the ABI payload of a `SystemConfigUpdate` log with no range or sanity check: [1](#0-0) 

`OperatorFeeUpdate::apply` then unconditionally writes these raw values into `SystemConfig`: [2](#0-1) 

These values flow into `L1FeeParams::operator_fee_charge_inner`, which is used to compute an **additional cost added on top of a transaction's L1 data fee** for every non-deposit, non-empty transaction, with `saturating_mul`/`saturating_add` and no upper cap: [3](#0-2) 

This charge is folded into `tx_cost`/`tx_cost_with_tx`, i.e. it is added to the cost the L2 sender must pay for every transaction once Isthmus is active: [4](#0-3) 

Unlike the audited `_updateDAORevenueCommission()` function in the original report — which at least enforces `require(_commissionPercentage <= MODULO)` — there is no analogous `require`/bound anywhere in `OperatorFeeUpdate`, `SystemConfigUpdate::apply`, or `L1FeeParams::operator_fee_charge_inner`. `operator_fee_scalar` is a `u32` (max ≈ 4.29×10⁹) and `operator_fee_constant` is a `u64` (max ≈ 1.8×10¹⁹, i.e. ~18 ETH in wei), both fully attacker/operator-controlled via the L1 `SystemConfig` contract's `ConfigUpdate` event, and both are trusted without validation by the derivation pipeline that builds every L2 block's L1 attributes deposit.

### Impact Explanation
If the system-config owner key is compromised (explicitly in-scope per the same reasoning the original judge used: "If the DAO is compromised, the impact is felt immediately and applies to all unclaimed rewards"), the attacker can push an `OperatorFee` config update with a maximal scalar/constant. Every L2 transaction thereafter is charged an unbounded, attacker-chosen "operator fee" on top of its L1 data fee — effectively an unauthorized, protocol-level tax that can consume all or most of a transaction's declared budget, redirecting value away from ordinary transaction senders. Because it is `saturating_mul`/`saturating_add`, worst case it can force costs up to `U256::MAX`, causing legitimate transactions to fail or be drained. This is an unauthorized-operation / theft-of-funds class impact reachable through standard L1→L2 derivation, affecting every L2 transaction sender without their consent, analogous to node runners losing "all their stake rewards" in the original report.

### Likelihood Explanation
Reaching this path requires control of (or a compromise of) the L1 `SystemConfig` update authority — the same trust assumption the original C4 finding relied on (a compromised/malicious DAO owner). No unbounded-fee cap exists in code to prevent it even as a policy backstop, so the only mitigating factor is the privilege required to emit the `ConfigUpdate` log, not any protocol-level validation. Given the report explicitly treats "DAO compromised" as in-scope and impactful immediately, the equivalent Base scenario (system-config owner compromised or misconfigured) is of comparable likelihood and severity.

### Recommendation
Add explicit bounds validation in `OperatorFeeUpdate::try_from` (or in `OperatorFeeUpdate::apply`) analogous to `_updateDAORevenueCommission`'s `require(_commissionPercentage <= MODULO)`: reject or clamp `operator_fee_scalar`/`operator_fee_constant` values above a sane maximum (e.g., matching the OP-stack spec's expected fixed-point range), and consider consensus-level sanity checks so a single malformed/malicious `ConfigUpdate` cannot impose unbounded fees on L2 users.

### Proof of Concept
1. An attacker (or compromised system-config owner) emits an `OperatorFee` `ConfigUpdate` log with `operator_fee_scalar = u32::MAX` and `operator_fee_constant = u64::MAX`, as accepted unmodified by: [1](#0-0) 
2. Once the L2 epoch advances past the L1 block containing this log, `SystemConfig.operator_fee_scalar`/`operator_fee_constant` are updated with no bound check (per `apply`), and every subsequently derived L2 block's L1 info deposit carries these values (demonstrated by the propagation test at `actions/harness/tests/upgrade/operator_fees.rs:578-687`).
3. For every ordinary L2 transaction from that point forward, `L1FeeParams::operator_fee_charge_inner` (`crates/common/l1-fees/src/params.rs:184-199`) computes `gas * operator_fee_scalar [* 100 if Jovian] / 1e6 + operator_fee_constant`, which with the maximal values above saturates to an enormous additional charge added to `tx_cost` (`crates/common/evm/src/l1block.rs:279-292`), imposing an attacker-controlled, effectively unbounded fee on every transaction sender.

### Citations

**File:** crates/common/genesis/src/updates/operator_fee.rs (L19-24)
```rust
impl OperatorFeeUpdate {
    /// Applies the update to the [`SystemConfig`].
    pub const fn apply(&self, config: &mut SystemConfig) {
        config.operator_fee_scalar = Some(self.operator_fee_scalar);
        config.operator_fee_constant = Some(self.operator_fee_constant);
    }
```

**File:** crates/common/genesis/src/updates/operator_fee.rs (L56-66)
```rust
        let payload = validated.payload();
        let mut be_bytes = [0u8; 4];
        be_bytes[0..4].copy_from_slice(&payload[20..24]);
        let operator_fee_scalar = u32::from_be_bytes(be_bytes);

        let mut be_bytes = [0u8; 8];
        be_bytes[0..8].copy_from_slice(&payload[24..32]);
        let operator_fee_constant = u64::from_be_bytes(be_bytes);

        Ok(Self { operator_fee_scalar, operator_fee_constant })
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

**File:** crates/common/evm/src/l1block.rs (L279-292)
```rust
    /// Calculate additional transaction cost.
    #[inline]
    pub fn tx_cost(&mut self, enveloped_tx: &[u8], gas_limit: U256, spec: BaseSpecId) -> U256 {
        // compute L1 cost
        let mut additional_cost = self.calculate_tx_l1_cost(enveloped_tx, spec);

        // compute operator fee
        if spec.is_enabled_in(BaseUpgrade::Isthmus) {
            let operator_fee_charge = self.operator_fee_charge(enveloped_tx, gas_limit, spec);
            additional_cost = additional_cost.saturating_add(operator_fee_charge);
        }

        additional_cost
    }
```
