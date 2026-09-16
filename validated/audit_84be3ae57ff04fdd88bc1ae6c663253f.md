## Analog Found

### Title
Bundle metering hardcodes a legacy 2-second block time, producing wrong simulated next-block timestamps once Denim's 200ms cadence is active - (File: `crates/execution/metering/src/meter.rs`)

### Summary
`crates/execution/metering/src/meter.rs` hardcodes a `BLOCK_TIME` constant of 2 seconds and uses it to derive the "next block" timestamp fed into EVM/spec selection for bundle metering. [1](#0-0)  This mirrors the reported Goldigovernor bug class: a fixed block-time assumption baked into a security/economically-relevant calculation that no longer matches the chain's actual, configurable block cadence.

### Finding Description
`meter_bundle` computes the simulated next-block timestamp as `header.timestamp() + BLOCK_TIME` where `BLOCK_TIME` is a hardcoded constant equal to `2`: [2](#0-1)  This timestamp is passed into `BaseNextBlockEnvAttributes` and then into `evm_config.next_evm_env(header, &attributes)`, which derives the EVM's hardfork `spec` from that timestamp: [3](#0-2) 

However, Base's actual L2 block cadence is not a fixed 2-second constant across the chain's lifetime. The rollup config models a "Denim" upgrade after which block production shifts from the legacy 2-second cadence to a fixed 200ms subsecond interval (`NATIVE_SUBSECOND_BLOCK_INTERVAL_MILLIS = 200`), and timestamp math throughout the rest of the codebase (`RollupConfig::l2_block_timestamp_millis`, `denim_activation_block_number`, the sequencer's `block_seal_target`) is written to account for this transition: [4](#0-3) [5](#0-4) [6](#0-5) 

The metering module's `BLOCK_TIME` constant is not derived from `RollupConfig` and does not account for Denim activation, so once Denim is active the simulated "next block" timestamp used for bundle metering overshoots the real next-block timestamp by roughly 10x (2000ms assumed vs. the real ~200ms cadence). Since hardfork/feature activation elsewhere in the codebase is timestamp-gated (e.g. `is_denim_active`, and downstream consumers keyed off `BaseUpgrade`/`BaseSpecId`), an inflated simulated timestamp can push the metering EVM environment across upgrade boundaries prematurely relative to the real chain state, causing `next_evm_env`'s selected `spec` (and any spec-dependent fee/gas-cost tables used by `metered_opcodes.clone().for_spec(spec)`) to diverge from what will actually be used when the transaction is truly included on-chain.

### Impact Explanation
Bundle metering output (`MeterBundleOutput`/opcode gas breakdown) feeds the builder's resource-metering admission/exclusion pipeline, which is explicitly used to throttle or reject transactions from blocks (`crates/execution/payload/src/resource_metering.rs`, `crates/builder/core/src/execution.rs`). [7](#0-6)  If metering simulates against the wrong hardfork spec/gas-cost table because of an incorrect assumed block time, transactions can be incorrectly priced, incorrectly excluded, or incorrectly admitted relative to their real on-chain cost, degrading the accuracy of resource-based transaction admission around an upgrade boundary. This is a block-building/resource-metering correctness issue, not a consensus-breaking one, since the actual on-chain execution env is still derived correctly elsewhere; the risk is confined to the simulation path.

### Likelihood Explanation
The divergence is directly proportional to how close `header.timestamp()` is to the Denim activation boundary — for a narrow window around activation, the fixed 2-second offset used by `BLOCK_TIME` can straddle the boundary that the real ~200ms cadence would not, making the bug reachable simply by metering a bundle near that timestamp. It requires no privileged access — bundle submission for metering is reachable by anonymous RPC/bundle senders — but it only manifests transiently around the Denim upgrade boundary, and doesn't corrupt canonical state.

### Recommendation
Derive the metering "next block" timestamp from `RollupConfig`/`BaseUpgrade`-aware helpers (e.g. mirroring `RollupConfig::l2_block_timestamp_millis`/`is_denim_active`) rather than a hardcoded `const BLOCK_TIME: u64 = 2`, so metering's simulated spec/timestamp tracks the chain's actual configured cadence across the Denim transition.

### Proof of Concept
1. Configure a chain with Denim activation at timestamp `T`.
2. Submit a bundle for metering whose parent header timestamp is `T - 1` (just before Denim activates under the real 200ms cadence, so the true next block timestamp would be `T - 0.8`, i.e. still pre-Denim).
3. `meter_bundle` computes `timestamp = header.timestamp() + BLOCK_TIME = T - 1 + 2 = T + 1`, which is past `T` and therefore evaluated as **post-Denim**, even though the real next produced block would still be pre-Denim.
4. `evm_config.next_evm_env` selects the post-Denim spec/gas parameters for metering, producing gas/fee estimates inconsistent with the transaction's actual on-chain execution environment, which can cause the resource-metering pipeline to admit/reject the transaction incorrectly relative to the real chain.

### Citations

**File:** crates/execution/metering/src/meter.rs (L30-30)
```rust
const BLOCK_TIME: u64 = 2; // 2 seconds per block
```

**File:** crates/execution/metering/src/meter.rs (L717-726)
```rust
    // Set up next block attributes
    let timestamp = header.timestamp() + BLOCK_TIME;
    let attributes = BaseNextBlockEnvAttributes {
        timestamp,
        suggested_fee_recipient: header.beneficiary(),
        prev_randao: header.mix_hash().unwrap_or_else(B256::random),
        gas_limit: header.gas_limit(),
        parent_beacon_block_root: header.parent_beacon_block_root(),
        extra_data: header.extra_data().clone(),
    };
```

**File:** crates/execution/metering/src/meter.rs (L737-740)
```rust
        let evm_config = BaseEvmConfig::base(chain_spec);
        let evm_env = evm_config.next_evm_env(header, &attributes)?;
        let spec = evm_env.cfg_env.spec;
        let metered_opcodes = Arc::new(metered_opcodes.clone().for_spec(spec));
```

**File:** crates/common/genesis/src/rollup.rs (L392-427)
```rust
    /// Returns the deterministic timestamp of an L2 block in milliseconds.
    ///
    /// Before Denim activation, this matches the legacy whole-second schedule exactly.
    /// After Denim activation, this advances by a fixed 200ms cadence from the activation block.
    ///
    /// `block_number` is an absolute L2 block number; it is measured relative to the L2 genesis
    /// block number (`self.genesis.l2.number`), which is non-zero for chains whose L2 genesis
    /// was anchored at a later block.
    pub fn l2_block_timestamp_millis(&self, block_number: u64) -> u64 {
        let blocks_since_genesis = block_number.saturating_sub(self.genesis.l2.number);

        let legacy_seconds = self
            .genesis
            .l2_time
            .saturating_add(blocks_since_genesis.saturating_mul(self.block_time));
        let legacy_millis = legacy_seconds.saturating_mul(1_000);

        let Some(denim_activation_block) = self.denim_activation_block_number() else {
            return legacy_millis;
        };

        if blocks_since_genesis < denim_activation_block {
            return legacy_millis;
        }

        let denim_activation_seconds = self
            .genesis
            .l2_time
            .saturating_add(denim_activation_block.saturating_mul(self.block_time));
        let denim_activation_full_millis = denim_activation_seconds.saturating_mul(1_000);
        denim_activation_full_millis.saturating_add(
            blocks_since_genesis
                .saturating_sub(denim_activation_block)
                .saturating_mul(Self::NATIVE_SUBSECOND_BLOCK_INTERVAL_MILLIS),
        )
    }
```

**File:** crates/common/genesis/src/rollup.rs (L486-487)
```rust
    /// The fixed cadence once subsecond blocks activates.
    pub const NATIVE_SUBSECOND_BLOCK_INTERVAL_MILLIS: u64 = 200;
```

**File:** crates/consensus/service/src/actors/sequencer/actor.rs (L334-352)
```rust
    ///
    /// Pre-Denim blocks compensate for the previous seal duration, capped at half the
    /// block interval so one slow seal cannot collapse the next build window to zero and
    /// trigger a fat/thin block oscillation.
    pub(super) fn block_seal_target(
        &self,
        block_number: u64,
        last_seal_duration: Duration,
    ) -> SystemTime {
        let target = UNIX_EPOCH
            + Duration::from_millis(self.rollup_config.l2_block_timestamp_millis(block_number));
        if self.rollup_config.is_denim_active(self.rollup_config.l2_block_timestamp(block_number)) {
            let interval =
                Duration::from_millis(RollupConfig::NATIVE_SUBSECOND_BLOCK_INTERVAL_MILLIS);
            return target - interval.saturating_sub(self.seal_offset);
        }
        let block_interval = Duration::from_secs(self.rollup_config.block_time);
        target - last_seal_duration.min(block_interval / 2)
    }
```

**File:** crates/execution/payload/src/resource_metering.rs (L1-16)
```rust
//! Versioned resource-metering schedules and their transaction-cost evaluator.
//!
//! Resource metering reweights named observations into independent resource-unit
//! dimensions. Simulated `meterBundle` data is a candidate pre-filter.
//! Committed payload usage is accounted from executed observations when they
//! exist: actual gas used and net post-state effects such as
//! [`ResourceSample::STATE_NEW_STORAGE_SLOT`] replace simulated `STATE_*`
//! rows, while other simulated opcode and precompile rows are kept.
//! Production execution does not attach opcode bags. Throttling excludes a
//! transaction when an enforced dimension exceeds a budget. A dimension with
//! [`ResourceMeteringDimension::dry_run`] set is observed without excluding.
//! Block-scope excludes skip only the current payload scan. Transaction-scope
//! excludes (simulated or executed) are permanent pool evictions: the
//! transaction cannot fit any block. Neither changes protocol gas, fees, or
//! validity.

```
