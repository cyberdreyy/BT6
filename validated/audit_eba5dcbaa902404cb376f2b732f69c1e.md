### Title
Attacker-controlled L1 SystemConfig `GasLimit` update to zero halts all L2 transaction inclusion - (File: `crates/common/genesis/src/updates/gas_limit.rs`)

### Summary
The Portfolios.sol bug allowed an owner to set `numPeriods` to `0`, which propagated into `FutureCash.G_NUM_PERIODS = 0` and caused every subsequent market operation to revert, permanently disabling the market. The analogous pattern in this repo is the L1 `SystemConfig` `GasConfig`/`GasLimit` update path: a `GasLimitUpdate` log emitted at the configured `l1_system_config_address` is decoded and applied to `SystemConfig.gas_limit` with no non-zero validation, and that value is later used directly as the L2 block's target gas limit during block building, with no floor enforced.

### Finding Description
`GasLimitUpdate::apply` unconditionally overwrites the system config's gas limit from L1-derived data with no validation that the value is non-zero: [1](#0-0) 

This value is derived from an event log written by whoever controls the L1 `SystemConfigUpdate` topic at the rollup's configured `l1_system_config_address` (i.e., the L1 `SystemConfig` contract owner), and is folded into `SystemConfig.gas_limit` during derivation: [2](#0-1) 

The resulting `SystemConfig.gas_limit` is threaded straight into the L2 payload attributes' `gas_limit` field by both the stateful attributes builder and the standalone builder: [3](#0-2) 

