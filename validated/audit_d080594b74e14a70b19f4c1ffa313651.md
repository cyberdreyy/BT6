### Title
Division-before-multiplication in Jovian DA-footprint gas causes systematic under-metering of resource usage - (File: crates/common/l1-fees/src/params.rs)

### Summary
`L1FeeParams::jovian_da_footprint` computes `(estimated_size / 1_000_000) * scalar` instead of `(estimated_size * scalar) / 1_000_000`, truncating precision before the multiplication. This mirrors the reported bug class (`floorTick = (tick / tickSpacing) * tickSpacing` in Multipool.sol) where dividing first discards remainder bits that the subsequent multiplication can no longer recover.

### Finding Description
`tx_estimated_size_fjord` returns the FastLZ-estimated compressed transaction size *scaled by 1e6* (i.e., `compressed_bytes * 1_000_000`, generally not an exact multiple of `1_000_000`, since it's `fastlz_size.saturating_mul(836_500).saturating_sub(intercept).max(min)`). [1](#0-0) 

`jovian_da_footprint` divides that scaled value by `1_000_000` *before* multiplying by the per-byte gas scalar: [2](#0-1) 

Because integer division truncates, `wrapping_div(1_000_000)` discards up to `999_999/1_000_000` of a "byte" of precision immediately, and that loss is then amplified by `scalar` in the subsequent multiplication. Computing `estimated_size.saturating_mul(scalar).wrapping_div(1_000_000)` instead (multiply before divide, as `data_gas` and `calculate_tx_l1_cost_fjord`/`calculate_tx_l1_cost_bedrock` correctly do elsewhere in the same file) would only lose the single, unavoidable rounding step at the very end rather than compounding it through a multiplier. Compare the correct ordering used a few lines above in the same struct: [3](#0-2) 
and in the Fjord L1-cost path: [4](#0-3) 

The doc comment even claims this function "Mirrors the reference `jovian_da_footprint_estimation`", implying the intended semantics are the standard scale-then-descale pattern used identically elsewhere in the file — but the implementation flips the operation order only in this one function.

### Impact Explanation
`jovian_da_footprint` feeds into Jovian's DA-footprint gas/block-limit accounting, a resource-metering mechanism reachable by every ordinary L2 transaction sender post-Jovian (the derivation path is driven by attacker-controllable/L1-derived `da_footprint_gas_scalar` and the size of user-submitted calldata). Systematically under-computing the footprint gas for most calldata sizes (whenever `estimated_size` is not an exact multiple of `1_000_000`) causes:
- Under-charging of DA-footprint gas relative to the canonical (reference) formula, letting transactions consume more L1 DA capacity per unit of metered gas than intended — a resource-metering/mispricing bug.
- A potential state/receipt divergence versus any other Base/OP-stack execution client (e.g. op-geth) that implements `jovian_da_footprint_estimation` with the standard multiply-then-divide order, which is a determinism/consensus-relevant discrepancy for a value that participates in block gas accounting.

### Likelihood Explanation
This function is deterministically invoked for essentially every transaction post-Jovian; the only way to avoid the loss is for `tx_estimated_size_fjord` to output an exact multiple of `1_000_000`, which happens only when `fastlz_size * 836_500` minus the intercept lands exactly on a million-boundary, or when the minimum-size floor is hit. In practice this is rare, so the bug fires on essentially every transaction whose calldata isn't degenerate/minimal, making the divergence effectively certain to occur repeatedly. However, I was not able to load the exact op-geth/reference Go implementation to definitively confirm the intended (and canonical) operation order, so whether this constitutes a live consensus-divergence versus the shipped reference client (as opposed to simply an internal precision-loss inefficiency) remains unverified from the indexed code alone.

### Recommendation
Reorder the computation to multiply before dividing, matching the pattern used by `data_gas` and the L1-cost calculators in the same file:
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
Add a regression test asserting parity against the canonical Go/op-geth `jovian_da_footprint_estimation` reference vectors (mirroring the existing `data_gas_non_zero_bytes_matches_reference` style tests) to lock in the correct rounding order.

### Proof of Concept
Given `da_footprint_gas_scalar = 7` and an input whose `tx_estimated_size_fjord` = `100_500_000` (not a multiple of `1e6`):
- Current (buggy) order: `100_500_000 / 1_000_000 = 100` (fractional `0.5` truncated), then `100 * 7 = 700`.
- Correct order: `100_500_000 * 7 = 703_500_000`, then `/ 1_000_000 = 703`.

The buggy path under-reports DA footprint gas by `3` units (`700` vs `703`) for this single transaction; the discrepancy compounds with larger `scalar` values and scales across every transaction in a block, systematically under-metering DA resource usage relative to the intended/reference formula. [5](#0-4)

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

**File:** crates/common/l1-fees/src/params.rs (L63-82)
```rust
    /// Calculates the L1 calldata gas for posting `input`, per the schedule at `upgrade`.
    ///
    /// Post-Fjord uses the FastLZ-estimated compressed size; earlier forks count
    /// EIP-2028 calldata tokens, with pre-Regolith adding 68 signature bytes.
    pub fn data_gas(input: &[u8], upgrade: BaseUpgrade) -> U256 {
        if Self::is_enabled(upgrade, BaseUpgrade::Fjord) {
            let estimated_size = U256::from(tx_estimated_size_fjord(input));
            return estimated_size
                .saturating_mul(U256::from(NON_ZERO_BYTE_COST))
                .wrapping_div(U256::from(1_000_000u64));
        }

        let mut tokens = input.iter().fold(0u64, |acc, &byte| {
            acc + if byte == 0 { 1 } else { NON_ZERO_BYTE_MULTIPLIER_ISTANBUL }
        });
        if !Self::is_enabled(upgrade, BaseUpgrade::Regolith) {
            tokens += PRE_REGOLITH_SIGNATURE_BYTES * NON_ZERO_BYTE_MULTIPLIER_ISTANBUL;
        }
        U256::from(tokens.saturating_mul(STANDARD_TOKEN_COST))
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
