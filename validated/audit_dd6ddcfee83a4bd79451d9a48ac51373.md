### Title
Division-before-multiplication precision loss in `L1FeeParams::jovian_da_footprint` — ([File: crates/common/l1-fees/src/params.rs])

### Summary
`L1FeeParams::jovian_da_footprint` divides the FastLZ-estimated compressed size by `1e6` *before* multiplying by the DA-footprint gas scalar, instead of multiplying first and dividing last. This is the exact division-before-multiplication bug class from the source report, applied to Base's Jovian DA-footprint gas accounting.

### Finding Description
`tx_estimated_size_fjord` returns the estimated compressed size scaled by `1e6` [1](#0-0) . `jovian_da_footprint` is supposed to compute `estimated_size_scaled * scalar / 1e6`, but instead computes `(estimated_size_scaled / 1e6) * scalar`: [2](#0-1) 

Because the division happens first, any sub-`1e6` remainder in `estimated_size_scaled` (which is common, since `tx_estimated_size_fjord` is `max(MIN_TX_SIZE_SCALED, intercept + fastlzCoef*fastlzSize)` and is rarely an exact multiple of `1e6`) is truncated away before the multiplication by `scalar` can preserve it. The correct order (multiply-then-divide) would retain that fractional precision up to `scalar` times more resolution, as in the original report's recommendation.

### Impact Explanation
`jovian_da_footprint` feeds `BaseBlockExecutor::da_footprint_used`, which implements the Jovian DA-footprint block-gas-limit accounting (per the OP-stack spec referenced in the executor) [3](#0-2) . Systematic under-counting of the DA footprint per transaction (from truncating fractional compressed-size units before scaling) means the aggregate `da_footprint_used` for a block will consistently be lower than the "correct" multiply-first value would produce. Over many transactions this bias can let a block admit more compressed calldata than the DA-footprint limit is meant to allow, weakening the block-building resource metering the limit exists to enforce. This is a metering/accounting correctness bug in a path reachable by ordinary transaction inclusion (any transaction with calldata read by `flz_compress_len`), not by a privileged actor.

### Likelihood Explanation
This triggers on essentially every transaction post-Jovian activation, since `tx_estimated_size_fjord`'s output is derived from FastLZ compression length and rarely lands on an exact multiple of `1e6`; the precision loss is deterministic and reproducible, not a rare edge case.

### Recommendation
Reorder the arithmetic to multiply before dividing, matching the fix pattern in the source report:
```rust
pub fn jovian_da_footprint(&self, input: &[u8]) -> u64 {
    let Some(scalar) = self.da_footprint_gas_scalar else {
        return 0;
    };
    U256::from(tx_estimated_size_fjord(input))
        .saturating_mul(scalar)
        .wrapping_div(U256::from(1_000_000u64))
        .saturating_to::<u64>()
}
```
This mirrors the already-correct multiply-then-divide pattern used elsewhere in the same file, e.g. `calculate_tx_l1_cost_bedrock` and `calculate_tx_l1_cost_ecotone` [4](#0-3) .

### Proof of Concept
Using the existing unit test's own numbers but with a non-multiple-of-1e6 estimated size: suppose `tx_estimated_size_fjord(input)` returns `100_500_000` (i.e., 100.5 compressed bytes scaled by 1e6) and `scalar = 7`.
- Current (buggy) order: `100_500_000 / 1_000_000 = 100` (fraction `.5` truncated), then `100 * 7 = 700`.
- Correct order: `100_500_000 * 7 = 703_500_000`, then `/ 1_000_000 = 703`.

The buggy path returns `700` instead of `703`, an under-count of ~0.43% per transaction that compounds across a block's transactions in `da_footprint_used`, systematically deflating the DA-footprint gas metric relative to the specified formula. The existing test only exercises the clamped, exactly-scaled minimum size (`100 * 1_000_000`), so it does not catch this truncation [5](#0-4) .

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

**File:** crates/common/l1-fees/src/params.rs (L117-127)
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

**File:** crates/common/evm/src/executor/block_executor.rs (L46-51)
```rust
    pub gas_used: u64,
    /// DA footprint.
    ///
    /// This is only set for blocks post-Jovian activation.
    /// See [DA footprint block limit spec](https://github.com/ethereum-optimism/specs/blob/main/specs/protocol/jovian/exec-engine.md#da-footprint-block-limit)
    pub da_footprint_used: u64,
```
