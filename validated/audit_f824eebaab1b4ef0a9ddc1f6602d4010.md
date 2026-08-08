### Title
Sentinel-value collision in `PrioritizationFee::get_min_compute_unit_price` masks a real `u64::MAX` compute-unit price as "no fee" - ([File: runtime/src/prioritization_fee.rs])

### Summary
`PrioritizationFee` uses `u64::MAX` as both the "unset" default and a theoretically legal `compute_unit_price` value. If a transaction's `compute_unit_price` equals exactly `u64::MAX`, `PrioritizationFee::update` leaves `min_compute_unit_price` unchanged at the sentinel, and `get_min_compute_unit_price` then reports `None` instead of `Some(u64::MAX)`, causing `getRecentPrioritizationFees` to return `0` for that slot instead of the true fee.

### Finding Description
`PrioritizationFee::default()` initializes `min_compute_unit_price: u64::MAX` as the "unset" sentinel [1](#0-0) . `update()` only overwrites this field when `compute_unit_price < self.min_compute_unit_price` [2](#0-1) . If the sole prioritized transaction in the slot has `compute_unit_price == u64::MAX`, the comparison `u64::MAX < u64::MAX` is false, so the field stays at its default sentinel value, indistinguishable from "no prioritized transaction seen." `get_min_compute_unit_price` then evaluates `(self.min_compute_unit_price != u64::MAX).then_some(...)`, which returns `None` in this case [3](#0-2) . Downstream, `PrioritizationFeeCache::get_prioritization_fees`, which backs the RPC `getRecentPrioritizationFees` path, calls `.get_min_compute_unit_price().unwrap_or_default()`, silently substituting `0` for the true `u64::MAX` fee [4](#0-3) . This is a genuine sentinel-collision bug: the code cannot distinguish "block had a real fee of u64::MAX" from "block had no prioritized transactions."

However, reaching this state requires a transaction whose `compute_unit_price` (in the internal microlamports representation used by `update`, per `transaction_configuration.compute_unit_price_in_microlamports()`) is exactly `u64::MAX` to actually be processed and included in a block. The prioritization fee paid is `compute_unit_limit * compute_unit_price / 1_000_000` lamports, deducted from the fee payer. With `compute_unit_price = u64::MAX` (~1.8×10^19) and any non-trivial compute unit limit, the required lamports vastly exceed the total SOL supply (~5×10^17 lamports), so such a transaction cannot pass balance/fee checks and would never be included in a real block under Agave's transaction validation and fee-payment logic. I was not able to fully verify the exact fee/lamports overflow-handling code path (`clap-utils`/`runtime-transaction` compute budget parsing) within available context, so I cannot rule out an alternate crafted case (e.g., zero compute unit limit combined with rounding) that hits exactly `u64::MAX` at lower cost, but no such path was found in the reachable code.

### Impact Explanation
If triggered, this is a data-correctness bug: `getRecentPrioritizationFees` would report `0` for a slot that actually had a maximal prioritization fee, giving RPC clients an incorrect (under-reported) fee estimate for that slot. This matches Agave's "incorrect data returned by RPC" bounty category — a wrong-value response, not a crash, DoS, or consensus violation. It affects a single slot's cached record and does not corrupt other slots or persistent state.

### Likelihood Explanation
Triggering it requires a transaction with `compute_unit_price` (as computed internally) exactly equal to `u64::MAX` to be legitimately processed and to be the only prioritized transaction for that slot's cached entry. Given the economics of fee payment (fee scales with `compute_unit_price`), paying a `u64::MAX` compute-unit price is economically infeasible relative to total SOL supply, making real-world occurrence effectively impossible outside of test/toy environments (e.g., `Bank::default_for_tests()`) where balance checks may be bypassed. The bug is reproducible deterministically in unit tests calling `PrioritizationFee::update` directly, as the audit prompt suggests, but attacker-controlled end-to-end exploitation via a real submitted transaction is not practically feasible on a live cluster due to fee-payment/balance validation.

### Recommendation
Use an `Option<u64>` (or a separate boolean/`bool` "has_any" flag) for `min_compute_unit_price` instead of overloading `u64::MAX` as both a sentinel and a legal value, e.g., change the field to `Option<u64>` and update it via `.map_or(cu_price, |min| min.min(cu_price))` (mirroring `PrioritizationFeeMetrics::update_compute_unit_price`), removing the `!= u64::MAX` sentinel check entirely.

### Proof of Concept
```rust
// runtime/src/prioritization_fee.rs (add to #[cfg(test)] mod tests)
#[test]
fn test_u64_max_compute_unit_price_masked_as_none() {
    let mut prioritization_fee = PrioritizationFee::default();
    assert!(prioritization_fee.get_min_compute_unit_price().is_none());

    // Attacker's sole prioritized transaction has compute_unit_price == u64::MAX
    prioritization_fee.update(u64::MAX, 10, vec![]);

    // BUG: expected Some(u64::MAX), but sentinel collision yields None,
    // which downstream causes get_prioritization_fees() to report 0.
    assert_eq!(
        prioritization_fee.get_min_compute_unit_price(),
        None // demonstrates the masking; a correct implementation should return Some(u64::MAX)
    );
}
```
Expected result: the assertion passes today, demonstrating that a real `u64::MAX` fee is indistinguishable from "no fee," confirming the sentinel-collision bug in `get_min_compute_unit_price`.

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

**File:** runtime/src/prioritization_fee.rs (L177-208)
```rust
    pub fn update(
        &mut self,
        compute_unit_price: u64,
        prioritization_fee: u64,
        writable_accounts: Vec<Pubkey>,
    ) {
        let (_, update_us) = measure_us!({
            if !self.is_finalized {
                if compute_unit_price < self.min_compute_unit_price {
                    self.min_compute_unit_price = compute_unit_price;
                }

                for write_account in writable_accounts {
                    self.min_writable_account_fees
                        .entry(write_account)
                        .and_modify(|write_lock_fee| {
                            *write_lock_fee = std::cmp::min(*write_lock_fee, compute_unit_price)
                        })
                        .or_insert(compute_unit_price);
                }

                self.metrics
                    .accumulate_total_prioritization_fee(prioritization_fee);
                self.metrics.update_compute_unit_price(compute_unit_price);
            } else {
                self.metrics
                    .increment_attempted_update_on_finalized_fee_count(1);
            }
        });

        self.metrics.accumulate_total_update_elapsed_us(update_us);
    }
```

**File:** runtime/src/prioritization_fee.rs (L228-230)
```rust
    pub fn get_min_compute_unit_price(&self) -> Option<u64> {
        (self.min_compute_unit_price != u64::MAX).then_some(self.min_compute_unit_price)
    }
```

**File:** runtime/src/prioritization_fee_cache.rs (L432-451)
```rust
    pub fn get_prioritization_fees(&self, account_keys: &[Pubkey]) -> Vec<(Slot, u64)> {
        self.cache
            .read()
            .unwrap()
            .iter()
            .map(|(slot, slot_prioritization_fee)| {
                let mut fee = slot_prioritization_fee
                    .get_min_compute_unit_price()
                    .unwrap_or_default();
                for account_key in account_keys {
                    if let Some(account_fee) =
                        slot_prioritization_fee.get_writable_account_fee(account_key)
                    {
                        fee = std::cmp::max(fee, account_fee);
                    }
                }
                (*slot, fee)
            })
            .collect()
    }
```
