### Title
Unbounded Isthmus operator-fee scalar/constant from L1 `SystemConfig` lets a per-transaction fee be inflated without limit - (File: `crates/common/genesis/src/updates/operator_fee.rs`, `crates/common/l1-fees/src/params.rs`)

### Summary
`OperatorFeeUpdate::apply` writes the `operator_fee_scalar` (u32) and `operator_fee_constant` (u64) decoded from an L1 `ConfigUpdate(OperatorFee)` log directly into `SystemConfig` with no upper- or lower-bound validation, unlike the sibling Ecotone L1 scalar update which is explicitly checked with `check_ecotone_l1_system_config_scalar`.

### Finding Description
`OperatorFeeUpdate::try_from` only validates ABI shape (pointer/length) via `UpdateDataValidator::validate`, then decodes the raw 4-byte scalar and 8-byte constant with no range checks: [1](#0-0) [2](#0-1) 

Compare to `GasConfigUpdate`, which validates the Ecotone scalar via `RollupConfig::check_ecotone_l1_system_config_scalar` and falls back to the previous value on invalid input: [3](#0-2) 

No equivalent bound exists for `operator_fee_scalar`/`operator_fee_constant`. These values flow, via `SystemConfig::update_with_receipts` → `process_config_update_log` → `SystemConfigUpdate::apply`, unconditionally into every subsequently derived L2 block's `SystemConfig` and L1-info deposit transaction: [4](#0-3) [5](#0-4) 

The values are then read into `L1FeeParams` and used to compute the per-transaction operator fee that every L2 user pays, with the constant added unconditionally and the scalar multiplying gas with no ceiling: [6](#0-5) 

### Impact Explanation
Because `operator_fee_constant` is a full `u64` added directly (in wei) to every non-exempt transaction's charge, and `operator_fee_scalar` is a `u32` multiplier on `gas_limit` with no cap, a value written into the L1 `SystemConfig` contract's `OperatorFee` update (e.g. `operator_fee_constant = u64::MAX` or a maximal scalar) is derived into L2 blocks and applied to every ordinary user transaction via `operator_fee_charge_inner`. This can make every L2 transaction economically unpayable (effective denial of the chain to all senders) or, conversely, force senders to overpay by an attacker-controlled unbounded amount, extracting value from every user transaction. This satisfies "unauthorized operation / theft of funds / node halt"-class impact reachable purely from data derived from L1, with no code-level guard against extreme values on the L2 side.

### Likelihood Explanation
Likelihood depends on the L1-side authorization for emitting `ConfigUpdate(OperatorFee)` logs (typically the L1 `SystemConfig` contract owner), which is outside this repo's scope. However, the L2 derivation code that consumes this data provides **no defense-in-depth bound check**, unlike the analogous Ecotone L1 fee scalar path, which does validate/reject and retain the old value. This asymmetry means any operator-fee update — whether from a compromised/malicious L1 owner key or a misconfiguration — is applied unconditionally and can immediately degrade or halt the L2 chain for all users, with the bug root-caused entirely in the L2 derivation logic (`OperatorFeeUpdate::apply`, `operator_fee_charge_inner`).

### Recommendation
Add explicit bounds validation for `operator_fee_scalar` and `operator_fee_constant` in `OperatorFeeUpdate::try_from`/`apply` (and/or in `L1FeeParams::operator_fee_charge_inner`), mirroring the pattern used for `check_ecotone_l1_system_config_scalar`: reject or clamp scalar/constant values above a sane protocol-defined ceiling, retaining the previous valid `SystemConfig` values on out-of-range input instead of applying them unconditionally.

### Proof of Concept
1. On L1, emit a `ConfigUpdate(uint256 version, uint8 updateType=5 /* OperatorFee */, bytes data)` log where `data` encodes `operator_fee_scalar = 0xFFFFFFFF` and `operator_fee_constant = 0xFFFFFFFFFFFFFFFF` (as done by the test harness `enqueue_operator_fee_update`): [7](#0-6) 
2. `SystemConfig::update_with_receipts` decodes this via `OperatorFeeUpdate::try_from`, which performs no range checks and applies it verbatim to `SystemConfig.operator_fee_scalar`/`operator_fee_constant`: [2](#0-1) 
3. On the next epoch change, the `StatefulAttributesBuilder` propagates these values into the L1-info deposit transaction of every subsequent L2 block, as verified by the existing test `operator_fee_update_reflected_in_derived_l2_blocks`-style assertions: [8](#0-7) 
4. `L1FeeParams::operator_fee_charge_inner` then charges every ordinary (non-deposit, non-empty) L2 transaction `gas_limit * operator_fee_scalar [* OPERATOR_FEE_JOVIAN_MULTIPLIER] + operator_fee_constant`, with no upper bound, making transactions unpayable or overcharging every sender chain-wide: [6](#0-5)

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

**File:** crates/common/genesis/src/system/config.rs (L130-154)
```rust
    pub fn update_with_receipts(
        &mut self,
        receipts: &[Receipt],
        l1_system_config_address: Address,
        ecotone_active: bool,
    ) -> (Vec<SystemConfigUpdateKind>, Vec<SystemConfigUpdateError>) {
        receipts
            .iter()
            .filter(|r| r.status != Eip658Value::Eip658(false))
            .flat_map(|r| &r.logs)
            .filter(|log| {
                let topics = log.topics();
                log.address == l1_system_config_address
                    && !topics.is_empty()
                    && topics[0] == SystemConfigUpdate::TOPIC
            })
            .map(|log| self.process_config_update_log(log, ecotone_active))
            .fold((Vec::new(), Vec::new()), |(mut updates, mut errors), result| {
                match result {
                    Ok(kind) => updates.push(kind),
                    Err(e) => errors.push(e),
                }
                (updates, errors)
            })
    }
```

**File:** crates/common/genesis/src/system/update.rs (L42-54)
```rust
    /// Applies the update to the [`SystemConfig`].
    pub const fn apply(&self, config: &mut SystemConfig) {
        match self {
            Self::Batcher(update) => update.apply(config),
            Self::GasConfig(update) => update.apply(config),
            Self::GasLimit(update) => update.apply(config),
            Self::UnsafeBlockSigner(_) => { /* Ignored in derivation */ }
            Self::Eip1559(update) => update.apply(config),
            Self::OperatorFee(update) => update.apply(config),
            Self::MinBaseFee(update) => update.apply(config),
            Self::DaFootprintGasScalar(update) => update.apply(config),
        }
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

**File:** actions/harness/tests/upgrade/operator_fees.rs (L673-687)
```rust
    // Block 6 (epoch 1 — first epoch change) carries NEW fee params. This is the
    // "seventh" block total counting from genesis (block 0), confirming that
    // StatefulAttributesBuilder reads L1 block 1's receipts on the epoch change.
    let info6 = find(6).expect("L1 info tx for block 6 must be recorded");
    assert_eq!(
        info6.operator_fee_scalar(),
        NEW_SCALAR,
        "block 6: operator_fee_scalar must reflect the OperatorFee config update"
    );
    assert_eq!(
        info6.operator_fee_constant(),
        NEW_CONSTANT,
        "block 6: operator_fee_constant must reflect the OperatorFee config update"
    );
}
```
