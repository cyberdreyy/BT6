### Title
Isthmus/Jovian operator fee scalar has no upper bound, allowing user transaction fees to be inflated without limit - (File: `crates/common/l1-fees/src/params.rs`)

### Summary
The OP-Stack Isthmus operator fee is computed from an `operator_fee_scalar` (u32) and `operator_fee_constant` (u64) that are decoded verbatim from L1 `SystemConfigUpdate` logs / L1 attributes deposit calldata and applied to every L2 transaction's gas cost with **no range validation** anywhere in the decode, update-application, or fee-charge pipeline. This mirrors the reported Opyn finding: an unconstrained fee multiplier can force users to pay an arbitrarily large (potentially near-total) portion of their transaction budget in fees.

### Finding Description
`OperatorFeeUpdate::try_from` decodes the raw `operator_fee_scalar`/`operator_fee_constant` fields directly from the L1 `SystemConfigUpdate` log payload with no bounds checking beyond basic ABI-length validation, then applies them unconditionally to the `SystemConfig`: [1](#0-0) [2](#0-1) 

Likewise, `L1BlockInfoIsthmus::decode_calldata_body` decodes the scalar/constant straight from the L1 attributes deposit calldata bytes with no sanity check on the value: [3](#0-2) 

These unbounded values are then fed directly into the fee-charging formula, which applies `gas * operator_fee_scalar / OPERATOR_FEE_SCALAR_DECIMAL` (or `gas * operator_fee_scalar * 100` under Jovian) plus a flat constant, with no cap on the resulting fee relative to the user's actual gas cost: [4](#0-3) 

This computed `operator_fee_charge` is charged upfront from the payer's balance alongside the L1 data fee and gas cost, and credited to the `OPERATOR_FEE_VAULT`, with no ceiling check performed against it: [5](#0-4) [6](#0-5) 

The same unbounded value is also used in the revm-based execution path's `prepay`/`tx_cost` flow: [7](#0-6) 

Because `operator_fee_scalar` is a `u32` with a fixed-point decimal of `1_000_000`, its unbounded value allows a multiplier of up to ~4295x the gas cost pre-Jovian, and ~429,500x post-Jovian (extra 100x multiplier): [8](#0-7) 

There is no `MAX_OPERATOR_FEE_SCALAR` or equivalent guard anywhere in the derivation-to-execution pipeline (system-config update application, L1 info decoding, or the fee-charge math itself), unlike the analogous `fee_limit_multiplier`/`fee_limit_threshold` ceiling enforced for the tx-manager's own gas-price bumping logic (`FeeCalculator::check_limits`), which shows the codebase does elsewhere recognize the need for fee ceilings but did not apply the same discipline to the operator fee scalar derived from L1 system-config data: [9](#0-8) 

### Impact Explanation
Once an oversized `operator_fee_scalar`/`operator_fee_constant` is written into the derived `SystemConfig`/L1 attributes deposit, every subsequent L2 transaction is charged the inflated operator fee upfront from the payer's balance (`ensure_can_pay_fees`/`charge_upfront`), redirecting the user's funds to the `OPERATOR_FEE_VAULT`. In the extreme, this can consume the vast majority of (or exceed) a user's intended gas budget, effectively forcing users to overpay fees on every transaction until the bad system-config value is corrected — matching the reported bug class of "fees does not have limit, potentially causing user to pay [nearly] 100% in fees." This is a direct, protocol-level loss of user funds via mispriced/unbounded fee accounting rather than a benign config mistake, since no code path bounds the value before it is charged.

### Likelihood Explanation
This requires the L1-side entity that emits `SystemConfigUpdate` (OperatorFee) events / constructs the Isthmus L1 attributes deposit to set an oversized scalar — i.e., attacker-influenced or malformed L1 system-config/deposit data as explicitly permitted by scope. Given there is no validation anywhere in the decode → apply → charge pipeline, any out-of-range value propagates straight through to fee collection on every L2 transaction without any circuit breaker.

### Recommendation
Add an explicit upper bound (e.g., a `MAX_OPERATOR_FEE_SCALAR` and `MAX_OPERATOR_FEE_CONSTANT`, matching what a "reasonable" fee percentage would be) enforced in `OperatorFeeUpdate::try_from` (or at `SystemConfig` application time) and in `L1BlockInfoIsthmus::decode_calldata_body`/`operator_fee_charge_inner`, rejecting or clamping updates that exceed the sane ceiling, consistent with the ceiling pattern already used in `FeeCalculator::check_limits`.

### Proof of Concept
1. Construct (or have derivation ingest) a `SystemConfigUpdate` log / Isthmus L1 attributes deposit with `operator_fee_scalar = u32::MAX` (4,294,967,295) and `operator_fee_constant = u64::MAX`.
2. `OperatorFeeUpdate::try_from` decodes and applies these values unchecked to `SystemConfig` (`crates/common/genesis/src/updates/operator_fee.rs:19-24,56-66`).
3. On the next L2 epoch, `L1BlockInfoIsthmus`/`L1FeeParams.operator_fee_scalar` is seeded with this value with no bound (`crates/consensus/protocol/src/info/isthmus.rs:125-151`).
4. Every subsequent transaction's `operator_fee_charge_inner` computes `gas * u32::MAX / 1_000_000` (pre-Jovian) or `gas * u32::MAX * 100` (Jovian), producing a fee far exceeding the transaction's actual gas cost, charged upfront in `before_execution` (`crates/common/evm2/src/handler.rs:108-132`) and credited to `OPERATOR_FEE_VAULT`, causing users to pay a disproportionate/near-total share of their transaction value in fees.

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

**File:** crates/consensus/protocol/src/info/isthmus.rs (L125-151)
```rust
    pub fn decode_calldata_body(r: &[u8]) -> Result<Self, DecodeError> {
        let base = L1BlockInfoEcotoneBase::decode_calldata_body(r);

        // Decode Isthmus-specific fields
        // SAFETY: 4 bytes are copied directly into the array
        let mut operator_fee_scalar = [0u8; 4];
        operator_fee_scalar.copy_from_slice(&r[164..168]);
        let operator_fee_scalar = u32::from_be_bytes(operator_fee_scalar);

        // SAFETY: 8 bytes are copied directly into the array
        let mut operator_fee_constant = [0u8; 8];
        operator_fee_constant.copy_from_slice(&r[168..176]);
        let operator_fee_constant = u64::from_be_bytes(operator_fee_constant);

        Ok(Self::new(
            base.number(),
            base.time(),
            base.base_fee(),
            base.block_hash(),
            base.sequence_number(),
            base.batcher_address(),
            base.blob_base_fee(),
            base.blob_base_fee_scalar(),
            base.base_fee_scalar(),
            operator_fee_scalar,
            operator_fee_constant,
        ))
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

**File:** crates/common/evm2/src/handler.rs (L150-163)
```rust
        // Refund the operator fee down from the amount charged (on the gas limit) to the gas
        // actually used; the net is credited to the operator-fee vault below.
        let operator_fee_used = Self::operator_fee(host, envelope, gas_used);
        let operator_fee_refund = operator_fee_charged.saturating_sub(operator_fee_used);
        Self::credit(host, caller, operator_fee_refund)?;

        // Distribute the collected OP-stack fees to their vaults.
        Self::credit(host, Predeploys::L1_FEE_VAULT, l1_fee)?;
        Self::credit(
            host,
            Predeploys::BASE_FEE_VAULT,
            basefee.saturating_mul(U256::from(gas_used)),
        )?;
        Self::credit(host, Predeploys::OPERATOR_FEE_VAULT, operator_fee_used)?;
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

**File:** crates/utilities/tx-manager/src/fees.rs (L144-160)
```rust
    pub const fn check_limits(
        fee: u128,
        suggested: u128,
        fee_limit_multiplier: u64,
        fee_limit_threshold: u128,
    ) -> Result<(), TxManagerError> {
        if suggested < fee_limit_threshold {
            return Ok(());
        }

        let ceiling = (fee_limit_multiplier as u128).saturating_mul(suggested);
        if fee > ceiling {
            return Err(TxManagerError::FeeLimitExceeded { fee, ceiling });
        }

        Ok(())
    }
```
