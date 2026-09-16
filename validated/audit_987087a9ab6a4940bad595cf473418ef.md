### Title
Division-before-multiplication truncation in Jovian DA-footprint gas accounting undercharges/undermeters transaction data-availability footprint - (File: crates/common/l1-fees/src/params.rs)

### Summary
The Jovian DA-footprint gas calculation divides the FastLZ-estimated, 1e6-scaled compressed transaction size by `1_000_000` *before* multiplying by the DA-footprint gas scalar, instead of multiplying first and dividing once at the end. This is the same division-before-multiplication rounding-error pattern as the reference report, and it appears independently in four places in the codebase.

### Finding Description
`L1FeeParams::jovian_da_footprint` computes: [1](#0-0) 

```rust
pub fn jovian_da_footprint(&self, input: &[u8]) -> u64 {
    let Some(scalar) = self.da_footprint_gas_scalar else { return 0; };
    U256::from(tx_estimated_size_fjord(input))
        .wrapping_div(U256::from(1_000_000u64))   // divide first
        .saturating_mul(scalar)                    // multiply after
        .saturating_to::<u64>()
}
```

`tx_estimated_size_fjord` returns the estimated compressed size *scaled by 1e6* (bytes × 1e6) [2](#0-1) . Truncating this value to whole bytes (`/1e6`) before multiplying by `scalar` discards up to `999_999/1_000_000` of a byte's worth of precision, and that discarded fraction is lost *before* being scaled by the DA-footprint gas scalar — exactly mirroring the reported `getfCashExchangeRate()` division-before-multiplication defect (dividing before the value is "scaled up" by a subsequent multiplication).

This same order-of-operations bug is duplicated verbatim in three additional call sites that independently recompute the DA footprint rather than reusing `L1FeeParams::jovian_da_footprint`:

- Block execution: `estimate_tx_compressed_size(...).saturating_div(1_000_000)` then `.saturating_mul(da_footprint_gas_scalar)` [3](#0-2) 
- Flashblocks pending-state builder: identical pattern [4](#0-3) 
- RPC receipt building (`blob_gas_used` field repurposed for DA footprint under Jovian): identical pattern [5](#0-4) 

Notably, the sibling L1-fee functions in the very same file correctly perform multiplication before division (`saturating_mul(...).saturating_mul(...).wrapping_div(...)`) — e.g. `calculate_tx_l1_cost_bedrock`, `calculate_tx_l1_cost_ecotone`, `calculate_tx_l1_cost_fjord` [6](#0-5) . Only the Jovian DA-footprint math inverts that order, which is inconsistent with the established, correct pattern used elsewhere in the same module.

### Impact Explanation
The truncation always rounds the per-transaction DA footprint estimate *down*, never up, before scaling. Since this footprint feeds a per-block DA-footprint resource limit ("DA footprint block limit" per the Jovian spec, referenced directly in code comments) used by block building/metering, systematic under-accounting of DA footprint gas per transaction allows more effective L1 calldata/DA usage to be packed into a block than the intended cap permits. Because block building and resource metering are explicitly in-scope, and this degrades an on-chain resource-limit guarantee (looser-than-specified DA footprint enforcement across every block post-Jovian), this is a resource-metering integrity defect rather than a purely cosmetic rounding issue — every actor able to submit ordinary transactions benefits from the under-metering with no special privilege required.

Because the computation is deterministic and identical in all four locations (validator execution, block building/flashblocks, and RPC receipt reporting), it does not by itself cause a chain split between nodes running this code — but it does cause the enforced DA footprint to be silently smaller than the value the protocol intends to enforce, which is precisely the "block building and resource metering" bug class called out as in-scope.

### Likelihood Explanation
This triggers on essentially every ordinary transaction once Jovian is active — no special conditions are required beyond normal calldata submission, and the discrepancy is deterministic and reproducible on every transaction (worst case up to ~1 byte of estimated size truncated before scaling by `da_footprint_gas_scalar`, compounding across every transaction in a block).

### Recommendation
Multiply by the scalar first and divide by `1_000_000` once, matching the correct pattern already used by `calculate_tx_l1_cost_bedrock` / `_ecotone` / `_fjord` in the same file:

```rust
pub fn jovian_da_footprint(&self, input: &[u8]) -> u64 {
    let Some(scalar) = self.da_footprint_gas_scalar else { return 0; };
    U256::from(tx_estimated_size_fjord(input))
        .saturating_mul(scalar)
        .wrapping_div(U256::from(1_000_000u64))
        .saturating_to::<u64>()
}
```
Apply the same fix to the duplicated logic in `crates/common/evm/src/executor/block_executor.rs`, `crates/execution/flashblocks/src/state_builder.rs`, and `crates/execution/rpc/src/eth/receipt.rs`, and ideally have all three call sites delegate to the single `L1FeeParams::jovian_da_footprint` implementation to prevent the formula from drifting independently in multiple places.

### Proof of Concept
Using the existing test fixture in the repo, take an input whose `tx_estimated_size_fjord(input)` is not an exact multiple of `1_000_000` (e.g., a real compressed size like `100_500_000` after the minimum-size floor is exceeded) and a large `da_footprint_gas_scalar` (e.g., `65535`, its `u16` max per `da_footprint()`):

- Correct (mul-then-div) order: `100_500_000 * 65535 / 1_000_000 = 6,585,767` (approx, floor at the very end).
- Current (div-then-mul) order: `(100_500_000 / 1_000_000) * 65535 = 100 * 65535 = 6,553,500`.

The difference (~32,267 gas units in this example) is silently dropped from the metered DA footprint on every such transaction, and scales with `scalar` and the fractional remainder of the compressed-size estimate — demonstrating the systematic undercount described above. This can be verified directly against the existing unit test `jovian_da_footprint_uses_min_size_and_scalar` [7](#0-6) , which encodes the current (incorrect-order) expected value `100 * 7` rather than the precision-preserving `estimated_size * 7 / 1_000_000`.

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

**File:** crates/common/l1-fees/src/params.rs (L117-159)
```rust
    /// Pre-Ecotone (Bedrock) L1 cost. Deposit and empty transactions are fee-exempt.
    pub fn calculate_tx_l1_cost_bedrock(&self, input: &[u8], upgrade: BaseUpgrade) -> U256 {
        if Self::is_fee_exempt(input) {
            return U256::ZERO;
        }
        Self::data_gas(input, upgrade)
            .saturating_add(self.l1_fee_overhead.unwrap_or_default())
            .saturating_mul(self.l1_base_fee)
            .saturating_mul(self.l1_base_fee_scalar)
            .wrapping_div(U256::from(1_000_000u64))
    }

    /// Post-Ecotone L1 cost:
    /// `calldataGas * (l1BaseFee*16*l1BaseFeeScalar + l1BlobBaseFee*l1BlobBaseFeeScalar) / 16e6`.
    pub fn calculate_tx_l1_cost_ecotone(&self, input: &[u8], upgrade: BaseUpgrade) -> U256 {
        if Self::is_fee_exempt(input) {
            return U256::ZERO;
        }
        // The very first Ecotone block (unless activated at genesis) still prices
        // using the Bedrock function, detected via unset Ecotone scalars.
        if self.empty_ecotone_scalars {
            return self.calculate_tx_l1_cost_bedrock(input, upgrade);
        }
        let rollup_data_gas_cost = Self::data_gas(input, upgrade);
        self.calculate_l1_fee_scaled_ecotone()
            .saturating_mul(rollup_data_gas_cost)
            .wrapping_div(U256::from(1_000_000u64 * NON_ZERO_BYTE_COST))
    }

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

**File:** crates/common/l1-fees/src/params.rs (L235-241)
```rust
    #[test]
    fn jovian_da_footprint_uses_min_size_and_scalar() {
        // A small input floors at the minimum FastLZ size (100 * 1e6); divided by 1e6 that is 100
        // compressed bytes, times the scalar.
        let p = L1FeeParams { da_footprint_gas_scalar: Some(U256::from(7)), ..Default::default() };
        assert_eq!(p.jovian_da_footprint(&[0x02, 0xAB, 0xCD]), 100 * 7);
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

**File:** crates/common/evm/src/executor/block_executor.rs (L133-149)
```rust
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
```

**File:** crates/execution/flashblocks/src/state_builder.rs (L264-286)
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
```

**File:** crates/execution/rpc/src/eth/receipt.rs (L381-390)
```rust
        chain_spec.is_jovian_active_at_timestamp(timestamp).then(|| {
            // Estimate the size of the transaction in bytes and multiply by the DA
            // footprint gas scalar.
            // Jovian specs: `https://github.com/ethereum-optimism/specs/blob/main/specs/protocol/jovian/exec-engine.md#da-footprint-block-limit`
            let da_size = estimate_tx_compressed_size(tx_signed.encoded_2718().as_slice())
                .saturating_div(1_000_000)
                .saturating_mul(l1_block_info.da_footprint_gas_scalar.unwrap_or_default().into());

            core_receipt.blob_gas_used = Some(da_size);
        });
```
