### Title
Division-before-multiplication in `jovian_da_footprint` causes gas-metering precision loss - (File: crates/common/l1-fees/src/params.rs)

### Summary
`L1FeeParams::jovian_da_footprint` computes the Jovian DA-footprint gas by dividing the FastLZ-estimated, `1e6`-scaled transaction size by `1e6` *before* multiplying by the `da_footprint_gas_scalar`, instead of multiplying first and dividing last. This is the same division-before-multiplication rounding-loss pattern flagged in the referenced DODO V3 `usedQuota` finding.

### Finding Description
`tx_estimated_size_fjord` returns the estimated compressed size scaled by `1e6` [1](#0-0) . Every other consumer of this scaled value multiplies by its cost factor first and divides by `1e6` last, e.g. `data_gas_fjord` [2](#0-1)  and `calculate_tx_l1_cost_fjord` [3](#0-2) .

`jovian_da_footprint`, however, does it backwards — it divides the scaled estimate by `1e6` first (truncating the sub-byte remainder, up to `999_999` out of `1_000_000`, i.e. nearly a full byte) and only then multiplies by `scalar`: [4](#0-3) 

Because the truncated remainder is discarded before the multiplication, the rounding error is scaled up by `scalar` instead of being scaled down with it, which is exactly the "division before multiplication" precision-loss class described in the external report (`record.amount.div(oldInterestIndex).mul(currentInterestIndex)` in DODO's `poolBorrow`). The correct order, matching every sibling function in the same file, is `saturating_mul(scalar).wrapping_div(1_000_000)`.

### Impact Explanation
`jovian_da_footprint` feeds Jovian DA-footprint gas accounting, which is part of block building / resource metering (an explicitly in-scope reachable path from an unprivileged transaction sender's calldata). Any deterministic bug in this arithmetic is still consensus-relevant: if the reference OP-stack implementation performs multiply-then-divide (as the sibling functions in this same codebase do) while this port performs divide-then-multiply, the computed DA-footprint gas can diverge from the canonical value for adversarially chosen calldata sizes, leading to incorrect gas metering/resource accounting for Jovian blocks. This is a Medium-severity correctness bug rather than a Critical one, since it does not, by itself, enable theft of funds or forge signatures — it can, however, cause systematically wrong (under- or over-) DA-footprint gas charges, i.e. wrong provable resource accounting for blocks built under Jovian.

### Likelihood Explanation
Every transaction submitted by an ordinary, unprivileged sender supplies the `input` bytes used in `tx_estimated_size_fjord`, and the resulting truncation happens deterministically on every call once the Jovian `da_footprint_gas_scalar` is configured (non-`None`). No special privilege or crafted state is required — any transaction whose FastLZ-estimated size modulo `1e6` is non-zero triggers the precision loss, which is the common case.

### Recommendation
Reorder the operations to multiply before dividing, consistent with `data_gas_fjord` and `calculate_tx_l1_cost_fjord`:
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

### Proof of Concept
Given `tx_estimated_size_fjord(input) = 100_999_999` (i.e. `100` whole bytes plus a `999_999/1_000_000` fractional remainder) and `scalar = 100`:
- Current (buggy) order: `100_999_999 / 1_000_000 = 100` (fraction truncated), then `100 * 100 = 10_000`.
- Correct order: `100_999_999 * 100 = 10_099_999_900`, then `/ 1_000_000 = 10_099` (truncated only once, at the end).

The two results (`10_000` vs `10_099`) differ by ~1%, and the gap scales linearly with `scalar`, demonstrating the avoidable precision loss caused by dividing before multiplying in [4](#0-3) .

### Citations

**File:** crates/common/flz/src/flz.rs (L20-23)
```rust
pub fn data_gas_fjord(input: &[u8]) -> u64 {
    let estimated_size = tx_estimated_size_fjord(input);
    estimated_size.saturating_mul(NON_ZERO_BYTE_COST).wrapping_div(1_000_000)
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

**File:** crates/common/l1-fees/src/params.rs (L148-159)
```rust
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
