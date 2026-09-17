## Title
Division-before-multiplication precision loss undercounts Jovian DA-footprint gas - (File: `crates/common/l1-fees/src/params.rs`)

### Summary
`L1FeeParams::jovian_da_footprint()` computes the per-transaction DA-footprint gas by dividing the FastLZ-estimated compressed size by `1e6` *before* multiplying by the `da_footprint_gas_scalar`, instead of multiplying first and dividing last. This is the same division-before-multiplication bug class as the GMX `getPositionPnlUsd()` report, and it systematically undercounts the DA-footprint charge assessed to a transaction.

### Finding Description
`tx_estimated_size_fjord()` returns the estimated compressed size **scaled by `1e6`** [1](#0-0) . Every other consumer of this scaled value performs the scalar multiplication first and only divides by the scaling factor as the very last step, e.g. `calculate_tx_l1_cost_fjord`: [2](#0-1) 

`jovian_da_footprint`, however, reverses this order — it divides the scaled size by `1_000_000` first, and only then multiplies by `scalar`: [3](#0-2) 

Mathematically, the correct footprint is `floor(size_scaled * scalar / 1e6)`, but the code computes `floor(size_scaled / 1e6) * scalar`. These are not equal in general: truncating the `size_scaled / 1e6` division first throws away a remainder of up to `999,999` (out of `1e6`) before that fractional information can be redistributed by the subsequent multiplication, whereas multiplying first preserves that fractional weight through to the single final division. The result is that `jovian_da_footprint` can under-report the true DA-footprint gas by up to `scalar - 1` gas units per transaction (the docstring for this function itself confirms the intended formula is "divided back down by `1e6` and multiplied," acknowledging the division-first order was a deliberate but flawed implementation choice, unlike the correctly-ordered sibling function three functions above it).

### Impact Explanation
`da_footprint_gas_scalar` and the resulting DA-footprint gas value are used to meter and cap the DA byte-budget consumed by transactions during block building and txpool admission (confirmed via references in `crates/execution/txpool/src/validator.rs` and `crates/common/evm/src/executor/block_executor.rs`, which both call into this DA-footprint accounting). Systematic undercounting of a resource-metering value used to enforce a per-block DA capacity limit means transactions can be admitted/built into blocks that collectively consume more real L1 DA capacity than the configured `da_footprint_gas_scalar`-derived limit permits. This is a resource-metering correctness bug rather than a purely cosmetic rounding error, since the entire purpose of the Jovian DA-footprint mechanism is to bound L1 data-availability usage per block.

### Likelihood Explanation
The bug triggers on essentially every non-trivial transaction post-Jovian activation whenever `tx_estimated_size_fjord(input)` is not an exact multiple of `1,000,000` (which is the common case, since compressed transaction sizes measured in bytes are extremely unlikely to land exactly on a million-scaled boundary). No attacker action is required beyond submitting ordinary transactions; the precision loss is deterministic and always in the direction of under-charging, so it is reliably and continuously reachable via the standard transaction/block-building path.

### Recommendation
Reorder the arithmetic in `jovian_da_footprint` to multiply by `scalar` before dividing by `1_000_000`, mirroring the pattern already used in `calculate_tx_l1_cost_fjord`:
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
Using the existing test fixture values from the codebase's own unit test: [4](#0-3) 

With `scalar = 7` and a minimum-floored `tx_estimated_size_fjord` of `100 * 1_000_000` (exact multiple), no loss occurs in this specific test case — but for any `tx_estimated_size_fjord(input)` value not evenly divisible by `1_000_000` (the realistic case for any input above the minimum floor), e.g. `size_scaled = 100_500_001`, `scalar = 7`:
- Correct: `floor(100_500_001 * 7 / 1_000_000) = floor(703_500_007 / 1_000_000) = 703`
- Current buggy code: `floor(100_500_001 / 1_000_000) * 7 = 100 * 7 = 700`

This demonstrates a 3-gas undercount for this input, and the gap scales up to `scalar - 1` per transaction as `scalar` grows, systematically leaking DA-footprint budget across all transactions in a block.

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

**File:** crates/common/l1-fees/src/params.rs (L236-241)
```rust
    fn jovian_da_footprint_uses_min_size_and_scalar() {
        // A small input floors at the minimum FastLZ size (100 * 1e6); divided by 1e6 that is 100
        // compressed bytes, times the scalar.
        let p = L1FeeParams { da_footprint_gas_scalar: Some(U256::from(7)), ..Default::default() };
        assert_eq!(p.jovian_da_footprint(&[0x02, 0xAB, 0xCD]), 100 * 7);
    }
```
