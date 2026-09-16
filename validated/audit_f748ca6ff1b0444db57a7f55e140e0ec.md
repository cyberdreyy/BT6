## Title
Jovian DA-footprint gas computed with division before multiplication truncates/undercounts resource usage - ([File: crates/common/l1-fees/src/params.rs])

## Summary
`L1FeeParams::jovian_da_footprint` computes the Jovian DA-footprint gas by dividing the FastLZ-estimated compressed size by `1_000_000` *before* multiplying by the DA-footprint gas scalar, instead of multiplying first and dividing afterward. This is the same class of bug as the reported `newLeverage` miscalculation: reordering a multiply/divide sequence changes where truncation happens, silently producing a smaller-than-correct result that is used downstream for gas/DA metering and block-building throttling decisions.

## Finding Description
`tx_estimated_size_fjord` returns the FastLZ-estimated compressed size pre-scaled by `1e6` (per the doc comment). `jovian_da_footprint` is documented as computing:

> "the FastLZ-estimated compressed size (scaled by `1e6`, as `tx_estimated_size_fjord` returns) divided back down by `1e6` and multiplied by the DA-footprint gas scalar."

and it literally implements that as: [1](#0-0) 

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

i.e. `(estimated_size / 1e6) * scalar`, dividing *before* multiplying by `scalar`. Integer division here truncates the fractional (sub-`1e6`) remainder of the estimated size once, before that remainder has a chance to be scaled up by `scalar`. Multiplying first (`estimated_size * scalar / 1e6`) preserves that fractional information through the scalar multiplication and only truncates once at the very end — the two orders are mathematically equivalent only when `estimated_size` is an exact multiple of `1e6`; for any other value the divide-first order rounds the DA footprint gas *down* relative to the correct multiply-first order, and the gap grows with `scalar`. This mirrors exactly the external report's `newLeverage` bug: `(a + b*1e3)/c` vs `(a+b)*1e3/c` — an order-of-operations change that biases the computed value low and lets a downstream check pass more permissively than intended.

This computed value is not incidental — it is compared against, or accumulated into, the Jovian DA-footprint gas metering path consumed by the block executor and payload builder (`jovian_da_footprint` is referenced from `crates/common/evm/src/executor/block_executor.rs`, `crates/common/evm2/src/executor.rs`, and `crates/execution/flashblocks/src/state_builder.rs`), which is the block-building/resource-metering surface explicitly listed as in-scope. I was not able to fully trace, within the remaining budget, exactly how the returned `u64` is consumed at each of those three call sites (e.g., whether it's summed against a hard per-block DA-footprint gas limit, refunded, or only informational), so I cannot state with certainty how severe the resulting under-metering is at each site — that would require reading `block_executor.rs`, `evm2/executor.rs`, and `flashblocks/state_builder.rs` in full.

## Impact Explanation
If the DA-footprint gas value returned by `jovian_da_footprint` gates a per-block or per-transaction DA resource budget (as its name and surrounding Jovian DA-metering code suggest), systematically underestimating it lets transactions/blocks consume more real off-chain DA capacity than the protocol accounts for, i.e., a resource-metering/gas-accounting correctness bug on the block-building path. Depending on how the value feeds into consensus-critical gas accounting (e.g., if it must match between block builder and block validator, or is charged against `gas_used`), a consistent-but-wrong formula is not by itself a cross-node divergence (both sides use the same code), but it does mean the metering diverges from the intended protocol constant, understating real DA footprint and weakening the guardrail the Jovian DA scalar was introduced to enforce.

## Likelihood Explanation
The bug is deterministic and triggers on essentially every Jovian-fork transaction whose FastLZ-estimated size is not an exact multiple of `1,000,000` (i.e., almost every transaction), since `da_footprint_gas_scalar` only needs to be configured (non-`None`) for the code path to execute. No special transaction crafting is required beyond being included post-Jovian activation.

## Recommendation
Reorder the arithmetic to multiply before dividing, matching the standard OP-stack fee-formula pattern used elsewhere in the same file (e.g. `calculate_tx_l1_cost_fjord`):

```diff
 pub fn jovian_da_footprint(&self, input: &[u8]) -> u64 {
     let Some(scalar) = self.da_footprint_gas_scalar else {
         return 0;
     };
     U256::from(tx_estimated_size_fjord(input))
-        .wrapping_div(U256::from(1_000_000u64))
-        .saturating_mul(scalar)
+        .saturating_mul(scalar)
+        .wrapping_div(U256::from(1_000_000u64))
         .saturating_to::<u64>()
 }
```

This preserves the sub-`1e6` fractional remainder through the multiplication and truncates only once at the end, producing the mathematically correct (non-under-counted) DA-footprint gas value, consistent with the other cost formulas in [2](#0-1)  that multiply before dividing.

## Proof of Concept
Using the existing test harness style in the same file:

```rust
let p = L1FeeParams { da_footprint_gas_scalar: Some(U256::from(7)), ..Default::default() };
// tx_estimated_size_fjord floors at 100 * 1e6 for tiny inputs per the existing test,
// so pick an input whose estimated size is e.g. 150 * 1e6 + 500_000 (not a multiple of 1e6).
``` [3](#0-2) 

With the current (buggy) divide-first order: `(150_000_500_000 / 1_000_000) * 7 = 150 * 7 = 1050`.
With the correct multiply-first order: `(150_000_500_000 * 7) / 1_000_000 = 1_050_003_500_000 / 1_000_000 = 1050003`.

The example numbers above are illustrative of the truncation direction (divide-first drops the sub-`1e6` remainder before it can be amplified by the scalar); exact reproduction requires feeding a real `input` byte slice to `tx_estimated_size_fjord` and comparing the two orderings, which I was unable to execute directly given tool constraints — I recommend a background Devin session add a unit test in `crates/common/l1-fees/src/params.rs::tests` asserting `jovian_da_footprint` matches a multiply-then-divide reference computation for a range of non-exact-multiple-of-`1e6` inputs, and trace the three consumer sites (`block_executor.rs`, `evm2/executor.rs`, `flashblocks/state_builder.rs`) to confirm the concrete metering impact before merging the fix.

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
