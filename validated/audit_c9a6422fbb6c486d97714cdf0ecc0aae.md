### Title
`PrioritizationFee::get_min_compute_unit_price` sentinel collision causes silent misreporting of fees equal to `u64::MAX` via `getRecentPrioritizationFees` - ([File: runtime/src/prioritization_fee.rs])

### Summary
`PrioritizationFee` uses `u64::MAX` as the internal "unset" sentinel for `min_compute_unit_price`, but a transaction whose `ComputeBudgetInstruction::set_compute_unit_price` is exactly `u64::MAX` is a legitimate value that collides with that sentinel. Once such a transaction lands and the block is finalized, `get_min_compute_unit_price()` cannot distinguish "no prioritized tx observed" from "observed fee == u64::MAX", and returns `None`, which the RPC layer converts into a reported fee of `0` instead of the true value.

### Finding Description
`PrioritizationFee::default()` initializes `min_compute_unit_price: u64::MAX` [1](#0-0)  as the sentinel meaning "no fee recorded yet". `update()` unconditionally tracks the minimum seen `compute_unit_price` in that same field: `if compute_unit_price < self.min_compute_unit_price { self.min_compute_unit_price = compute_unit_price; }` [2](#0-1) . If the only (or minimum) transaction in the block sets `compute_unit_price == u64::MAX`, the comparison `compute_unit_price < self.min_compute_unit_price` (`u64::MAX < u64::MAX`) is `false`, so the field stays at its default `u64::MAX` — indistinguishable from the "unset" state.

`get_min_compute_unit_price()` then applies the sentinel check: `(self.min_compute_unit_price != u64::MAX).then_some(self.min_compute_unit_price)` [3](#0-2) , which returns `None` for this case even though a real transaction with fee `u64::MAX` was processed. `get_writable_account_fee`/`min_writable_account_fees` are unaffected by this specific sentinel (they use `Option`-free `HashMap` with real min-per-account tracking pruned relative to `min_compute_unit_price`, but the block-level minimum is the value exposed by the RPC path used for `getRecentPrioritizationFees` (`PrioritizationFeeCache::get_prioritization_fees`) via `rpc/src/rpc.rs`. The consumer of `get_min_compute_unit_price() == None` treats it as "no prioritized transactions in this slot" and reports `0`, silently converting the highest possible legitimate fee into the lowest possible reported fee — a data-representation collision, not a crash or privilege escalation.

### Impact Explanation
This is a data-integrity/misreporting bug in a public read-only RPC method (`getRecentPrioritizationFees`): the returned prioritization-fee value does not faithfully reflect on-chain fee data for the edge case `compute_unit_price == u64::MAX`. This matches the "wrong-value/misreported account data returned" category from a single low-rate RPC query, achievable purely by submitting one attacker-crafted, otherwise-valid transaction and finalizing it through the normal transaction path, then issuing one `getRecentPrioritizationFees` call.

### Likelihood Explanation
Feasibility depends entirely on whether a transaction can carry `compute_unit_price == u64::MAX` in its `ComputeBudgetInstruction::set_compute_unit_price` and still be accepted/executed/finalized by the cluster (fee/priority calculations are generally saturating and instruction parsing does not appear to reject the literal value `u64::MAX` in the instruction data itself). If such a transaction can be finalized (even if it is deprioritized or rarely lands due to being economically irrational), the collision is deterministic and 100% reproducible for that slot: any observer issuing `getRecentPrioritizationFees` for that slot will get the wrong value. The attacker does not need any elevated privileges — only the ability to submit one transaction.

### Recommendation
Separate "unset" tracking from the actual fee value, e.g. store `min_compute_unit_price: Option<u64>` (mirroring `PrioritizationFeeMetrics::min_compute_unit_price`) instead of overloading `u64::MAX` as a sentinel inside a `u64` field, and update `update()`/`get_min_compute_unit_price()`/`prune_irrelevant_writable_accounts()` accordingly so that a legitimately observed `u64::MAX` fee is preserved and returned as `Some(u64::MAX)`.

### Proof of Concept
```rust
// runtime/src/prioritization_fee.rs (add to #[cfg(test)] mod tests)
#[test]
fn test_min_compute_unit_price_sentinel_collision() {
    let mut prioritization_fee = PrioritizationFee::default();
    assert!(prioritization_fee.get_min_compute_unit_price().is_none());

    // Attacker-controlled transaction sets compute_unit_price == u64::MAX
    prioritization_fee.update(u64::MAX, 0, vec![]);

    // BUG: this should be Some(u64::MAX) since a real tx with that price was observed,
    // but the sentinel collision makes it indistinguishable from "unset".
    assert_eq!(
        prioritization_fee.get_min_compute_unit_price(),
        None, // current (incorrect) behavior
        // expected correct behavior: Some(u64::MAX)
    );
}
```
Differential check: compare the raw transaction's `compute_unit_price` (`u64::MAX`) against the value returned by `getRecentPrioritizationFees` for the slot containing that transaction — the RPC will report `0`/absent instead of `u64::MAX`, confirming the misreporting end-to-end.

### Citations

**File:** runtime/src/prioritization_fee.rs (L164-173)
```rust
impl Default for PrioritizationFee {
    fn default() -> Self {
        PrioritizationFee {
            min_compute_unit_price: u64::MAX,
            min_writable_account_fees: HashMap::new(),
            is_finalized: false,
            metrics: PrioritizationFeeMetrics::default(),
        }
    }
}
```

**File:** runtime/src/prioritization_fee.rs (L184-187)
```rust
            if !self.is_finalized {
                if compute_unit_price < self.min_compute_unit_price {
                    self.min_compute_unit_price = compute_unit_price;
                }
```

**File:** runtime/src/prioritization_fee.rs (L228-230)
```rust
    pub fn get_min_compute_unit_price(&self) -> Option<u64> {
        (self.min_compute_unit_price != u64::MAX).then_some(self.min_compute_unit_price)
    }
```
