### Title
Missing maximum cap on operator fee scalar/constant charged per transaction - (File: `crates/common/genesis/src/updates/operator_fee.rs`)

### Summary
The `OperatorFeeUpdate::apply` function writes an L1-derived `operator_fee_scalar` (u32) and `operator_fee_constant` (u64) directly into `SystemConfig` with no sanity ceiling, and this exact pair is later used unmodified by `L1FeeParams::operator_fee_charge`/`operator_fee_charge_inner` to compute the per-transaction operator fee charged to every L2 sender.

### Finding Description
`OperatorFeeUpdate::try_from` decodes the raw scalar/constant bytes straight from a `ConfigUpdate(OperatorFee)` log with only structural validation (data length/pointer checks via `UpdateDataValidator`), and `apply` copies the values into `SystemConfig` unconditionally: [1](#0-0) 

There is no upper-bound/business-logic check anywhere in the update path (`SystemConfigUpdate::apply`, `SystemConfigUpdateError`) analogous to the "max cap" that is missing in the reported `MainVault.transferFunds` gov-fee case.

These raw values flow into the fee math used for every transaction: [2](#0-1) 

`operator_fee_scalar` is a `u32` with `OPERATOR_FEE_SCALAR_DECIMAL = 1_000_000` as the only normalization, and post-Jovian the scalar is additionally multiplied by `OPERATOR_FEE_JOVIAN_MULTIPLIER = 100`: [3](#0-2) 

Because there is no ceiling check on the decoded `operator_fee_scalar`/`operator_fee_constant` (nor any downstream clamp in `operator_fee_charge_inner`), an extreme value posted via the `ConfigUpdate` log is applied verbatim to every subsequent L2 block's L1 info deposit and charged to every user transaction (this is exercised, without any bound assertion, by the propagation test): [4](#0-3) 

### Impact Explanation
If `operator_fee_scalar`/`operator_fee_constant` are posted with excessively large values (whether via an L1 governance mistake or a compromise of the L1 `SystemConfig` contract that emits this event), every L2 transaction's operator fee (routed to `OPERATOR_FEE_VAULT`) can balloon to consume most or all of a sender's transferred value, mirroring the reported class of bug where an unbounded fee parameter drains user funds with no protocol-level cap to prevent it. This is a direct, unauthorized extraction of user funds at the protocol level, matching the Medium-severity "loss of funds due to missing fee cap" class.

### Likelihood Explanation
This requires the value to originate from the L1 `SystemConfig` contract's `ConfigUpdate` event, which is normally owner-gated on L1; however, the L2 derivation code in this repository performs no independent bound-checking of the decoded scalar/constant, so it fully trusts and propagates whatever is posted — there is no defense-in-depth cap at the point where the value is applied or consumed, unlike other fee paths in the codebase (e.g., `FeeCheck::validate_fees` for EIP-1559 fields) that do enforce explicit invariants.

### Recommendation
Add an explicit maximum-value check (a "sane ceiling") on `operator_fee_scalar` and `operator_fee_constant` in `OperatorFeeUpdate::try_from` (or in `SystemConfigUpdate::apply`) before committing them to `SystemConfig`, rejecting or clamping values beyond a documented, protocol-agreed limit, and add unit tests exercising near-`u32::MAX`/`u64::MAX` scalar/constant inputs to prove the operator fee cannot exceed the intended cap.

### Proof of Concept
1. Emit a `ConfigUpdate(OperatorFee)` L1 log with `operator_fee_scalar = u32::MAX` and `operator_fee_constant = u64::MAX`, following the encoding path exercised by `enqueue_operator_fee_update`: [5](#0-4) 
2. Let the derivation pipeline apply the update via `OperatorFeeUpdate::apply`, which unconditionally overwrites `SystemConfig.operator_fee_scalar`/`operator_fee_constant` with no bound check: [1](#0-0) 
3. Any subsequent L2 transaction's operator fee is computed by `operator_fee_charge_inner` using these unbounded values, producing a fee that can consume the transaction's entire gas budget or more, extracted into `OPERATOR_FEE_VAULT`: [6](#0-5)

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

**File:** crates/common/l1-fees/src/params.rs (L10-14)
```rust
pub const NON_ZERO_BYTE_MULTIPLIER_ISTANBUL: u64 = 4;
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

**File:** actions/harness/tests/upgrade/operator_fees.rs (L578-592)
```rust
#[tokio::test]
async fn operator_fee_config_update_propagates_to_l1_info() {
    const OLD_SCALAR: u32 = 1_000;
    const OLD_CONSTANT: u64 = 500;
    const NEW_SCALAR: u32 = 3_000;
    const NEW_CONSTANT: u64 = 700;

    let l1_sys_cfg_addr = Address::repeat_byte(0xCC);
    let batcher_cfg = BatcherConfig::default();
    let mut rollup_cfg =
        TestRollupConfigBuilder::base_mainnet(&batcher_cfg).through_isthmus().build();
    rollup_cfg.l1_system_config_address = l1_sys_cfg_addr;
    let sys_cfg = rollup_cfg.genesis.system_config.as_mut().unwrap();
    sys_cfg.operator_fee_scalar = Some(OLD_SCALAR);
    sys_cfg.operator_fee_constant = Some(OLD_CONSTANT);
```

**File:** actions/harness/src/l1/miner.rs (L376-400)
```rust
    pub fn enqueue_operator_fee_update(
        &mut self,
        l1_sys_cfg_addr: Address,
        operator_fee_scalar: u32,
        operator_fee_constant: u64,
    ) {
        let mut data = [0u8; 96];
        data[31] = 0x20; // pointer = 32
        data[63] = 0x20; // length  = 32
        data[84..88].copy_from_slice(&operator_fee_scalar.to_be_bytes());
        data[88..96].copy_from_slice(&operator_fee_constant.to_be_bytes());
        let mut update_type = [0u8; 32];
        update_type[31] = 5; // OperatorFee = 5
        self.enqueue_log(Log {
            address: l1_sys_cfg_addr,
            data: LogData::new_unchecked(
                vec![
                    SystemConfigUpdate::TOPIC,
                    SystemConfigUpdate::EVENT_VERSION_0,
                    B256::from(update_type),
                ],
                data.into(),
            ),
        });
    }
```
