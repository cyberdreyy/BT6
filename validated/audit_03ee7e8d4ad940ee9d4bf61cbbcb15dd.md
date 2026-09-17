### Title
Division-before-multiplication precision loss in Jovian DA-footprint estimation causes systematic under-metering of block resource usage - (File: crates/common/evm/src/executor/block_executor.rs)

### Summary
`BaseBlockExecutor::jovian_da_footprint_estimation` computes each transaction's DA-footprint gas by dividing the FastLZ-estimated compressed size (which is intentionally pre-scaled by `1e6`) by `1_000_000` *before* multiplying by `da_footprint_gas_scalar`, exactly the "divide-then-multiply" pattern flagged in the referenced report.

### Finding Description
`tx_estimated_size_fjord` (aliased here as `estimate_tx_compressed_size`) returns the compressed size scaled by `1e6` [1](#0-0) . In `jovian_da_footprint_estimation`, this scaled value is truncated with `saturating_div(1_000_000)` first, and only afterward multiplied by `da_footprint_gas_scalar`: [2](#0-1) 

The identical division-before-multiplication order is duplicated in the engine-neutral helper `L1FeeParams::jovian_da_footprint`, whose own doc comment explicitly describes the truncate-then-multiply order as "mirroring" the block executor: [3](#0-2) 

Because `saturating_div(1_000_000)` truncates any fractional sub-`1e6` remainder of the scaled size *before* the multiplication by `da_footprint_gas_scalar` amplifies whatever was truncated, the resulting `tx_da_footprint` is systematically lower (and never higher) than the mathematically exact `size * scalar / 1e6`. This value directly gates the Jovian per-block/per-tx DA-footprint resource limit used during both block execution and (per the file's own doc-link) block building: [4](#0-3) 

This is precisely the same bug class described in the external report — dividing before multiplying loses precision that a "multiply-then-divide" ordering would have preserved — applied here to a resource-metering computation reachable by any unprivileged transaction sender whose transaction is included in a Jovian-activated block.

### Impact Explanation
This function gates block-level and per-transaction resource limits (`da_footprint_available`/`da_footprint_used`), which is explicitly in the "block building and resource metering" analog category. Because the truncation happens deterministically in both the reference `block_executor.rs` implementation and the "mirrored" `params.rs` helper, all Base nodes running this exact code compute the same (slightly-under) DA footprint value — so no chain split occurs *among Base nodes themselves*. However, if this rounding order diverges from the canonical OP-Stack Jovian spec formula (order of operations for `estimatedSize * scalar / 1e6`) implemented by other OP-Stack execution clients (e.g., op-geth), transactions near the DA-footprint block boundary could be accepted by Base's engine but rejected (or vice-versa) by a spec-conforming client, which is a chain-split-class risk. I could not verify the exact canonical formula/order mandated by the linked OP-Stack Jovian DA-footprint spec from within this repository (the spec text itself is not vendored here), so I cannot confirm with certainty whether this ordering is a genuine deviation from spec or an intentional, spec-compliant choice matching op-geth's Go port.

### Likelihood Explanation
Any unprivileged transaction sender submitting a transaction included in a post-Jovian block exercises this code path on every transaction; no special privilege or condition is required. The precision loss is small per-transaction (bounded by `scalar - 1` gas units of under-counting, since the truncated remainder is < 1 before the scalar multiply), so it is unlikely to be independently exploitable for large-scale resource exhaustion, but it is a real, always-triggered deterministic deviation from the mathematically exact metering formula.

### Recommendation
Multiply first, then divide, to preserve precision: compute `estimate_tx_compressed_size(...).saturating_mul(da_footprint_gas_scalar).saturating_div(1_000_000)` in both `BaseBlockExecutor::jovian_da_footprint_estimation` (`crates/common/evm/src/executor/block_executor.rs`) and `L1FeeParams::jovian_da_footprint` (`crates/common/l1-fees/src/params.rs`), matching whatever order is canonically defined by the OP-Stack Jovian DA-footprint-block-limit spec, and add a cross-client differential test against op-geth's reference values to confirm exact bit-for-bit agreement.

### Proof of Concept
Not applicable as a standalone exploit — the issue is a deterministic arithmetic-ordering defect reachable by any transaction post-Jovian activation. Given `estimate_tx_compressed_size(tx) = 100_999_999` (scaled by 1e6, i.e. 100.999999 bytes) and `da_footprint_gas_scalar = 3`:
- Current code: `100_999_999 / 1_000_000 = 100`, then `100 * 3 = 300`.
- Multiply-first: `100_999_999 * 3 = 302_999_997`, then `/ 1_000_000 = 302` (truncated).
The two orders differ by 2 units for this input, illustrating the systematic under-metering; the magnitude scales with `scalar - 1` in the worst case, per transaction. [5](#0-4)

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
