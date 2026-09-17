## Finding

### Title
Division-before-multiplication precision loss in Jovian DA-footprint gas estimation understates per-tx DA footprint - (File: `crates/common/evm/src/executor/block_executor.rs`, `crates/execution/flashblocks/src/state_builder.rs`, `crates/common/l1-fees/src/params.rs`)

### Summary
Three independent implementations of the Jovian DA-footprint estimate compute `floor(estimated_compressed_size / 1e6) * da_footprint_gas_scalar` instead of `floor(estimated_compressed_size * da_footprint_gas_scalar / 1e6)`. Dividing before multiplying discards up to `scalar - 1` units of footprint gas per transaction, systematically understating the true DA footprint used to enforce the Jovian DA-footprint block/tx limit.

### Finding Description
`tx_estimated_size_fjord` (aliased as `estimate_tx_compressed_size`) returns the estimated compressed transaction size pre-scaled by `1e6` [1](#0-0) .

In the block executor, this scaled size is divided by `1e6` *before* being multiplied by the `da_footprint_gas_scalar`:
```
let encoded = tx_env.encoded_bytes().map_or_else(...).saturating_div(1_000_000);
...
Ok(encoded.saturating_mul(da_footprint_gas_scalar))
``` [2](#0-1) 

The exact same divide-then-multiply pattern is duplicated in the flashblocks pending-state builder: [3](#0-2) 

And in the engine-neutral `L1FeeParams::jovian_da_footprint` used by the EVM2 executor: [4](#0-3) [5](#0-4) 

Given `estimated_size` scaled by `1e6` with remainder `r` (`0 ≤ r < 1e6`) and `scalar` up to `65535` (a `u16`, per `DA_FOOTPRINT_GAS_SCALAR_OFFSET`/`fetch_da_footprint_gas_scalar` [6](#0-5) ):
- Correct order: `floor(estimated_size * scalar / 1e6)` — loses less than 1 unit.
- Current order: `floor(estimated_size / 1e6) * scalar` — loses `floor(r * scalar / 1e6)`, which approaches `scalar - 1` (up to 65,534 gas-equivalent units) per transaction when `r` is close to `1e6`.

This DA-footprint gas value is the resource-metering quantity enforced against the block's DA-footprint limit (mirroring `block_gas_limit`) both during block building (`ResourceLimits.da_footprint_gas_scalar` / `is_tx_over_limits`, [7](#0-6) ) and during block execution/validation (`jovian_da_footprint_estimation`, [2](#0-1) ).

### Impact Explanation
Because the truncation happens before the scalar is applied, every ordinary transaction admitted after the Jovian upgrade has its DA-footprint gas systematically undercounted by up to `scalar - 1` per transaction. Summed across all transactions in a block, this allows blocks to be built and accepted with a real DA footprint materially larger than the protocol-enforced DA-footprint budget (which is designed to bound the actual data posted to L1). This undermines the resource-metering guarantee the Jovian DA-footprint limit is meant to provide (an intentional analog to "block building and resource metering", which is explicitly in scope). The impact is systemic mis-accounting of a block-safety resource limit reachable by any unprivileged transaction sender, rather than a one-off rounding error.

### Likelihood Explanation
This triggers on every single post-Jovian transaction — no privileged access or special crafting is required beyond submitting a normal transaction whose FastLZ-estimated size is not an exact multiple of `1e6` (the common case), and the effect scales directly with the configured `da_footprint_gas_scalar` (attacker cannot control the scalar, but its magnitude, up to `65535`, determines how large the shortfall can be per transaction). It is deterministic and always reproducible, not a probabilistic/edge-case bug.

### Recommendation
Reorder the arithmetic to multiply before dividing in all three sites, consistent with `calculate_tx_l1_cost_fjord`/`data_gas` which already do multiply-then-divide correctly:
- `crates/common/evm/src/executor/block_executor.rs::jovian_da_footprint_estimation`
- `crates/execution/flashblocks/src/state_builder.rs::jovian_da_footprint_estimation`
- `crates/common/l1-fees/src/params.rs::jovian_da_footprint`

i.e. compute `estimate_tx_compressed_size(...).saturating_mul(da_footprint_gas_scalar).saturating_div(1_000_000)` instead of dividing first.

### Proof of Concept
Using `L1FeeParams::jovian_da_footprint` with `da_footprint_gas_scalar = 65535` and an input whose `tx_estimated_size_fjord` returns e.g. `100_999_999` (i.e., `estimated_size / 1e6 = 100` with remainder `999_999`):
- Current (buggy) order: `100_999_999 / 1_000_000 = 100`; `100 * 65535 = 6,553,500`.
- Correct order: `100_999_999 * 65535 / 1_000_000 = 6,619,497` (approx).
- Shortfall: `65,997` gas-equivalent units understated for this single transaction — repeatable for every transaction in every post-Jovian block, cumulatively allowing the block's real DA footprint to exceed its intended limit. [8](#0-7)

### Citations

**File:** crates/common/flz/src/flz.rs (L25-35)
```rust
/// Calculate the estimated compressed transaction size in bytes, scaled by 1e6.
/// This value is computed based on the following formula:
/// max(minTransactionSize, intercept + fastlzCoef*fastlzSize)
pub fn tx_estimated_size_fjord(input: &[u8]) -> u64 {
    let fastlz_size = flz_compress_len(input) as u64;

    fastlz_size
        .saturating_mul(L1_COST_FASTLZ_COEF)
        .saturating_sub(L1_COST_INTERCEPT)
        .max(MIN_TX_SIZE_SCALED)
}
```

**File:** crates/common/evm/src/executor/block_executor.rs (L127-150)
```rust
    fn jovian_da_footprint_estimation(
        &mut self,
        tx_env: &E::Tx,
        tx: impl RecoveredTx<R::Transaction>,
    ) -> Result<u64, BlockExecutionError> {
        // Try to use the enveloped tx if it exists, otherwise use the encoded 2718 bytes
        let encoded = tx_env
            .encoded_bytes()
            .map_or_else(
                || estimate_tx_compressed_size(tx.tx().encoded_2718().as_ref()),
                |encoded| estimate_tx_compressed_size(encoded),
            )
            .saturating_div(1_000_000);

        // Load the L1 block contract into the cache. If the L1 block contract is not pre-loaded the
        // database will panic when trying to fetch the DA footprint gas scalar.
        self.evm.db_mut().basic(Predeploys::L1_BLOCK_INFO).map_err(BlockExecutionError::other)?;

        let da_footprint_gas_scalar = L1BlockInfo::fetch_da_footprint_gas_scalar(self.evm.db_mut())
            .map_err(BlockExecutionError::other)?
            .into();

        Ok(encoded.saturating_mul(da_footprint_gas_scalar))
    }
```

**File:** crates/execution/flashblocks/src/state_builder.rs (L264-287)
```rust
    fn jovian_da_footprint_estimation(
        &mut self,
        tx_env: &Recovered<BaseTxEnvelope>,
    ) -> Result<u64, StateProcessorError> {
        // Try to use the enveloped tx if it exists, otherwise use the encoded 2718 bytes
        let encoded = estimate_tx_compressed_size(tx_env.into_encoded().encoded_bytes())
            .saturating_div(1_000_000);

        // Load the L1 block contract into the cache. If the L1 block contract is not pre-loaded the
        // database will panic when trying to fetch the DA footprint gas scalar.
        self.evm.db_mut().basic(Predeploys::L1_BLOCK_INFO).map_err(|err| {
            StateProcessorError::Execution(ExecutionError::DaFootprintEstimation(err.to_string()))
        })?;

        let da_footprint_gas_scalar = L1BlockInfo::fetch_da_footprint_gas_scalar(self.evm.db_mut())
            .map_err(|err| {
                StateProcessorError::Execution(ExecutionError::DaFootprintEstimation(
                    err.to_string(),
                ))
            })?
            .into();

        Ok(encoded.saturating_mul(da_footprint_gas_scalar))
    }
```

**File:** crates/common/l1-fees/src/params.rs (L84-98)
```rust
    /// Calculates the Jovian DA-footprint gas for posting the enveloped transaction bytes `input`.
    ///
    /// The footprint is the FastLZ-estimated compressed size (scaled by `1e6`, as
    /// [`tx_estimated_size_fjord`] returns) divided back down by `1e6` and multiplied by the
    /// DA-footprint gas scalar. Returns zero when no scalar is set (pre-Jovian or unconfigured).
    /// Mirrors the reference `jovian_da_footprint_estimation`.
    pub fn jovian_da_footprint(&self, input: &[u8]) -> u64 {
        let Some(scalar) = self.da_footprint_gas_scalar else {
            return 0;
        };
        U256::from(tx_estimated_size_fjord(input))
            .wrapping_div(U256::from(1_000_000u64))
            .saturating_mul(scalar)
            .saturating_to::<u64>()
    }
```

**File:** crates/common/evm2/src/executor.rs (L281-296)
```rust
        // are exempt. Accumulated below into `da_footprint_used` (surfaced as `blob_gas_used`).
        let is_jovian = (self.evm.config_spec_id().upgrade() as u8) >= (BaseUpgrade::Jovian as u8);
        let tx_da_footprint = if is_jovian && !is_deposit {
            let enveloped = tx.enveloped().map(|bytes| bytes.as_ref()).unwrap_or_default();
            let footprint = self.evm.block().ext.jovian_da_footprint(enveloped);
            let available = block_gas_limit.saturating_sub(self.da_footprint_used);
            if footprint > available {
                return Err(HandlerError::external(DaFootprintAboveGasLimit {
                    transaction_da_footprint: footprint,
                    available_block_da_footprint: available,
                }));
            }
            footprint
        } else {
            0
        };
```

**File:** crates/common/evm/src/l1block.rs (L96-108)
```rust
    /// Fetch the DA footprint gas scalar from the database.
    pub fn fetch_da_footprint_gas_scalar<DB: Database>(db: &mut DB) -> Result<u16, DB::Error> {
        let da_footprint_gas_scalar_slot = db
            .storage(Predeploys::L1_BLOCK_INFO, Self::DA_FOOTPRINT_GAS_SCALAR_SLOT)?
            .to_be_bytes::<32>();

        // Extract the first 2 bytes directly as a u16 in big-endian format
        let bytes = [
            da_footprint_gas_scalar_slot[Self::DA_FOOTPRINT_GAS_SCALAR_OFFSET],
            da_footprint_gas_scalar_slot[Self::DA_FOOTPRINT_GAS_SCALAR_OFFSET + 1],
        ];
        Ok(u16::from_be_bytes(bytes))
    }
```

**File:** crates/builder/core/src/execution.rs (L287-298)
```rust
        // Post Jovian: the tx DA footprint must be less than the block gas limit (protocol-enforced)
        if let Some(da_footprint_gas_scalar) = limits.da_footprint_gas_scalar {
            let tx_da_footprint =
                total_da_bytes_used.saturating_mul(da_footprint_gas_scalar as u64);
            if tx_da_footprint > limits.block_da_footprint_limit.unwrap_or(limits.block_gas_limit) {
                return Err(TxnExecutionError::DAFootprintLimitExceeded {
                    total_da_used: total_da_bytes_used,
                    tx_da_size: tx.da_size,
                    da_footprint: tx_da_footprint,
                });
            }
        }
```
