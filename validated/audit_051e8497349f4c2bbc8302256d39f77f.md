### Title
Divide-before-multiply in Jovian DA-footprint gas calculation causes systematic gas-metering underestimation - (File: `crates/common/l1-fees/src/params.rs`)

### Summary
`L1FeeParams::jovian_da_footprint` computes the Jovian DA-footprint gas charge by dividing the FastLZ-estimated transaction size by `1_000_000` **before** multiplying by the configured `da_footprint_gas_scalar`, instead of multiplying first and dividing last. This is the exact "divide-then-multiply" precision-loss pattern described in the external report, and it is inconsistent with the sibling fee functions in the same file, which all multiply before dividing.

### Finding Description
`tx_estimated_size_fjord` returns an estimated compressed transaction size *scaled by 1e6* (`max(minTransactionSize, intercept + fastlzCoef*fastlzSize)`), a value that is generally **not** an exact multiple of `1_000_000`. [1](#0-0) 

`jovian_da_footprint` divides this scaled size by `1_000_000` first, then multiplies by `scalar`: [2](#0-1) 

Compare this to `data_gas`'s Fjord branch, which computes the analogous quantity but correctly multiplies before dividing, preserving precision: [3](#0-2) 

and to `calculate_tx_l1_cost_fjord`, which also multiplies (`saturating_mul`) before the final `wrapping_div`: [4](#0-3) 

Because `jovian_da_footprint` truncates the integer division to a whole number of "size units" (losing up to `999_999` out of `1_000_000` scaled units of precision) *before* multiplying by `scalar`, the resulting gas footprint is systematically rounded down relative to the mathematically correct `estimated_size * scalar / 1_000_000`. For any `scalar > 1` this loss is amplified rather than merely truncated once at the end.

### Impact Explanation
`jovian_da_footprint` feeds into block/transaction resource metering under the Jovian upgrade — it is consumed by the block executor (`crates/common/evm/src/executor/block_executor.rs`) and the EVM2 executor (`crates/common/evm2/src/executor.rs`), which use `da_footprint_gas_scalar`-gated DA-footprint gas to account for a transaction's L1 data-availability footprint against block resource limits. [5](#0-4) 
Systematic underestimation of this metered quantity means transactions can be admitted/charged less DA-footprint gas than the protocol-correct amount, understating the DA resource consumption accounted for at the block-building/resource-metering layer. This is a metering-accuracy defect in a chain-consensus-relevant calculation reachable simply by submitting ordinary transactions (the input size directly determines `tx_estimated_size_fjord`), and it diverges from the correct mul-before-div pattern used everywhere else in the same module for equivalent fee math.

### Likelihood Explanation
This code path executes for every transaction once Jovian is active and a `da_footprint_gas_scalar` is configured — no special privilege or malicious behavior is required, only a normal transaction whose FastLZ-estimated size is not a clean multiple of `1e6`, which is the common case. The bug is deterministic and always present under Jovian with a configured scalar, not merely a theoretical edge case.

### Recommendation
Reorder the arithmetic in `jovian_da_footprint` to multiply before dividing, mirroring `data_gas`'s Fjord branch and `calculate_tx_l1_cost_fjord`:
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
This preserves full precision until the final division, consistent with the rest of the L1 fee/DA-footprint math in this file.

### Proof of Concept
Given `tx_estimated_size_fjord(input) = 1_500_001` (i.e., 1.500001 compressed bytes scaled by 1e6) and `scalar = 3`:
- Correct (mul-then-div): `1_500_001 * 3 / 1_000_000 = 4_500_003 / 1_000_000 = 4` (floor).
- Current buggy (div-then-mul), per `crates/common/l1-fees/src/params.rs:94-97`: `1_500_001 / 1_000_000 = 1`, then `1 * 3 = 3`.

The current implementation returns `3` instead of the correct `4`, an understatement that grows proportionally with `scalar` and recurs on essentially every transaction whose scaled estimated size is not an exact multiple of `1_000_000`. [6](#0-5)

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

**File:** crates/common/l1-fees/src/params.rs (L230-241)
```rust
    fn jovian_da_footprint_zero_without_scalar() {
        // No DA scalar (pre-Jovian / unconfigured) yields no footprint.
        assert_eq!(params().jovian_da_footprint(&[0x02, 0xAB, 0xCD]), 0);
    }

    #[test]
    fn jovian_da_footprint_uses_min_size_and_scalar() {
        // A small input floors at the minimum FastLZ size (100 * 1e6); divided by 1e6 that is 100
        // compressed bytes, times the scalar.
        let p = L1FeeParams { da_footprint_gas_scalar: Some(U256::from(7)), ..Default::default() };
        assert_eq!(p.jovian_da_footprint(&[0x02, 0xAB, 0xCD]), 100 * 7);
    }
```
