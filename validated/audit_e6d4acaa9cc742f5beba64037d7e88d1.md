### Title
Division-before-multiplication in `L1FeeParams::jovian_da_footprint` causes DA-footprint gas to be underscaled, allowing the per-block/per-transaction Jovian DA-footprint budget to be under-charged - (File: `crates/common/l1-fees/src/params.rs`)

### Summary
`L1FeeParams::jovian_da_footprint` computes the Jovian DA-footprint gas by dividing the FastLZ-estimated, `1e6`-scaled compressed size *before* multiplying by the DA-footprint scalar, instead of multiplying first and dividing last as every sibling fee function in the same file does. This is the same division-before-multiplication precision-loss pattern described in the referenced GMX report, applied here to the OP-stack/Base Jovian DA-footprint accounting that gates block building and resource metering.

### Finding Description
`tx_estimated_size_fjord` returns the compressed transaction size scaled by `1e6` (a fixed-point representation), and the correct pattern used elsewhere in the file first multiplies by the relevant scalar and only divides by the scaling constant at the very end, e.g. `calculate_tx_l1_cost_fjord`: [1](#0-0) 

and `data_gas`: [2](#0-1) 

However, `jovian_da_footprint` reverses this order — it divides by `1_000_000` first and only then multiplies by `scalar`: [3](#0-2) 

Because `wrapping_div(1_000_000)` truncates any remainder before the multiplication by `scalar` occurs, up to `999_999` units of the scaled estimated size are discarded per call, and that lost precision is then amplified (rather than corrected) by the subsequent multiplication — exactly the "division before multiplication" defect described in the source report for `PositionUtils.getPositionPnlUsd()`/`DecreasePositionUtils.decreasePosition()`. The function's own doc comment even describes the intended order ("divided back down by `1e6` and multiplied by the DA-footprint gas scalar") — but this documented order is precisely the reversed, precision-losing order, not the corrected `multiply-then-divide` order used by every other fee helper in this file.

The `da_footprint_used` field this feeds is tracked by the block executor specifically for the Jovian DA-footprint block-limit mechanism: [4](#0-3) 

### Impact Explanation
`jovian_da_footprint` feeds the Jovian DA-footprint block-limit accounting (`da_footprint_used`), which is a resource-metering/block-building gate on Base's execution layer — reachable indirectly by any unprivileged transaction sender whose transaction's compressed size and the block's aggregate DA usage are measured through this function. Systematic under-scaling of DA-footprint gas (losing up to `999,999/1e6` of a unit per transaction) means the computed DA footprint is consistently smaller than the correct value produced by multiply-then-divide. This allows more real DA bytes to be packed into a block than the protocol's DA-footprint limit intends, undermining the resource-metering guarantee that this mechanism exists to enforce (per the referenced OP-stack Jovian DA-footprint block-limit spec). This is a protocol-accounting correctness bug in a resource-metering/gas-accounting path, not an isolated arithmetic curiosity — it affects the same class of on-chain resource accounting the audit report targeted (systematic precision loss in a financially/consensus-relevant fixed-point computation).

### Likelihood Explanation
This code path executes on every transaction/block once the `da_footprint_gas_scalar` is configured (post-Jovian), and is deterministic — it will misprice DA footprint on every call where the divide-first step truncates a non-zero remainder, not merely under edge-case conditions. Because Jovian's DA-footprint metering is described as a protocol-level, consensus-relevant limit, any deterministic under-accounting here would need to be reproduced identically by all Base nodes to avoid a chain split; if this same reversed-order function is implemented (or referenced as "the" canonical Rust implementation) inconsistently versus the Go/op-geth reference implementation, it is a strong candidate for a state-transition/consensus divergence. I could not fully confirm from the available context whether op-geth's reference Jovian implementation uses the same (reversed) order — if it does, this is merely a spec-following precision loss (Medium/High per the audit-report class); if it does not, this becomes a chain-split-class bug. This uncertainty should be resolved by comparing against the op-geth Jovian DA-footprint reference formula.

### Recommendation
Refactor `jovian_da_footprint` to multiply before dividing, matching the pattern already used in `calculate_tx_l1_cost_fjord` and `data_gas`:
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
Add a regression test asserting equivalence/parity against the op-geth Jovian reference implementation to guarantee no consensus divergence, and verify the ordering against the OP-stack Jovian DA-footprint spec before merging.

### Proof of Concept
Given `tx_estimated_size_fjord(input) = 1_999_999` (i.e., `1.999999` compressed bytes scaled by `1e6`) and `scalar = 3`:

- Current (divide-then-multiply) code: `1_999_999 / 1_000_000 = 1` (remainder `999_999` truncated), then `1 * 3 = 3`.
- Correct (multiply-then-divide) code: `1_999_999 * 3 = 5_999_997`, then `5_999_997 / 1_000_000 = 5`.

The current implementation returns `3` instead of the correct `5`, a ~40% underestimate of the DA-footprint gas charged for that transaction, demonstrating the precision loss and its potential to let transactions collectively exceed the intended DA-footprint budget while reporting a value under the limit. [3](#0-2)

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

**File:** crates/common/evm/src/executor/block_executor.rs (L46-51)
```rust
    pub gas_used: u64,
    /// DA footprint.
    ///
    /// This is only set for blocks post-Jovian activation.
    /// See [DA footprint block limit spec](https://github.com/ethereum-optimism/specs/blob/main/specs/protocol/jovian/exec-engine.md#da-footprint-block-limit)
    pub da_footprint_used: u64,
```
