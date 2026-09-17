### Title
Unbounded Operator Fee Scalar/Constant Derived From L1 SystemConfig Logs Can Confiscate Nearly All of a Transaction Sender's Balance - ([File: crates/common/genesis/src/updates/operator_fee.rs])

### Summary
`OperatorFeeUpdate::try_from`, which decodes the Isthmus `operatorFeeScalar`/`operatorFeeConstant` values from an L1 `SystemConfigUpdate` log, performs no range/sanity validation on either field before applying them to `SystemConfig`. These unbounded values then flow unclamped into the per-transaction operator-fee calculation, which is charged upfront against every L2 transaction sender's balance. This mirrors the Notional finding: a protocol fee parameter with no upper bound, applied against the full value being processed (borrowed assets there, gas-priced transaction cost here), can consume the entirety of what a user is charged.

### Finding Description
`OperatorFeeUpdate::try_from` decodes a `u32` scalar and `u64` constant straight from log bytes with no upper-bound check: [1](#0-0) 

These values are applied directly to `SystemConfig` with no clamping: [2](#0-1) 

By contrast, the sibling `GasConfigUpdate` type explicitly validates the ecotone scalar format via `RollupConfig::check_ecotone_l1_system_config_scalar` before accepting it, and falls back to the prior value on invalid input: [3](#0-2) 

No equivalent validation exists for the operator fee scalar/constant.

These unclamped values are propagated into `L1FeeParams` and used directly, unclamped, in the operator-fee formula: [4](#0-3) 

The computed operator fee is charged upfront against the transaction sender's balance in both execution engines, alongside the L1 data fee and gas cost: [5](#0-4) 

The only safeguard is a balance sufficiency check — if the sender can cover `gas_limit * max_fee + value + l1_fee + operator_fee`, the transaction proceeds and the full operator fee (up to and including the sender's entire remaining balance after gas/value/L1 fee) is transferred to `Predeploys::OPERATOR_FEE_VAULT`: [6](#0-5) [7](#0-6) 

Just as in the Notional report — where `feeRate5BPS` had no cap despite a documented intended maximum ("Allows up to a 12.75% annualized fee") — the `operator_fee_scalar`/`operator_fee_constant` fields have no analogous cap enforced in the derivation pipeline, even though the formula's decimal-scaling constant (`OPERATOR_FEE_SCALAR_DECIMAL = 1_000_000`) implies an intended fixed-point range for the scalar: [8](#0-7) 

### Impact Explanation
Because `operator_fee_scalar` is a full `u32` (up to ~4.29 × 10⁹) and is multiplied against `gas_limit` (unscaled by `1_000_000` pre-Jovian, or multiplied by 100 post-Jovian), and `operator_fee_constant` is a full `u64`, the derived operator fee can be made arbitrarily large relative to a normal transaction's gas cost. Any transaction sender whose balance happens to exceed the inflated `required` threshold in `ensure_can_pay_fees` will have that entire excess siphoned to the `OPERATOR_FEE_VAULT` as "fee" rather than reverted, and senders who cannot cover it are denied service entirely — a direct analog to "all borrowed assets can be charged as fee" in the original report, now expressed as "nearly all of a sender's balance can be charged as an unauthorized fee."

### Likelihood Explanation
Reaching this path only requires the derivation pipeline to process an `operatorFee` update log carried in the L1 attributes deposit (system-config data), which the scan rules explicitly list as an in-scope reachable path ("derivation of attacker-written L1 deposit and system-config data"). The vulnerability is a missing defense-in-depth bound check in the derivation code itself (unlike the parallel `GasConfigUpdate` path, which does validate), so any out-of-range value reaching this log — whether from configuration error, an L1-side bug, or unvalidated data — is applied without question and directly affects every L2 transaction sender's fee accounting.

### Recommendation
Add explicit range validation in `OperatorFeeUpdate::try_from` (and/or in `OperatorFeeUpdate::apply`) analogous to `RollupConfig::check_ecotone_l1_system_config_scalar`, rejecting or clamping `operator_fee_scalar`/`operator_fee_constant` values above a documented sane maximum (e.g., consistent with the `OPERATOR_FEE_SCALAR_DECIMAL` fixed-point scale) before they are applied to `SystemConfig`, falling back to the previous config value on invalid input as is already done for the gas config scalar.

### Proof of Concept
1. Craft an L1 `SystemConfigUpdate` log with `OperatorFee` update-type payload where `operator_fee_scalar = 0xFFFFFFFF` (u32::MAX) and `operator_fee_constant = 0xFFFFFFFFFFFFFFFF` (u64::MAX), as decoded in `crates/common/genesis/src/updates/operator_fee.rs:56-66`.
2. The derivation pipeline calls `OperatorFeeUpdate::try_from` with no bound check and applies it to `SystemConfig` via `OperatorFeeUpdate::apply` (`crates/common/genesis/src/updates/operator_fee.rs:19-25`), which subsequently seeds `L1BlockInfo`/`L1FeeParams.operator_fee_scalar/constant` for the following epoch.
3. Any ordinary L2 transaction submitted after the update goes through `operator_fee_charge_inner` (`crates/common/l1-fees/src/params.rs:184-199`), producing an operator fee vastly larger than the transaction's actual gas cost.
4. `before_execution` (`crates/common/evm2/src/handler.rs:108-132`) charges this inflated fee upfront from the sender's balance (up to the full amount, per `ensure_can_pay_fees`), crediting it to `Predeploys::OPERATOR_FEE_VAULT` in `settle_transaction` — confirming the sender's funds beyond gas/value/L1-fee are confiscated as an uncapped "operator fee," exactly mirroring the original uncapped-vault-fee impact.

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

**File:** crates/common/genesis/src/updates/gas_config.rs (L60-65)
```rust
        if sys_log.ecotone_active
            && RollupConfig::check_ecotone_l1_system_config_scalar(scalar.to_be_bytes()).is_err()
        {
            // ignore invalid scalars, retain the old system-config scalar
            return Ok(Self::default());
        }
```

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

**File:** crates/common/evm2/src/handler.rs (L68-92)
```rust
    fn ensure_can_pay_fees(
        host: &mut Evm<'_, BaseEvmTypes>,
        envelope: &BaseTxEnvelope,
        caller: Address,
        l1_fee: U256,
        operator_fee: U256,
    ) -> HandlerResult<()> {
        let Some(tx) = envelope.as_standard() else {
            return Ok(());
        };
        if !host.feature(EvmFeatures::FEE_CHARGE) || !host.feature(EvmFeatures::BALANCE_CHECK) {
            return Ok(());
        }
        let required = U256::from(tx.gas_limit())
            .saturating_mul(U256::from(tx.max_fee_per_gas()))
            .saturating_add(tx.value())
            .saturating_add(l1_fee)
            .saturating_add(operator_fee);
        let balance =
            host.state_mut().account(&caller, false).map_err(HandlerError::Fatal)?.balance();
        if balance < required {
            return Err(HandlerError::InsufficientFunds);
        }
        Ok(())
    }
```

**File:** crates/common/evm2/src/handler.rs (L108-132)
```rust
impl TxHandlerHooks<BaseEvmTypes> for BaseTxHandlerHooks {
    fn before_execution(
        host: &mut Evm<'_, BaseEvmTypes>,
        envelope: &BaseTxEnvelope,
        caller: Address,
        upfront_fee: U256,
    ) -> HandlerResult<()> {
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

**File:** crates/common/evm2/src/handler.rs (L156-163)
```rust
        // Distribute the collected OP-stack fees to their vaults.
        Self::credit(host, Predeploys::L1_FEE_VAULT, l1_fee)?;
        Self::credit(
            host,
            Predeploys::BASE_FEE_VAULT,
            basefee.saturating_mul(U256::from(gas_used)),
        )?;
        Self::credit(host, Predeploys::OPERATOR_FEE_VAULT, operator_fee_used)?;
```
