### Title
Division-before-multiplication truncation in `jovian_da_footprint` underprices DA-footprint gas - ([File: crates/common/l1-fees/src/params.rs])

### Summary
`L1FeeParams::jovian_da_footprint` divides the FastLZ-estimated compressed size by `1_000_000` *before* multiplying by the DA-footprint gas scalar, instead of multiplying first and dividing last. This is the same division-before-multiplication precision-loss bug class described in the external report (`floorTick = (tick / tickSpacing) * tickSpacing`), and it systematically undercharges the Jovian DA-footprint gas component that feeds into block resource metering.

### Finding Description
`tx_estimated_size_fjord` returns the estimated compressed transaction size *scaled by 1e6* (i.e., `bytes * 1_000_000`, floored/clamped per the Fjord formula): [1](#0-0) 

`jovian_da_footprint` is supposed to convert that scaled size back to whole bytes and multiply by the configured scalar to get a gas value: [2](#0-1) 

```rust
pub fn jovian_da_footprint(&self, input: &[u8]) -> u64 {
    let Some(scalar) = self.da_footprint_gas_scalar else {
        return 0;
    };
    U256::from(tx_estimated_size_fjord(input))
        .wrapping_div(U256::from(1_000_000u64))   // divide first
        .saturating_mul(scalar)                    // multiply second
        .saturating_to::<u64>()
}
```

Because the intermediate `wrapping_div` truncates to an integer byte count *before* the scalar multiplication, any fractional byte lost by that division can never be recovered. Doing the multiplication first (`estimated_size_scaled * scalar / 1_000_000`) would be exact aside from a single final truncation, but performing the division first compounds the truncation across every byte fraction, systematically rounding the DA footprint down. This mirrors exactly the reported analog: `(tick / tickSpacing) * tickSpacing` loses precision that `(tick * tickSpacing) / tickSpacing` would not.

Contrast this with every other fee-scaling function in the same file, which correctly multiplies before dividing, e.g. `calculate_tx_l1_cost_fjord`: [3](#0-2) 
and `calculate_tx_l1_cost_ecotone`/`calculate_tx_l1_cost_bedrock`, which both multiply first and only divide once at the very end. `jovian_da_footprint` is the outlier that divides first.

`jovian_da_footprint` is consumed on the hot block-execution/metering path (block executor and flashblocks state builder), i.e. it is reachable by any unprivileged transaction sender whose transaction is included post-Jovian: [4](#0-3) 

### Impact Explanation
The DA-footprint gas scalar is meant to price/meter data-availability resource usage introduced in Jovian for block-building/resource-metering purposes. Truncating the byte estimate before multiplying by the scalar systematically undercounts the metered gas for every transaction whose FastLZ-estimated size (scaled by 1e6) isn't an exact multiple of 1,000,000 — i.e., almost always. This causes transactions to be charged less DA-footprint gas than they should be, which is a resource-metering/gas-accounting correctness bug: it can let transactions consume more DA/compute resource than they are billed for, understating the block's resource footprint relative to the intended pricing model. This falls in the medium-severity range for a resource-metering accounting flaw (not itself a fund-theft or freezing bug, but a genuine and provable wrong-computation defect on the block-building/metering path).

### Likelihood Explanation
This triggers on every transaction processed under the Jovian upgrade once `da_footprint_gas_scalar` is configured — no special conditions or attacker crafting is needed; it's a deterministic arithmetic truncation present in normal operation for the vast majority of transaction sizes (any size where `tx_estimated_size_fjord(input) % 1_000_000 != 0`).

### Recommendation
Reorder the arithmetic to multiply before dividing, matching the pattern used elsewhere in the same file (e.g. `calculate_tx_l1_cost_fjord`):

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
Using the existing test as a baseline (`crates/common/l1-fees/src/params.rs`):
```rust
let p = L1FeeParams { da_footprint_gas_scalar: Some(U256::from(7)), ..Default::default() };
// tx_estimated_size_fjord returns a scaled size, e.g. 1_999_999 (just under 2 bytes * 1e6)
// Current (divide-then-multiply): 1_999_999 / 1_000_000 = 1 ; 1 * 7 = 7
// Correct (multiply-then-divide): 1_999_999 * 7 = 13_999_993 ; / 1_000_000 = 13
assert_eq!(p.jovian_da_footprint(&some_input_yielding_1_999_999), 7); // current buggy result
// vs. 13 expected under multiply-first semantics
```
The existing regression test `jovian_da_footprint_uses_min_size_and_scalar` only exercises the clamped minimum size (`100 * 1_000_000`), which happens to be an exact multiple of `1_000_000` and therefore doesn't expose the truncation; testing with a non-minimum, non-round input demonstrates the discrepancy.

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

**File:** crates/execution/flashblocks/src/state_builder.rs (L1-22)
```rust
use std::{sync::Arc, time::Instant};

use alloy_consensus::{
    Block, Header, TxReceipt,
    transaction::{Recovered, TransactionMeta},
};
use alloy_eips::Encodable2718;
use alloy_evm::{
    Database as AlloyDatabase,
    block::{StateDB, SystemCaller},
};
use alloy_primitives::B256;
use alloy_rpc_types::TransactionTrait;
use alloy_rpc_types_eth::state::StateOverride;
use base_common_chains::Upgrades;
use base_common_consensus::{BasePrimitives, BaseReceipt, BaseTxEnvelope, Predeploys};
use base_common_evm::{
    BaseHaltReason, L1BlockInfo, ensure_create2_deployer, ensure_eip8130_system_accounts,
};
use base_common_flz::tx_estimated_size_fjord as estimate_tx_compressed_size;
use base_common_rpc_types::{BaseTransactionReceipt, Transaction};
use base_execution_rpc::BaseReceiptBuilder as BaseRpcReceiptBuilder;
```
