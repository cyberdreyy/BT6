### Title
Divide-before-multiply in Jovian DA-footprint gas estimation causes systematic under-metering — (File: `crates/common/evm/src/executor/block_executor.rs`, also `crates/execution/flashblocks/src/state_builder.rs`)

### Summary

### Finding Description
`jovian_da_footprint_estimation` computes the per-transaction DA-footprint gas that is charged against the block's DA-footprint budget (`blob_gas_used`) for Jovian blocks. The formula per the OP-stack spec should be `estimatedSize(scaled by 1e6) * daFootprintGasScalar / 1e6`, i.e. multiply first, then divide — the same pattern correctly used elsewhere in the codebase, e.g. `L1FeeParams::calculate_tx_l1_cost_fjord` at [1](#0-0)  which does `.saturating_mul(l1_fee_scaled).wrapping_div(...)`.

Instead, `jovian_da_footprint_estimation` divides the estimated compressed size by `1_000_000` *before* multiplying by the scalar: [2](#0-1) 

The same divide-before-multiply pattern is duplicated in the flashblocks pending-state builder: [3](#0-2) 

`estimate_tx_compressed_size` (built on `tx_estimated_size_fjord`, see [4](#0-3) ) returns a size scaled by `1e6` with a floor of `MIN_TX_SIZE_SCALED = 100_000_000` (i.e., always ≥ 100 after truncation). Truncating (`saturating_div(1_000_000)`) before multiplying by `da_footprint_gas_scalar` (a `u16`, max `65535`, default `400` per [5](#0-4) ) discards the fractional remainder of the size division, and that discarded fraction is never recovered because the multiply happens afterward. Correct order (`size * scalar / 1e6`) would retain that precision.

### Impact Explanation
The resulting `tx_da_footprint` value is used to:
- gate per-transaction/per-block admission against `da_footprint_available` in the block executor ( [6](#0-5) ),
- gate admission during sequencer block building ( [7](#0-6) , [8](#0-7) ),
- populate the header's `blob_gas_used` field, i.e. the block's provable DA-footprint accounting ( [9](#0-8) ).

Because the truncation happens before scaling, every transaction's metered DA-footprint gas can be under-counted by up to `scalar - 1` gas units versus the spec-correct value. An attacker crafting calldata whose FastLZ-estimated size lands just below a `1e6`-scaled boundary can consistently maximize this loss across many transactions to pack more effective DA usage into a block than the configured `da_footprint_gas_scalar` budget intends, understating the true DA cost recorded in `blob_gas_used`. This is a deterministic, node-computed value (all conforming nodes replicate the same erroneous arithmetic), so it does not itself cause a chain split, but it does cause the block's provable DA-footprint accounting/resource metering to be systematically wrong relative to the intended protocol formula, and enables under-priced admission of DA-heavy transactions relative to the sequencer's/protocol's intended budget.

### Likelihood Explanation
Reachable by any ordinary transaction sender: crafting calldata size and content to land at unfavorable rounding boundaries requires no special privilege, and the DA-footprint path is exercised on every non-deposit transaction post-Jovian in both the canonical block executor and the flashblocks pending-state builder.

### Recommendation
Reorder the arithmetic to multiply before dividing, matching the pattern already used in `L1FeeParams::calculate_tx_l1_cost_fjord`/`jovian_da_footprint`: compute `estimate_tx_compressed_size(...).saturating_mul(da_footprint_gas_scalar).wrapping_div(1_000_000)` instead of dividing by `1_000_000` first. Apply the fix consistently in both `crates/common/evm/src/executor/block_executor.rs::jovian_da_footprint_estimation` and `crates/execution/flashblocks/src/state_builder.rs::jovian_da_footprint_estimation`, and add a regression test asserting the result matches `L1FeeParams::jovian_da_footprint` (`crates/common/l1-fees/src/params.rs`) for the same inputs, since that helper already implements the correct multiply-then-divide order.

### Proof of Concept
Not independently executed; the arithmetic discrepancy is demonstrable analytically:
- Let `estimated_size = 100_999_999` (just under the next whole-unit boundary after scaling) and `da_footprint_gas_scalar = 400`.
- Correct order: `100_999_999 * 400 / 1_000_000 = 40_399` (using integer division).
- Buggy order (current code): `100_999_999 / 1_000_000 = 100`; `100 * 400 = 40_000`.
- Difference: `399` gas units under-counted for this single transaction, reproducible deterministically for any size not evenly divisible by `1_000_000`, and scalable to the full `scalar - 1` loss per transaction across many transactions in a block.

### Citations

**File:** crates/common/l1-fees/src/params.rs (L146-159)
```rust
    /// Post-Fjord L1 cost:
    /// `estimatedSize * (baseFeeScalar*l1BaseFee*16 + blobFeeScalar*l1BlobBaseFee) / 1e12`.
    pub fn calculate_tx_l1_cost_fjord(&self, input: &[u8]) -> U256 {
        if Self::is_fee_exempt(input) {
            return U256::ZERO;
        }
        let l1_fee_scaled = self.calculate_l1_fee_scaled_ecotone();
        if l1_fee_scaled.is_zero() {
            return U256::ZERO;
        }
        U256::from(tx_estimated_size_fjord(input))
            .saturating_mul(l1_fee_scaled)
            .wrapping_div(U256::from(1_000_000_000_000u64))
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

**File:** crates/common/evm/src/executor/block_executor.rs (L230-252)
```rust
        let da_footprint_used = if self
            .spec
            .is_jovian_active_at_timestamp(self.evm.block().timestamp().saturating_to())
            && !is_deposit
        {
            let da_footprint_available =
                self.evm.block().gas_limit().saturating_sub(self.da_footprint_used);

            let tx_da_footprint = self.jovian_da_footprint_estimation(&tx_env, &tx)?;

            if tx_da_footprint > da_footprint_available {
                return Err(BlockExecutionError::Validation(BlockValidationError::Other(
                    Box::new(BaseBlockExecutionError::TransactionDaFootprintAboveGasLimit {
                        transaction_da_footprint: tx_da_footprint,
                        available_block_da_footprint: da_footprint_available,
                    }),
                )));
            }

            tx_da_footprint
        } else {
            0
        };
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

**File:** crates/consensus/protocol/src/info/jovian.rs (L72-75)
```rust
    /// The default DA footprint gas scalar
    /// <https://github.com/ethereum-optimism/specs/blob/main/specs/protocol/jovian/l1-attributes.md#overview>
    pub const DEFAULT_DA_FOOTPRINT_GAS_SCALAR: u16 = 400;

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

**File:** crates/execution/payload/src/builder.rs (L700-710)
```rust

        // Post Jovian: the tx DA footprint must be less than the block gas limit
        if let Some(da_footprint_gas_scalar) = da_footprint_gas_scalar {
            let tx_da_footprint =
                total_da_bytes_used.saturating_mul(da_footprint_gas_scalar as u64);
            if tx_da_footprint > block_gas_limit {
                return true;
            }
        }

        self.cumulative_gas_used.saturating_add(tx_reserved_gas) > block_gas_limit
```

**File:** crates/builder/core/src/flashblocks/context.rs (L345-361)
```rust
    /// Returns the blob fields for the header.
    ///
    /// This will return the cumulative DA bytes * scalar after Jovian
    /// after Ecotone, this will always return Some(0) as blobs aren't supported
    /// pre Ecotone, these fields aren't used.
    pub fn blob_fields(&self, info: &ExecutionInfo) -> (Option<u64>, Option<u64>) {
        if self.is_jovian_active() {
            let scalar =
                info.da_footprint_scalar.expect("Scalar must be defined for Jovian blocks");
            let result = info.cumulative_da_bytes_used * scalar as u64;
            (Some(0), Some(result))
        } else if self.is_ecotone_active() {
            (Some(0), Some(0))
        } else {
            (None, None)
        }
    }
```