At payload-building time, `BasePayloadBuilderCtx::block_gas_limit()` uses that attribute value as the effective block gas limit unless a separate builder-local `GasLimitConfig` override is set (and that local config explicitly treats `0` as "unset", i.e. it deliberately avoids this exact footgun for its own knob): [4](#0-3) [5](#0-4) 

Unlike the local `GasLimitConfig`/`BaseDAConfig` knobs (which both special-case `0` to mean "no limit configured"), the L1-derived `SystemConfig.gas_limit` has no such guard anywhere in the update-decoding or application path. If it becomes `0`, `block_gas_limit()` returns `0`, and every transaction (whose gas usage is always > 0, minimum ~21000) fails the block/transaction gas-limit check in the execution path: [6](#0-5) 

A code comment elsewhere in the harness explicitly documents this exact failure mode as a known, real hazard of a zero gas limit: "`SystemConfig::default()` has gas_limit=0 (derived Default), which causes the production payload builder to reject all transactions": [7](#0-6) 

### Impact Explanation
Once the L2 derivation pipeline processes a `GasLimit` update log setting `gas_limit = 0`, every subsequently derived/sequenced L2 block inherits `gas_limit = 0` as its target, since `SystemConfig` persists across blocks until the next update log. Because `block_gas_limit()` returns `0`, no user transaction can ever fit (`TransactionGasLimitExceeded`/`BlockGasLimitExceeded` for any nonzero gas usage), so the sequencer/builder produces empty blocks indefinitely — a full and persistent halt of user transaction inclusion on the L2 chain, equivalent in class to the market being "shut down instantly" in the original report. This is a chain/node halt of user-facing functionality reachable purely through the documented, in-scope "derivation of attacker-written L1 ... system-config data" path.

### Likelihood Explanation
Triggering this requires the actor who can emit the `SystemConfigUpdate(GasLimit)` log at `l1_system_config_address` (i.e., the entity controlling the L1 `SystemConfig` contract for this rollup configuration) to write a single update setting `gas_limit = 0`. There is no on-chain or off-chain validation rejecting a zero value anywhere in the decode (`GasLimitUpdate::try_from`) or apply (`GasLimitUpdate::apply`) path, nor in the attributes builders that copy it straight into `BasePayloadAttributes::gas_limit`. This mirrors exactly the missing lower-bound check that caused the original Portfolios.sol bug (`numPeriods = 0`).

### Recommendation
Add a validation step (either in `GasLimitUpdate::try_from`/`apply`, or in `SystemConfig::process_config_update_log`) that rejects or floors a `GasLimit` update of `0`, mirroring the "0 means unset/default" convention already used by `GasLimitConfig::set_gas_limit` and `BaseDAConfig::set_max_size`. Additionally, `BasePayloadBuilderCtx::block_gas_limit()` and `execute_best_transactions`'s `block_gas_limit` computation should defensively treat an L1-derived `gas_limit` of `0` as "use `evm_env.block_env.gas_limit`/a safe minimum" rather than propagating it verbatim, so a single malformed/malicious L1 config update cannot brick block building.

### Proof of Concept
1. Configure a rollup with `l1_system_config_address = X`.
2. As the L1 `SystemConfig` owner (or whoever can emit logs at `X` with the `SystemConfigUpdate` topic), emit a `GasConfig`/`GasLimit` update log encoding `gas_limit = 0`, following the encoding consumed by `GasLimitUpdate::try_from` (32-byte pointer=32, 32-byte length=32, 32-byte value=0): [8](#0-7) 
3. Mine the L1 block containing this log; the derivation pipeline processes it via `SystemConfig::process_config_update_log`, setting `system_config.gas_limit = 0` as shown in the existing unit test pattern: [2](#0-1) 
4. On the next L2 block(s), the attributes builder sets `payload.gas_limit = Some(0)`, which flows into `block_gas_limit()` returning `0`.
5. Any submitted transaction is rejected by `is_tx_over_limits`/`execute_best_transactions` (`TransactionGasLimitExceeded`), so every subsequent block is built empty — a persistent halt of user transaction processing until a further, unbounded corrective L1 update is issued (there is no protocol-level recovery mechanism or time-delay).

### Citations

**File:** crates/common/genesis/src/updates/gas_limit.rs (L16-21)
```rust
impl GasLimitUpdate {
    /// Applies the update to the [`SystemConfig`].
    pub const fn apply(&self, config: &mut SystemConfig) {
        config.gas_limit = self.gas_limit;
    }
}
```

**File:** crates/common/genesis/src/updates/gas_limit.rs (L23-57)
```rust
impl TryFrom<&SystemConfigLog> for GasLimitUpdate {
    type Error = GasLimitUpdateError;

    fn try_from(log: &SystemConfigLog) -> Result<Self, Self::Error> {
        let LogData { data, .. } = &log.log.data;
        if data.len() != 96 {
            return Err(GasLimitUpdateError::InvalidDataLen(data.len()));
        }

        let Ok(pointer) = <sol!(uint64)>::abi_decode_validate(&data[0..32]) else {
            return Err(GasLimitUpdateError::PointerDecodingError);
        };
        if pointer != 32 {
            return Err(GasLimitUpdateError::InvalidDataPointer(pointer));
        }

        let Ok(length) = <sol!(uint64)>::abi_decode_validate(&data[32..64]) else {
            return Err(GasLimitUpdateError::LengthDecodingError);
        };
        if length != 32 {
            return Err(GasLimitUpdateError::InvalidDataLength(length));
        }

        let Ok(gas_limit) = <sol!(uint256)>::abi_decode_validate(&data[64..]) else {
            return Err(GasLimitUpdateError::GasLimitDecodingError);
        };

        // Prevent overflows here.
        let max = U256::from(u64::MAX as u128);
        if gas_limit > max {
            return Err(GasLimitUpdateError::GasLimitDecodingError);
        }

        Ok(Self { gas_limit: U64::from(gas_limit).saturating_to::<u64>() })
    }
```

**File:** crates/common/genesis/src/system/config.rs (L521-541)
```rust
    #[test]
    fn test_system_config_update_gas_limit_log() {
        let mut system_config = SystemConfig::default();

        let update_log = Log {
            address: Address::ZERO,
            data: LogData::new_unchecked(
                vec![
                    SystemConfigUpdate::TOPIC,
                    SystemConfigUpdate::EVENT_VERSION_0,
                    GAS_LIMIT_UPDATE_TYPE,
                ],
                hex!("00000000000000000000000000000000000000000000000000000000000000200000000000000000000000000000000000000000000000000000000000000020000000000000000000000000000000000000000000000000000000000000beef").into()
            )
        };

        // Update the gas limit.
        system_config.process_config_update_log(&update_log, false).unwrap();

        assert_eq!(system_config.gas_limit, 0xbeef_u64);
    }
```

**File:** crates/consensus/service/src/standalone.rs (L124-139)
```rust
        Ok(BasePayloadAttributes {
            payload_attributes: PayloadAttributes {
                timestamp: next_l2_time,
                prev_randao: B256::ZERO,
                suggested_fee_recipient: Predeploys::SEQUENCER_FEE_VAULT,
                withdrawals: self.rollup_config.is_canyon_active(next_l2_time).then(Vec::new),
                parent_beacon_block_root: self
                    .rollup_config
                    .is_ecotone_active(next_l2_time)
                    .then_some(B256::ZERO),
                slot_number: None,
                target_gas_limit: None,
            },
            transactions: Some(transactions),
            no_tx_pool: Some(false),
            gas_limit: Some(self.system_config.gas_limit),
```

**File:** crates/builder/core/src/flashblocks/context.rs (L323-328)
```rust
    /// Returns the block gas limit to target.
    pub fn block_gas_limit(&self) -> u64 {
        self.builder_config.gas_limit_config.gas_limit().unwrap_or_else(|| {
            self.attributes().gas_limit.unwrap_or(self.evm_env.block_env.gas_limit)
        })
    }
```

**File:** crates/execution/payload/src/config.rs (L394-409)
```rust
impl GasLimitConfig {
    /// Creates a new Gas Limit configuration with the given maximum gas limit.
    pub fn new(max_gas_limit: u64) -> Self {
        let this = Self::default();
        this.set_gas_limit(max_gas_limit);
        this
    }
    /// Returns the gas limit for a transaction, if any.
    pub fn gas_limit(&self) -> Option<u64> {
        let val = self.gas_limit.load(std::sync::atomic::Ordering::Relaxed);
        if val == 0 { None } else { Some(val) }
    }
    /// Sets the gas limit for a transaction. 0 means use the default gas limit.
    pub fn set_gas_limit(&self, gas_limit: u64) {
        self.gas_limit.store(gas_limit, std::sync::atomic::Ordering::Relaxed);
    }
```

**File:** crates/execution/payload/src/builder.rs (L936-943)
```rust
        let gas_limit = builder.evm_mut().block().gas_limit();
        // If a gas limit is configured, use that limit as target if it's smaller, otherwise use
        // the block's actual gas limit.
        let block_gas_limit = self
            .builder_config
            .gas_limit_config
            .gas_limit()
            .map_or(gas_limit, |cfg| cfg.min(gas_limit));
```

**File:** actions/harness/src/providers/l2.rs (L73-77)
```rust
        // Use the rollup config's genesis system config, falling back to a harness
        // default with a non-zero gas_limit. `SystemConfig::default()` has gas_limit=0
        // (derived Default), which causes the production payload builder to reject all
        // transactions. Tests that use `RollupConfig::default()` (no explicit system
        // config) need a workable gas_limit to build blocks.
```
