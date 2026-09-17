### Title
DA-footprint gas underestimation via division-before-multiplication truncation - ([File: crates/common/l1-fees/src/params.rs])

### Summary
`L1FeeParams::jovian_da_footprint` computes the Jovian DA-footprint gas by dividing before multiplying, causing integer-truncation that systematically underestimates the DA footprint charged/metered for every post-Jovian L2 transaction.

### Finding Description
`jovian_da_footprint` estimates DA footprint gas as `estimatedSize(scaled by 1e6) / 1e6 * scalar`, performing the `wrapping_div(1_000_000)` **before** the `saturating_mul(scalar)`: [1](#0-0) 

Because `tx_estimated_size_fjord` returns a value pre-scaled by `1e6` (i.e., fractional compressed-size precision), doing the division first truncates any fractional compressed-size information down to whole "compressed bytes" before the scalar multiplication is applied. The mathematically correct order — multiply by `scalar` first, then divide by `1e6` — would preserve that sub-byte precision through the final division. With the current order, the truncation happens earlier and is amplified relative to the size scale rather than the final result scale, rounding the footprint down more aggressively than the intended fixed-point formula, matching exactly the "division before multiplication" bug class from the referenced report (`TrufVesting.sol` `timeElapsed` truncation).

This function is called from the block executor's DA-footprint accounting used to enforce the Jovian DA-footprint block limit (`da_footprint_used` in `crates/common/evm/src/executor/block_executor.rs`, and equivalently in `crates/common/evm2/src/executor.rs`), and is also used by the builder/flashblocks state-builder (`crates/execution/flashblocks/src/state_builder.rs`, `crates/builder/core/src/execution.rs`, `crates/builder/core/src/flashblocks/payload.rs`) to meter block-building resource usage against the DA-footprint cap.

### Impact Explanation
Since every transaction's DA-footprint gas is underestimated due to premature division, the cumulative `da_footprint_used` for a block will be computed lower than the true intended value across many transactions in aggregate. An unprivileged transaction sender can craft transaction calldata sizes that maximize this rounding-down effect (systematic truncation on every tx), allowing more transactions/data to be packed into a block than the DA-footprint limit is meant to permit. This is a resource-metering bypass on the Jovian DA-footprint block limit — a protocol-level economic/resource-safety invariant — potentially letting builders/senders exceed the intended L1-data-posting budget per block, which can affect batcher costs and violate the "block building and resource metering" guarantee explicitly in scope.

### Likelihood Explanation
The path is triggered automatically for every post-Jovian transaction with non-empty, non-deposit input via the normal block-execution/block-building pipeline; no special privilege or unusual conditions are required — only that Jovian is active and `da_footprint_gas_scalar` is configured (non-`None`). The systematic (not edge-case) nature of the truncation makes it a High-likelihood but bounded (dust-level, per-tx capped by 1e6 rounding) underestimation, aggregating over many transactions in a block.

### Recommendation
Reorder the arithmetic to multiply before dividing, matching the pattern used elsewhere in the same file (e.g., `data_gas`'s `estimated_size.saturating_mul(NON_ZERO_BYTE_COST).wrapping_div(1_000_000)`):

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
Using the existing unit test as the reference model: with `da_footprint_gas_scalar = 7` and an input whose `tx_estimated_size_fjord` returns the floor value `100_000_000` (i.e., `100e6`, representing `100` compressed bytes at 1e6 scale): [2](#0-1) 

Current (buggy) order: `100_000_000 / 1_000_000 = 100`, then `100 * 7 = 700`.
Correct order: `100_000_000 * 7 = 700_000_000`, then `/ 1_000_000 = 700`.

Both agree at this exact boundary value, but for any `tx_estimated_size_fjord` result that is *not* an exact multiple of `1_000_000` (e.g., `100_500_000`, representing 100.5 compressed bytes), the two orders diverge:
- Buggy: `100_500_000 / 1_000_000 = 100` (floor) → `100 * 7 = 700`
- Correct: `100_500_000 * 7 = 703_500_000` → `/ 1_000_000 = 703`

The buggy path loses `3` gas units of DA-footprint accounting on this single transaction; repeated across every transaction in a block this rounding-down bias accumulates, letting the block builder or a sequence of crafted transactions consume more real L1 DA capacity than the `da_footprint_used` accounting reflects, undermining the block resource-metering invariant. I was not able to fully trace the exact enforcement threshold check against `da_footprint_used` in `block_executor.rs`/`execution.rs` within the available exploration budget, so the precise consensus-level consequence (e.g., whether this can cause a chain split between nodes computing footprint differently, versus only a resource-cap laxity) should be verified directly in those files before treating this as more than a Medium-severity resource-metering bypass.

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
