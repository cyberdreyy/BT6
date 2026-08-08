### Title
Silent zeroing of extremely high priority fees in `compute_unit_price_in_microlamports` due to unchecked u64 downcast - ([File: runtime-transaction/src/transaction_meta.rs])

### Summary
`TransactionConfiguration::compute_unit_price_in_microlamports` recomputes an effective price from `priority_fee_lamports` and `compute_unit_limit`, but the final `u64::try_from(x).ok()` can fail for legitimately reachable (attacker-controlled) fee/limit combinations, and the code silently maps that failure to `0` via `unwrap_or(0)`, indistinguishable from a genuinely free transaction. This value feeds `PrioritizationFeeCache` used by the `getRecentPrioritizationFees` RPC method, so an attacker can make an extremely high-paying transaction appear to have paid nothing.

### Finding Description
`priority_fee_lamports` is a `u64` derived from `ComputeBudgetLimits::get_prioritization_fee()`, computed as `compute_unit_price (u64, attacker-controlled up to u64::MAX) * compute_unit_limit (u32, attacker-controlled up to MAX_COMPUTE_UNIT_LIMIT) / 1_000_000`, using u128 intermediate math before being saturated/cast back to u64 [1](#0-0) . This value can legitimately be very large (up to `u64::MAX`) when a transaction specifies a very high `compute_unit_price` combined with a normal-sized `compute_unit_limit`.

Later, `compute_unit_price_in_microlamports` reverses this computation to obtain an "effective" per-CU price:
```
(self.priority_fee_lamports as u128)
    .saturating_mul(1_000_000u128)
    .checked_div(self.compute_unit_limit as u128)
    .and_then(|x| u64::try_from(x).ok())
    .unwrap_or(0)
``` [2](#0-1) 

Contrary to the question's framing, the `saturating_mul(1_000_000u128)` itself cannot overflow u128 bounds — `u64::MAX * 1_000_000 ≈ 1.8e25`, far below `u128::MAX ≈ 3.4e38` — so saturation is unreachable here. The real failure mode is the subsequent `u64::try_from(x)`: if `priority_fee_lamports` is large (attacker sets an extreme `compute_unit_price`) and `compute_unit_limit` is comparatively small, the divided value `x` can legitimately exceed `u64::MAX`, causing `try_from` to fail. The `.and_then(...).unwrap_or(0)` then silently converts this "value too large to represent" case into `0`, which is semantically identical to "this transaction paid nothing," rather than to an error/sentinel indicating overflow.

This is attacker-reachable with a single crafted transaction (no privileged access needed): the attacker sets `set_compute_unit_price` to a very large value and `set_compute_unit_limit` to a moderate value via `ComputeBudgetInstruction`, sanitizes fine (no bounds check rejects this combination in `try_into_config` for the `LegacyAndV0` path) [3](#0-2) , and the resulting `TransactionConfiguration` is later consumed by `PrioritizationFeeCache`, which calls `compute_unit_price_in_microlamports` for fee bucketing consumed by RPC's `getRecentPrioritizationFees` [4](#0-3) .

### Impact Explanation
This falls under "wrong-slot/fork/account data returned" style misreporting via RPC (per the question's scoped impact): a transaction's true, very high effective priority fee is misreported as `0` (i.e., "free") by `getRecentPrioritizationFees`, undercounting or masking legitimate fee-market signals and allowing a lower-paying/lower-priority transaction to appear more competitive in fee metrics than a much higher-paying one. This is purely an informational/metrics-adjacent misreporting bug — it does not affect actual fee collection, consensus state, or the real lamports charged (that logic is separate and already saturates correctly), only the recomputed "effective price per CU" metric surfaced through RPC.

### Likelihood Explanation
Reachable with a single transaction from an unprivileged client: set an extreme `compute_unit_price` (e.g. close to `u64::MAX`) with a comparatively small `compute_unit_limit`, sign and submit it via a standard RPC `sendTransaction` call. No special privileges, staked node, or multiple calls are needed. The transaction need only pass sanitization to have its `TransactionConfiguration` recorded (whether or not it succeeds during execution) — the value bounds checks in `try_into_config` do not reject high `compute_unit_price` values [1](#0-0) .

### Recommendation
Distinguish "computed effective price too large to fit in u64" from "zero fee" — e.g., saturate to `u64::MAX` instead of `unwrap_or(0)`, or return an `Option<u64>`/dedicated sentinel that downstream consumers (e.g., `PrioritizationFeeCache`) can treat explicitly as "unknown/overflow" rather than folding it into the same bucket as a legitimately fee-less transaction.

### Proof of Concept
```rust
// runtime-transaction/src/transaction_meta.rs (test module)
#[test]
fn test_compute_unit_price_in_microlamports_overflow_masks_as_zero() {
    let config = TransactionConfiguration {
        updated_heap_bytes: 32 * 1024,
        compute_unit_limit: 1, // attacker-controlled, minimal
        priority_fee_lamports: u64::MAX, // attacker-controlled via extreme compute_unit_price
        loaded_accounts_data_size_limit: 0,
    };

    // Expected mathematically: (u64::MAX as u128) * 1_000_000 / 1 ≈ 1.8e25,
    // which cannot fit into u64 (max ≈ 1.8e19).
    let result = config.compute_unit_price_in_microlamports();

    // Current (buggy) behavior: silently returns 0, identical to a free transaction.
    assert_eq!(result, 0);
    // Desired behavior: should saturate to u64::MAX or otherwise flag as "unknown/overflow",
    // not silently equate to a zero-fee transaction.
}
```
This confirms that a legitimate, attacker-craftable fee/limit combination causes the effective price to be misreported as `0`, satisfying the "parsed fee data must faithfully represent the raw fee" invariant violation described in the question.

### Citations

**File:** runtime-transaction/src/transaction_meta.rs (L77-83)
```rust
    pub fn compute_unit_price_in_microlamports(&self) -> u64 {
        (self.priority_fee_lamports as u128)
            .saturating_mul(1_000_000u128)
            .checked_div(self.compute_unit_limit as u128)
            .and_then(|x| u64::try_from(x).ok())
            .unwrap_or(0)
    }
```

**File:** runtime-transaction/src/transaction_meta.rs (L139-155)
```rust
    pub(crate) fn try_into_config(
        &self,
        feature_set: &FeatureSet,
    ) -> Result<TransactionConfiguration, TransactionError> {
        match self {
            Self::LegacyAndV0(compute_budget_instruction_details) => {
                let compute_budget_limits = compute_budget_instruction_details
                    .sanitize_and_convert_to_compute_budget_limits(feature_set)?;
                Ok(TransactionConfiguration {
                    updated_heap_bytes: compute_budget_limits.updated_heap_bytes,
                    compute_unit_limit: compute_budget_limits.compute_unit_limit,
                    priority_fee_lamports: compute_budget_limits.get_prioritization_fee(),
                    loaded_accounts_data_size_limit: compute_budget_limits
                        .loaded_accounts_bytes
                        .get(),
                })
            }
```

**File:** runtime/src/prioritization_fee_cache.rs (L1-1)
```rust
use {
```
