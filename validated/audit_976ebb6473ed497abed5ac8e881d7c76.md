### No vulnerability found for this question.

Analysis: The `distribute_gas` function's per-index allocation `to_assign = (unused_gas as u128 * weight.0 as u128 / gas_weight_sum) as u64` is a floor of each receiver's proportional share [1](#0-0) . Because each `to_assign` is a floor of the exact fractional share, the sum of these floors across all-but-the-last entries is always less than or equal to `unused_gas` — this is a mathematical property of floor division applied to values summing exactly to `unused_gas`, not a flaw. The last iterator element intentionally absorbs the leftover `remainder = unused_gas - distributed`, which by construction is always non-negative and bounded by at most `N-1` gas units (where `N` is the number of weighted actions), since it equals the sum of the fractional rounding losses from all prior entries [2](#0-1) . The function asserts `assert_eq!(unused_gas.as_gas(), distributed)` before returning, which enforces the exact metering-totality invariant the question is concerned about [3](#0-2) .

This means:
1. No receiver can ever receive more gas than `unused_gas` in total across all receivers — the sum is exact by construction and enforced by the assertion.
2. The extra amount the "last" receiver gets due to rounding is bounded by the small number of weighted actions in the batch (practically capped by per-receipt/per-transaction action limits), not by `unused_gas` or `u64::MAX` weight values — using `GasWeight(u64::MAX)` does not change this bound, since the division `u128::from(unused_gas) * weight.0 / gas_weight_sum` is computed in `u128` precision precisely to avoid overflow before the final `as u64` cast, and the cast is safe because the mathematical result cannot exceed `unused_gas.as_gas()`.
3. This is not free/extra gas conjured from nowhere — it is gas that was already "unused" and destined to be redistributed among the same batch's weighted receipts; the attacker already fully controls which accounts receive these receipts (they are actions the attacker's own contract scheduled), so shifting a few rounding-remainder gas units to the last-indexed action doesn't grant capability beyond what the attacker's own weight allocation already permits.

The described flow does not violate the metering-totality invariant, does not overflow, and does not grant a receiver gas beyond `unused_gas`, so there is no fund loss, gas inflation, or authorization escalation reachable via this code path.

### Citations

**File:** runtime/runtime/src/receipt_manager.rs (L678-684)
```rust
            let to_assign =
                (u128::from(unused_gas.as_gas()) * weight.0 as u128 / gas_weight_sum) as u64;
            action.gas =
                action.gas.checked_add(Gas::from_gas(to_assign)).ok_or(IntegerOverflowError)?;
            distributed = distributed
                .checked_add(to_assign)
                .unwrap_or_else(|| panic!("gas computation overflowed"));
```

**File:** runtime/runtime/src/receipt_manager.rs (L685-692)
```rust
            if gas_weight_iterator.peek().is_none() {
                let remainder = unused_gas.as_gas().wrapping_sub(distributed);
                distributed = distributed
                    .checked_add(remainder)
                    .unwrap_or_else(|| panic!("gas computation overflowed"));
                action.gas =
                    action.gas.checked_add(Gas::from_gas(remainder)).ok_or(IntegerOverflowError)?;
            }
```

**File:** runtime/runtime/src/receipt_manager.rs (L694-694)
```rust
        assert_eq!(unused_gas.as_gas(), distributed);
```
