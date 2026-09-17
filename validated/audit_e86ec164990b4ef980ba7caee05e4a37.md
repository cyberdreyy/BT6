### Title
Rounding-down in Jovian DA-footprint estimation systematically underestimates per-tx DA gas, allowing block/DA-footprint budgets to be exceeded - (`crates/common/l1-fees/src/params.rs`, `crates/common/evm/src/executor/block_executor.rs`, `crates/execution/flashblocks/src/state_builder.rs`)

### Summary
The Jovian DA-footprint gas calculation truncates (floors) the FastLZ-scaled compressed size *before* multiplying by the DA-footprint gas scalar, instead of multiplying first and dividing last. This early-division rounding pattern is the same bug class as the reported `_calc_min_amount_out()` issue (dividing before scaling causes systematic loss of precision), except here the truncation is baked into a value that gates block-gas and DA admission control rather than a pure user-slippage parameter.

### Finding Description
`L1FeeParams::jovian_da_footprint` computes the estimated DA footprint gas for a transaction: [1](#0-0) 

`tx_estimated_size_fjord` returns the FastLZ compressed-size estimate **scaled by 1e6**: [2](#0-1) 

The correct/precise value would be `(estimated_size_scaled * scalar) / 1_000_000`. Instead, the code does `estimated_size_scaled.wrapping_div(1_000_000)` **first**, then multiplies by `scalar`. This early division floors the sub-1e6 fractional part of the compressed-size estimate before the scalar is applied, so any precision that would have been preserved by multiplying first is discarded. The same division-before-multiplication pattern is repeated in the sibling implementations used by block execution and the flashblocks pending-state builder: [3](#0-2) [4](#0-3) 

All three sites floor the compressed-size estimate to whole bytes before multiplying by `da_footprint_gas_scalar`, whereas `data_gas_fjord` in the same module does it the mathematically correct way (multiply, then divide): [5](#0-4) 

The Jovian DA footprint (surfaced as `blob_gas_used` in the block header, per the original audit-report analog of "consecutive division causing rounding loss") is used to gate:
- whether a transaction fits the remaining block-gas budget during block building (`is_tx_over_limits` and `jovian_da_footprint_estimation` in `block_executor.rs` and `state_builder.rs`),
- the EVM2 executor's hard rejection path `DaFootprintAboveGasLimit`: [6](#0-5) 

### Impact Explanation
Because the per-byte estimate is floored to an integer number of compressed bytes before multiplying by the scalar, the computed DA footprint is always `<=` the mathematically precise value, i.e. it is a systematic underestimate (never an overestimate). For scalars that are not multiples of 1,000,000-friendly values, this rounding-down accumulates across every transaction in a block. The DA-footprint check in `is_tx_over_limits`/`DaFootprintAboveGasLimit` exists specifically to bound the block's true data-availability cost (mirrored into `blob_gas_used`, feeding batcher/L1 posting cost accounting) to the block gas limit. A systematic underestimate means the block builder can admit more raw transaction bytes than the intended DA/gas budget allows, producing blocks whose actual (uncompressed lower-bound) DA footprint exceeds the configured limit. This is a resource-accounting/limit-enforcement correctness bug in block building and the fault/derivation-adjacent gas metering path (the same `jovian_da_footprint` math is shared with `L1FeeParams`, which is also consumed by consensus/derivation code, per `crates/consensus/protocol/src/info/variant.rs`), so a systematic bias could cause a block gas-limit accounting divergence between builder-side estimation and any downstream code relying on exact DA-footprint arithmetic.

### Likelihood Explanation
This triggers on every Jovian-era transaction once `da_footprint_gas_scalar` is set (which is normal post-Jovian operation) — it is not a rare edge case; it is a per-transaction rounding bias in the hot execution/block-building path reachable by any ordinary transaction sender.

### Recommendation
Reorder the arithmetic to multiply before dividing, matching `data_gas_fjord`'s pattern:
```rust
pub fn jovian_da_footprint(&self, input: &[u8]) -> u64 {
    let Some(scalar) = self.da_footprint_gas_scalar else { return 0 };
    U256::from(tx_estimated_size_fjord(input))
        .saturating_mul(scalar)
        .wrapping_div(U256::from(1_000_000u64))
        .saturating_to::<u64>()
}
```
Apply the equivalent reordering in `block_executor.rs::jovian_da_footprint_estimation` and `state_builder.rs::jovian_da_footprint_estimation`, which currently do `encoded.saturating_div(1_000_000)` before `saturating_mul(da_footprint_gas_scalar)`.

### Proof of Concept
Given `tx_estimated_size_fjord(input) = 1_999_999` (i.e., 1.999999 compressed bytes scaled by 1e6) and `da_footprint_gas_scalar = 3`:
- Correct (multiply-first): `1_999_999 * 3 / 1_000_000 = 5` (floor of 5.999997).
- Current (divide-first, as in `jovian_da_footprint`/`jovian_da_footprint_estimation`): `1_999_999 / 1_000_000 = 1`; `1 * 3 = 3`.

The current implementation returns `3` instead of the precise `5`, an understatement of ~40% for this input, and this error compounds over every transaction admitted into a block, which is exactly the "loss of precision from performing division before the final scaling multiplication" bug class described in the source report.

### Citations

**File:** crates/common/l1-fees/src/params.rs (L90-98)
```rust
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

**File:** crates/common/flz/src/flz.rs (L20-23)
```rust
pub fn data_gas_fjord(input: &[u8]) -> u64 {
    let estimated_size = tx_estimated_size_fjord(input);
    estimated_size.saturating_mul(NON_ZERO_BYTE_COST).wrapping_div(1_000_000)
}
```

**File:** crates/common/flz/src/flz.rs (L28-35)
```rust
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
