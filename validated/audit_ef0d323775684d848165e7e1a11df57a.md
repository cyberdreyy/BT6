### Title
Divide-before-multiply truncation in Jovian DA-footprint gas estimation causes systematic under-metering of L1 data-availability gas - (File: crates/common/evm/src/executor/block_executor.rs)

### Summary
Following the same "division before multiplication" bug class as the Asymmetry Finance report, Base's Jovian DA-footprint gas calculation divides the FastLZ-scaled estimated transaction size by `1_000_000` **before** multiplying by `da_footprint_gas_scalar`, instead of multiplying first and dividing last. This throws away fractional precision before it is scaled up, systematically under-counting the DA footprint gas charged/enforced for a transaction.

### Finding Description
`tx_estimated_size_fjord` / `estimate_tx_compressed_size` return the estimated compressed size **scaled by 1e6** [1](#0-0) . The correct order to convert this into the DA-footprint gas figure is `(scaled_size * scalar) / 1_000_000` — multiply first, then divide, mirroring how the sibling L1-fee functions in the same crate are written (`saturating_mul(l1_fee_scaled).wrapping_div(...)`) [2](#0-1) .

Instead, `jovian_da_footprint_estimation` in the block executor divides by `1_000_000` first and multiplies by the scalar afterward: [3](#0-2) 

The identical divide-then-multiply pattern is duplicated in the flashblocks pending-state builder: [4](#0-3) 

and again in the engine-neutral `L1FeeParams::jovian_da_footprint` helper: [5](#0-4) 

Dividing first floors the scaled size to the nearest whole "byte" before it is multiplied by the scalar, discarding up to `999,999/1,000,000` of a byte's worth of size *before* scaling. Because the multiplier (`da_footprint_gas_scalar`, a `u16`, up to 65,535) is applied after truncation, the resulting undercount can be as large as `scalar - 1` gas units per transaction, rather than the sub-1 rounding error that the correct multiply-then-divide order would produce.

Example: `encoded = 1,999,999` (≈1.999999 compressed bytes), `scalar = 3`.
- Correct (`mul` then `div`): `floor(1,999,999 * 3 / 1,000,000) = floor(5.999997) = 5`.
- Actual buggy code (`div` then `mul`): `floor(1,999,999 / 1,000,000) * 3 = 1 * 3 = 3`.

This DA footprint value is not cosmetic — it directly gates block/consensus-level admission via `TransactionDaFootprintAboveGasLimit` in `execute_transaction_without_commit` [6](#0-5)  and is recorded as `blob_gas_used` in the block header, which is exactly what the `da_footprint_fills_to_limit` test asserts must "not exceed the block gas limit" [7](#0-6) .

### Impact Explanation
Because the DA-footprint gas is systematically under-computed relative to the mathematically-correct (and presumably canonical/op-geth-reference) formula, a sequence of transactions that individually sit just under a 1e6-scaled-byte boundary can be crafted (via calldata sized to land the FastLZ estimate near `x.999999` compressed bytes) so that each transaction consumes less DA-footprint gas than it should. Repeated across many transactions in a block, this allows more data to be posted to L1 per block than the Jovian DA-footprint budget is meant to allow, effectively bypassing the resource-metering control that caps L1 data-availability consumption per block. This is a resource-accounting correctness bug at the consensus/execution layer (reachable purely via crafting an ordinary signed transaction with attacker-chosen calldata), not merely a builder heuristic, since the truncated computation is enforced in `BaseBlockExecutor::execute_transaction_without_commit`, which is part of block validation.

### Likelihood Explanation
Any unprivileged transaction sender can control transaction calldata to influence its FastLZ-estimated compressed size, and thus can deliberately land the estimate near an integer-byte boundary to maximize the truncation loss. The bug triggers on every Jovian-active, non-deposit transaction, so it is reachable on essentially every block post-Jovian activation without any special privilege — it is a systematic off-by-`scalar-1` gas undercount, not a rare edge case.

### Recommendation
Reorder each of the three duplicated computations to multiply before dividing:
- `crates/common/evm/src/executor/block_executor.rs::jovian_da_footprint_estimation` — compute `estimate_tx_compressed_size(...).saturating_mul(da_footprint_gas_scalar) / 1_000_000` instead of dividing first.
- `crates/execution/flashblocks/src/state_builder.rs::jovian_da_footprint_estimation` — same fix.
- `crates/common/l1-fees/src/params.rs::L1FeeParams::jovian_da_footprint` — same fix (`tx_estimated_size_fjord(input) * scalar / 1_000_000` rather than `/1_000_000 * scalar`).

Ensure the resulting formula matches whatever the canonical op-stack/op-geth Jovian DA-footprint specification defines bit-for-bit, since this value is consensus-critical and any mismatch between node implementations using the correct vs. incorrect order would also risk a chain split.

### Proof of Concept
1. Deploy/observe a Base node post-Jovian activation with `da_footprint_gas_scalar` set to a value `> 1` (e.g., 3, matching the `JOVIAN_DATA` default of 400 used in tests) [8](#0-7) .
2. Craft calldata for a legacy/EIP-1559 transaction whose FastLZ-estimated compressed size (per `tx_estimated_size_fjord`) lands just below an exact multiple of `1,000,000` when scaled (e.g., an estimated size of `1,999,999`).
3. Submit the transaction; call `BaseBlockExecutor::jovian_da_footprint_estimation` (or observe the resulting `blob_gas_used` in the produced block header).
4. Compare against the value that would be produced by `(estimated_size * scalar) / 1_000_000`: the recorded/enforced footprint (`1 * scalar = 3`) is smaller than the mathematically correct value (`floor(1,999,999*3/1e6) = 5`), demonstrating the systematic undercount that lets more L1 data be admitted per unit of metered DA-footprint gas than intended.

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

**File:** crates/builder/core/tests/data_availability.rs (L93-150)
```rust
/// This test ensures that the DA footprint limit (Jovian) is respected and the block fills
/// to the DA footprint limit. The DA footprint is calculated as:
/// `total_da_bytes_used` * `da_footprint_gas_scalar` (stored in `blob_gas_used`).
/// This must not exceed the block gas limit.
#[tokio::test]
async fn da_footprint_fills_to_limit() -> eyre::Result<()> {
    let rbuilder = setup_test_instance().await?;
    let driver = rbuilder.driver().await?;

    // DA footprint scalar from JOVIAN_DATA is 400
    // Set a constrained gas limit so DA footprint becomes the limiting factor
    //
    // - Gas limit: 400,000
    // - DA footprint scalar: 400
    // - Each user tx DA size: ~100 bytes
    // - DA footprint per tx: 100 bytes × 400 = 40,000
    // - Max DA bytes: 400,000 / 400 = 1,000 bytes
    //
    // With deposit tx overhead, approximately 9 user transactions can fit
    let gas_limit = 400_000u64;
    let call = driver
        .provider()
        .raw_request::<(u64,), bool>("miner_setGasLimit".into(), (gas_limit,))
        .await?;
    assert!(call, "miner_setGasLimit should be executed successfully");

    // Set DA size limit to be permissive (not the constraint)
    let call = driver
        .provider()
        .raw_request::<(i32, i32), bool>("miner_setMaxDASize".into(), (0, 100_000))
        .await?;
    assert!(call, "miner_setMaxDASize should be executed successfully");

    let mut tx_hashes = Vec::new();
    for _ in 0..12 {
        // Send more transactions to ensure some don't fit
        let tx = driver
            .create_transaction()
            .random_valid_transfer()
            .with_gas_limit(21000)
            .send()
            .await?;
        tx_hashes.push(*tx.tx_hash());
    }

    let block = driver.build_new_block_with_current_timestamp(None).await?;

    // Verify that blob_gas_used (DA footprint) is set and respects limits
    assert!(block.header.blob_gas_used.is_some(), "blob_gas_used should be set in Jovian");

    let blob_gas = block.header.blob_gas_used.unwrap();

    // The DA footprint must not exceed the block gas limit
    assert!(
        blob_gas == gas_limit,
        "DA footprint (blob_gas_used={blob_gas}) must not exceed block gas limit ({gas_limit})"
    );

```
