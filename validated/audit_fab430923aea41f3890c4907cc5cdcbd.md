### Title
Missing MIN/MAX bounds on `operator_fee_scalar`/`operator_fee_constant` when applying L1 `SystemConfig` `OperatorFee` updates - ([File: crates/common/genesis/src/updates/operator_fee.rs])

### Summary
The `OperatorFeeUpdate::apply` function writes `operator_fee_scalar` (u32) and `operator_fee_constant` (u64) directly into `SystemConfig` from an L1 `ConfigUpdate` log with no range/sanity validation, unlike sibling update types (`Eip1559Update`, `MinBaseFeeUpdate`/Jovian base-fee path, `DaFootprintGasScalarUpdate`) which enforce non-zero/consistency invariants before or during application.

### Finding Description
`OperatorFeeUpdate::try_from` only validates the ABI encoding shape of the log (pointer/length fields via `UpdateDataValidator`), then decodes the raw `scalar`/`constant` bytes with no bound checks: [1](#0-0) [2](#0-1) 

By contrast, other numeric `SystemConfig` fields updated via the same `ConfigUpdate` log mechanism DO get validated: `DaFootprintGasScalarUpdate::apply` substitutes a default when the value is zero [3](#0-2) , and EIP-1559 params are checked for a valid zero/non-zero denominator-elasticity combination before being accepted at multiple points in the pipeline (`base_fee_params_from_extra_data`, `ensure_well_formed_attributes`, `AttributesMatch::check_eip1559`) [4](#0-3) [5](#0-4) .

`OperatorFeeUpdate::apply` has no analogous guard: [6](#0-5) 

Once applied, `operator_fee_scalar`/`operator_fee_constant` flow into every L2 block's L1-info deposit transaction via `system_config_from_transaction`/`L1BlockInfoTx` and are used, post-Isthmus, to compute a per-transaction operator fee that is deducted from every ordinary (non-deposit) sender in `operator_fee_charge_inner`, which saturating-multiplies/adds an attacker-influenced constant into the fee owed by every transaction: [7](#0-6) 
and is charged upfront from the caller's balance before execution (`ensure_can_pay_fees`/`charge_upfront`) in the standard EVM transaction handler: [8](#0-7) 

If `operator_fee_constant` (u64) or `operator_fee_scalar` (u32) is set to an extreme value — whether via a legitimate-looking but misconfigured L1 `SystemConfig` update, or a bug/mistake in the value that reaches L1 — the derived operator fee for every L2 transaction can become larger than essentially any user's balance, causing every ordinary transaction to fail the upfront balance/fee check. This mirrors the referenced Y2K `Carousel.sol` issue exactly: an important storage value (a per-transaction fee input) is changed with no MIN/MAX enforcement, and downstream user transactions (deposits in the original report; ordinary L2 transactions here) become unable to proceed once that value crosses a threshold relative to user balances.

### Impact Explanation
Because the operator fee is charged on every non-deposit transaction via `before_execution`/`ensure_can_pay_fees`, an unbounded `operator_fee_constant`/`operator_fee_scalar` value effectively prices out (or outright reverts) all ordinary user transactions on the L2, which is a chain-halt/DoS-class impact on the sequencing/consolidation path — a legitimate transaction sender can no longer get any transaction included, and there is no protocol-level bound preventing this from being derived and applied. This is distinct from and stronger than a simple "wrong fee accounted" bug because it can make the chain effectively unusable for all senders simultaneously until a corrective `SystemConfig` update propagates through L1 finality and the derivation pipeline (which, per the OP-stack design, is not instantaneous).

### Likelihood Explanation
This requires the value that lands in the `OperatorFee` `ConfigUpdate` log to be extreme. The trust boundary for that update is whoever controls the L1 `SystemConfig` contract's operator-fee setter — this is a privileged, config-owner-controlled path rather than something an arbitrary unprivileged L1/L2 user can trigger directly, so likelihood of an adversarial trigger is bounded by that privilege. However, the same missing bound also protects against operational/human error (accidentally setting an implausible scalar/constant), which is the exact scenario the referenced report is about ("if by mistake relayerFee value get change to large value"), and once it occurs there is no in-protocol clamp, pause, or emergency mechanism to stop it from being derived and applied to every L2 block.

### Recommendation
Add explicit MIN/MAX sanity bounds when applying `OperatorFeeUpdate` (and ideally when constructing/deriving `L1BlockInfoTx` operator-fee fields), e.g., reject or clamp `operator_fee_scalar`/`operator_fee_constant` values whose resulting `operator_fee_charge_inner` output at typical gas limits would exceed a configured maximum fee-per-gas-limit ceiling, consistent with how `Eip1559Update`/`DaFootprintGasScalarUpdate` already validate or default out-of-range inputs before they are written into `SystemConfig`.

### Proof of Concept
1. Operator/attacker of the L1 `SystemConfig` contract emits a `ConfigUpdate(OperatorFee)` log with `operator_fee_constant = u64::MAX` (or a very large `operator_fee_scalar`).
2. `SystemConfig::update_with_receipts` → `process_config_update_log` → `OperatorFeeUpdate::try_from` decodes this with no bound check and `OperatorFeeUpdate::apply` writes it directly into `SystemConfig.operator_fee_constant` [6](#0-5) .
3. The derivation pipeline propagates this value into subsequent L2 blocks' L1-info deposit transactions (`system_config_from_transaction`) [9](#0-8) .
4. Every subsequent non-deposit L2 transaction now has `operator_fee_charge_inner` return a value dominated by the huge constant [10](#0-9) , which is charged upfront in `before_execution`/`ensure_can_pay_fees` [8](#0-7) , causing ordinary user transactions to fail for insufficient balance chain-wide until the value is corrected and re-derived through L1.

Note: I could not fully trace whether `crates/execution/txpool/src/validator.rs` (which also references `operator_fee` per the grep results) performs any independent bound-check at admission time that might mitigate this before block inclusion; this file's contents were not retrieved before the tool budget was exhausted, so it's possible a txpool-level guard exists that partially reduces the practical impact. I recommend verifying that file directly (or starting a full Devin session) before treating this as fully unmitigated.

### Citations

**File:** crates/common/genesis/src/updates/operator_fee.rs (L19-25)
```rust
impl OperatorFeeUpdate {
    /// Applies the update to the [`SystemConfig`].
    pub const fn apply(&self, config: &mut SystemConfig) {
        config.operator_fee_scalar = Some(self.operator_fee_scalar);
        config.operator_fee_constant = Some(self.operator_fee_constant);
    }
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

**File:** crates/common/genesis/src/updates/da_footprint_gas_scalar.rs (L21-32)
```rust
    /// Applies the update to the [`SystemConfig`].
    pub const fn apply(&self, config: &mut SystemConfig) {
        let mut da_footprint_gas_scalar = self.da_footprint_gas_scalar;

        // If the da footprint gas scalar is 0, use the default value
        // <https://github.com/ethereum-optimism/specs/blob/664cba65ab9686b0e70ad19fdf2ad054d6295986/specs/protocol/jovian/l1-attributes.md#overview>
        if da_footprint_gas_scalar == 0 {
            da_footprint_gas_scalar = Self::DEFAULT_DA_FOOTPRINT_GAS_SCALAR;
        };

        config.da_footprint_gas_scalar = Some(da_footprint_gas_scalar);
    }
```

**File:** crates/execution/chainspec/src/basefee.rs (L11-24)
```rust
fn base_fee_params_from_extra_data(
    chain_spec: impl EthChainSpec,
    timestamp: u64,
    elasticity: u32,
    denominator: u32,
) -> Result<BaseFeeParams, EIP1559ParamError> {
    if elasticity == 0 && denominator == 0 {
        Ok(chain_spec.base_fee_params_at_timestamp(timestamp))
    } else if elasticity == 0 || denominator == 0 {
        Err(EIP1559ParamError::InvalidParams)
    } else {
        Ok(BaseFeeParams::new(denominator as u128, elasticity as u128))
    }
}
```

**File:** crates/execution/node/src/engine.rs (L322-331)
```rust

            if elasticity != 0 && denominator == 0 {
                return Err(EngineObjectValidationError::InvalidParams(
                    "Eip1559ParamsDenominatorZero".to_string().into(),
                ));
            } else if denominator != 0 && elasticity == 0 {
                return Err(EngineObjectValidationError::InvalidParams(
                    "Eip1559ParamsElasticityZero".to_string().into(),
                ));
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

**File:** crates/common/evm2/src/handler.rs (L115-132)
```rust
        // Charge the upfront gas cost, the L1 data fee, and the operator fee (on the gas limit)
        // from the caller. Both fees are stashed for settlement so it charges/refunds against the
        // exact amounts collected here, without recomputing them.
        let l1_fee = Self::l1_fee(host, envelope);
        let operator_fee = Self::operator_fee(host, envelope, envelope.gas_limit());
        let ext = host.ext_mut();
        ext.l1_fee = l1_fee;
        ext.operator_fee = operator_fee;
        // `validate_sender` only checks the gas cost and value; the L1 and operator fees are
        // charged on top here via `charge_upfront`'s wrapping subtraction, so reject an
        // underfunded caller up front rather than let its balance wrap to a spurious value.
        Self::ensure_can_pay_fees(host, envelope, caller, l1_fee, operator_fee)?;
        charge_upfront(
            host,
            caller,
            upfront_fee.saturating_add(l1_fee).saturating_add(operator_fee),
        )
    }
```

**File:** crates/consensus/protocol/src/utils.rs (L134-137)
```rust
    if rollup_config.is_isthmus_active(timestamp) {
        cfg.operator_fee_scalar = Some(l1_info.operator_fee_scalar());
        cfg.operator_fee_constant = Some(l1_info.operator_fee_constant());
    }
```
