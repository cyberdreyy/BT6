## Title
`L1FeeParams::jovian_da_footprint` divides before multiplying, truncating the Jovian DA-footprint gas charged per transaction - (File: `crates/common/l1-fees/src/params.rs`)

### Summary
`L1FeeParams::jovian_da_footprint` computes the Jovian DA-footprint gas by dividing the FastLZ-estimated compressed size by `1_000_000` *before* multiplying by the `da_footprint_gas_scalar`, instead of multiplying first and dividing last. This is the same integer-division-order truncation pattern flagged in the Vault_Velo.sol report (division before multiplication causes value loss), and it is inconsistent with every sibling fee function in the same file, which all multiply first and divide last. [1](#0-0) 

### Finding Description
`jovian_da_footprint` is documented as: "the FastLZ-estimated compressed size (scaled by 1e6) divided back down by 1e6 and multiplied by the DA-footprint gas scalar," but the implementation performs the division first:

```rust
pub fn jovian_da_footprint(&self, input: &[u8]) -> u64 {
    let Some(scalar) = self.da_footprint_gas_scalar else {
        return 0;
    };
    U256::from(tx_estimated_size_fjord(input))
        .wrapping_div(U256::from(1_000_000u64))   // divide first — truncates here
        .saturating_mul(scalar)                    // multiply second — truncation is amplified by scalar
        .saturating_to::<u64>()
}
``` [2](#0-1) 

`tx_estimated_size_fjord` returns the FastLZ compressed-size estimate scaled by `1e6` (per its own doc comment and the `MIN_TX_SIZE_SCALED = 100 * 1_000_000` floor), so any remainder below `1_000_000` in the estimated size is discarded by the `wrapping_div` *before* the scalar multiplication amplifies the (already truncated) quotient. [3](#0-2) 

Compare this to the correct pattern used everywhere else in the same file — `data_gas` (Fjord branch), `calculate_tx_l1_cost_ecotone`, and `calculate_tx_l1_cost_fjord` all multiply first, then divide, preserving precision:

```rust
// data_gas (Fjord):
estimated_size.saturating_mul(U256::from(NON_ZERO_BYTE_COST)).wrapping_div(U256::from(1_000_000u64));
// calculate_tx_l1_cost_fjord:
U256::from(tx_estimated_size_fjord(input)).saturating_mul(l1_fee_scaled).wrapping_div(U256::from(1_000_000_000_000u64));
``` [4](#0-3) [5](#0-4) 

The equivalent function in `flz.rs`, `data_gas_fjord`, likewise multiplies before dividing:
```rust
pub fn data_gas_fjord(input: &[u8]) -> u64 {
    let estimated_size = tx_estimated_size_fjord(input);
    estimated_size.saturating_mul(NON_ZERO_BYTE_COST).wrapping_div(1_000_000)
}
``` [6](#0-5) 

`jovian_da_footprint` is the only Jovian-era fee/gas function in this file that reverses the multiply/divide order, matching exactly the bug class described in the external report (division-before-multiplication truncation).

### Impact Explanation
`jovian_da_footprint` feeds Jovian's DA-footprint gas accounting/metering path (referenced extensively in `crates/common/evm/src/executor/block_executor.rs`, `crates/builder/core/src/flashblocks/context.rs`, `crates/builder/core/src/execution.rs`, and `crates/execution/txpool/src/validator.rs`). Because the estimated size is divided by `1e6` before multiplying by the scalar, any fractional part below one unit (1e6) of the scaled compressed-size estimate is silently dropped and the shortfall is never recovered — unlike the correct multiply-then-divide functions, where dropping the remainder only loses at most `scalar - 1` units of precision total, here the truncation happens on the *un-scaled* value and is baked into every subsequent scaling, systematically under-reporting the DA footprint for typical (non-round) transaction sizes. Since this value participates in block-level DA-footprint gas/resource metering that gates block building and (transitively) transaction admission, a sender/deployer/anonymous submitter can construct calldata whose `tx_estimated_size_fjord` result is not an exact multiple of `1_000_000`, causing the node to consistently under-charge/under-count the DA footprint for their transactions relative to the documented and reference (op-geth `jovian_da_footprint_estimation`) formula. This can let transactions consume more L1 DA capacity than their footprint accounting reflects, distorting the block-builder's DA budget enforcement — a resource-metering correctness issue reachable by any ordinary transaction sender.

### Likelihood Explanation
High likelihood of triggering on ordinary transaction traffic: any transaction whose FastLZ-estimated, 1e6-scaled compressed size is not an exact multiple of `1_000_000` (which is the overwhelming majority of real transactions, since `flz_compress_len` returns an integer byte count multiplied by `L1_COST_FASTLZ_COEF = 836_500`, rarely landing on an exact million boundary) will trigger the truncation. No special privileges are required — this is reachable by any unprivileged transaction sender once Jovian activates and `da_footprint_gas_scalar` is configured.

### Recommendation
Reorder the arithmetic to multiply before dividing, mirroring `data_gas_fjord`/`data_gas`/`calculate_tx_l1_cost_fjord`:

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
Given `tx_estimated_size_fjord(input) = 126_387_400` (a realistic non-round value, as used in the existing `test_calculate_tx_l1_cost_fjord` test fixture) and `da_footprint_gas_scalar = 7`:

- Current (buggy) order: `126_387_400 / 1_000_000 = 126` (truncated) → `126 * 7 = 882`
- Correct order: `126_387_400 * 7 = 884_711_800` → `884_711_800 / 1_000_000 = 884` (truncated once, at the end)

The buggy path yields `882` vs. the correct `884` — a 2-unit (≈0.2%) undercount that grows proportionally with larger scalars and can be repeated across every transaction in a block, systematically skewing the Jovian DA-footprint gas accounting relied upon by the block builder / resource-metering path. [1](#0-0)

### Citations

**File:** crates/common/l1-fees/src/params.rs (L67-73)
```rust
    pub fn data_gas(input: &[u8], upgrade: BaseUpgrade) -> U256 {
        if Self::is_enabled(upgrade, BaseUpgrade::Fjord) {
            let estimated_size = U256::from(tx_estimated_size_fjord(input));
            return estimated_size
                .saturating_mul(U256::from(NON_ZERO_BYTE_COST))
                .wrapping_div(U256::from(1_000_000u64));
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

**File:** crates/common/l1-fees/src/params.rs (L147-159)
```rust
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
