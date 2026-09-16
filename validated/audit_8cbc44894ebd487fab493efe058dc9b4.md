## Analysis

I found a directly analogous division-before-multiplication precision-loss pattern in `L1FeeParams::jovian_da_footprint`, which is used in consensus-critical DA (data-availability) footprint gas accounting for the Jovian upgrade. [1](#0-0) 

### Title
Division-before-multiplication precision loss in `L1FeeParams::jovian_da_footprint` DA-footprint gas accounting - (File: crates/common/l1-fees/src/params.rs)

### Summary
`jovian_da_footprint` computes the per-transaction DA-footprint gas charge used to enforce the Jovian DA footprint block limit. It performs the division by `1_000_000` **before** multiplying by the scalar, whereas every other L1-fee formula in the same file (`calculate_tx_l1_cost_bedrock`, `_ecotone`, `_fjord`) correctly multiplies first and divides once at the end.

### Finding Description
`tx_estimated_size_fjord` returns an estimated compressed transaction size pre-scaled by `1e6` (as documented at line 86-88: "the FastLZ-estimated compressed size (scaled by `1e6`... divided back down by `1e6` and multiplied by the DA-footprint gas scalar)"). The implementation does:
```rust
U256::from(tx_estimated_size_fjord(input))
    .wrapping_div(U256::from(1_000_000u64))
    .saturating_mul(scalar)
    .saturating_to::<u64>()
``` [2](#0-1) 

This divides the 1e6-scaled size down to whole units first, truncating up to `999,999/1e6` of the true estimated size before the scalar is ever applied. The correct order — matching the sibling functions in the same file, e.g. `calculate_tx_l1_cost_fjord` at line 148-159 which does `size.saturating_mul(l1_fee_scaled).wrapping_div(...)` — is multiply-then-divide, so any fractional remainder is preserved through the multiplication and only lost once, in the final division. This is exactly the bug class from the external report: `finalAmount = price_ * baseAmount / 10**baseDecimals` (correct, multiply-then-divide) vs. dividing first and multiplying second, which discards precision before it can be recovered. [3](#0-2) 

### Impact Explanation
`jovian_da_footprint` is consensus-critical: it is called from the block executor to derive the per-transaction and cumulative DA-footprint gas that gates the Jovian DA-footprint block limit (referenced 8 times in `crates/common/evm/src/executor/block_executor.rs`, and also in `crates/execution/flashblocks/src/state_builder.rs` and `crates/common/evm2/src/executor.rs`). Systematically under-charging DA-footprint gas for small transactions (any tx whose estimated size is not an exact multiple of `1e6`) lets more L1 calldata be posted per block than the intended footprint budget permits, undermining the very resource limit the mechanism exists to enforce. Because this is protocol-level gas accounting rather than a value-transfer computation, an inconsistency here would only be a problem if implementations disagree — but as written, the bug is deterministic and reproducible by any node running this code, so it does not by itself cause a chain split; the primary impact is a wrong, permanently under-counted DA-footprint gas result for every transaction, which is a "wrong provable output root" risk if a fixed/corrected version were ever run against historical blocks or if another engine (e.g., evm2 vs evm) implements the same formula in the correct order and consensus mismatches with this incorrect one.

### Likelihood Explanation
This code path runs on every transaction once Jovian's `da_footprint_gas_scalar` is set (i.e., is not `None`), making it a certainty rather than an edge case — every transaction whose FastLZ-estimated size (in the 1e6-scaled fixed-point form) is not an exact multiple of `1e6` will have its DA-footprint gas underestimated. Given `tx_estimated_size_fjord` returns fine-grained scaled sizes, this is the common case, not a rare one.

### Recommendation
Reorder the arithmetic to multiply before dividing, mirroring the pattern already used in `calculate_tx_l1_cost_fjord`/`_ecotone`/`_bedrock` in the same file:
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
Given `tx_estimated_size_fjord(input) = 1_500_000` (representing 1.5 units after unscaling) and `scalar = 3`:
- Current (buggy) order: `1_500_000 / 1_000_000 = 1`, then `1 * 3 = 3`.
- Correct order: `1_500_000 * 3 = 4_500_000`, then `4_500_000 / 1_000_000 = 4`.

The buggy path returns `3` instead of the correct `4`, permanently under-charging DA-footprint gas by 25% for this transaction — consistent with every non-exact-multiple-of-`1e6` estimated size, which is the common case for real transaction sizes. This matches the reported bug class (division-before-multiplication order-of-operations precision loss) exactly, just applied to the DA-footprint gas formula instead of an oracle price conversion.

**Note**: I could not fully trace how `jovian_da_footprint`'s output is consumed inside `block_executor.rs` (e.g., whether it strictly enforces a hard block limit or is only used for metrics/soft accounting) due to running out of tool iterations before reading that file's content; this affects whether the severity should be classified as High (hard resource-limit bypass / cross-client consensus risk) or Medium (metering drift only). I recommend a Devin session review of `crates/common/evm/src/executor/block_executor.rs` to confirm the exact consumption and enforcement semantics before finalizing severity.

### Citations

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
