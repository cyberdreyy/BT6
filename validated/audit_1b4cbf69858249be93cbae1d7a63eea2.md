### Title
Jovian DA-footprint gas computed with division before multiplication truncates scaled compressed-size fixed-point value, causing systematic under-metering of block DA capacity - (File: crates/common/l1-fees/src/params.rs)

### Summary
`L1FeeParams::jovian_da_footprint` divides the FastLZ-estimated compressed transaction size (a value pre-scaled by `1e6`) by `1e6` *before* multiplying by the DA-footprint gas scalar, instead of multiplying first and dividing last as every other cost formula in the same file does.

### Finding Description
`tx_estimated_size_fjord` returns the compressed-size estimate pre-scaled by `1e6` (a fixed-point value), as documented at [1](#0-0) . Every other consumer of this value in `params.rs` follows the safe "multiply first, divide last" pattern, e.g. `calculate_tx_l1_cost_fjord`: [2](#0-1) 

However, `jovian_da_footprint` reverses this order — it divides the scaled estimate by `1e6` first, then multiplies by `da_footprint_gas_scalar`: [3](#0-2) 

Because `tx_estimated_size_fjord` is derived from `fastlz_size.saturating_mul(836_500).saturating_sub(42_585_600).max(100_000_000)` (see [4](#0-3) ), the result is essentially never an exact multiple of `1_000_000`. Performing `wrapping_div(1_000_000)` first discards the fractional remainder (up to `999_999` scaled units, i.e. almost a full byte of compressed-size precision) *before* the scalar is applied. Multiplying afterward amplifies that lost precision by the scalar factor instead of only rounding the final integer result down once, which is what the equivalent Ecotone/Fjord fee functions correctly do by dividing only at the very end.

### Impact Explanation
`da_footprint_used` (accumulated from `jovian_da_footprint`) is the metric enforcing the Jovian [DA-footprint block limit](https://github.com/ethereum-optimism/specs/blob/main/specs/protocol/jovian/exec-engine.md#da-footprint-block-limit), tracked per block in `BaseBlockExecutor::da_footprint_used` and populated via `tx_estimated_size_fjord`/`jovian_da_footprint` in `crates/common/evm/src/executor/block_executor.rs`, `crates/common/evm2/src/executor.rs`, and `crates/execution/flashblocks/src/state_builder.rs`. Systematically under-counting each transaction's DA footprint (by up to `scalar - 1` gas per transaction, scaled across every transaction in the block) allows more L1-DA-heavy transactions to be packed into a block than the protocol intends, degrading the effectiveness of the DA-footprint gas limit as a resource-metering safeguard. This is a resource-accounting/metering correctness bug in block building rather than a funds-theft or freezing bug, so it is Medium at most, and only reachable when `da_footprint_gas_scalar` is configured (post-Jovian).

### Likelihood Explanation
This triggers on essentially every Jovian-era transaction once `da_footprint_gas_scalar` is set, since the truncation happens unconditionally whenever the scaled estimated size isn't an exact multiple of `1e6` (which is nearly always, given the FastLZ-derived formula). No attacker action beyond submitting an ordinary transaction is required — likelihood is high once Jovian activates with a configured scalar, but the resulting drift per transaction is small (bounded by `scalar - 1` gas), so cumulative impact across a block is limited.

### Recommendation
Reorder the arithmetic to multiply before dividing, matching the pattern used elsewhere in the file:
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
This preserves the fractional precision of the scaled estimated size until the final division, avoiding compounded truncation.

### Proof of Concept
Given `tx_estimated_size_fjord(input) = 100_500_000` (i.e., 100.5 estimated bytes, scaled by 1e6) and `da_footprint_gas_scalar = 3`:

- Current (buggy) order: `100_500_000 / 1_000_000 = 100` (fractional `.5` truncated), then `100 * 3 = 300`.
- Correct order: `100_500_000 * 3 = 301_500_000`, then `/ 1_000_000 = 301`.

The buggy path under-reports DA-footprint gas by `1` unit per transaction in this example; with larger scalars or many transactions per block, the aggregate under-count of `da_footprint_used` grows, weakening the Jovian DA-footprint block-limit enforcement described in [5](#0-4) .

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

**File:** crates/common/evm/src/executor/block_executor.rs (L46-51)
```rust
    pub gas_used: u64,
    /// DA footprint.
    ///
    /// This is only set for blocks post-Jovian activation.
    /// See [DA footprint block limit spec](https://github.com/ethereum-optimism/specs/blob/main/specs/protocol/jovian/exec-engine.md#da-footprint-block-limit)
    pub da_footprint_used: u64,
```
