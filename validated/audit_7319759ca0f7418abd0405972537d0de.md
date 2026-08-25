### Title
Unchecked (non-saturating) multiplication in transaction fee burn calculation - ([File: runtime/src/bank/fee_distribution.rs])

### Summary
`Bank::calculate_reward_and_burn_fee_details` computes the fee burn amount using a raw `*` and `/` operation instead of `saturating_mul`/`checked_mul`, while every other arithmetic operation in the same function and file consistently uses `saturating_add`/`saturating_sub`. This is the same inconsistency pattern flagged in the external report: unchecked math mixed into a codebase that otherwise uses checked/saturating math for lamport accounting.

### Finding Description
In `calculate_reward_and_burn_fee_details`:

```rust
pub fn calculate_reward_and_burn_fee_details(
    &self,
    fee_details: &CollectorFeeDetails,
) -> FeeDistribution {
    let burn = fee_details.transaction_fee * self.burn_percent() / 100;
    let deposit = fee_details
        .priority_fee
        .saturating_add(fee_details.transaction_fee.saturating_sub(burn));
    FeeDistribution { deposit, burn }
}
``` [1](#0-0) 

`fee_details.transaction_fee` is a `u64` field of `CollectorFeeDetails` that is accumulated across all transactions processed in a bank/slot via `saturating_add`:

```rust
pub(crate) fn accumulate(&mut self, fee_details: &FeeDetails) {
    self.transaction_fee = self
        .transaction_fee
        .saturating_add(fee_details.transaction_fee());
    ...
}
``` [2](#0-1) 

Because `accumulate` saturates rather than errors, `transaction_fee` is permitted to reach values up to `u64::MAX`. When `calculate_reward_and_burn_fee_details` is later called (from `distribute_transaction_fee_details`, at end-of-slot fee distribution) [3](#0-2) , and also from `calculate_reward_for_transaction`, which is on the transaction-forwarding/prioritization path (`core/src/forwarding_stage.rs::calculate_priority`) [4](#0-3) , the unchecked `transaction_fee * self.burn_percent()` multiplication can overflow `u64` if `transaction_fee` exceeds `u64::MAX / 50` (burn_percent is a constant 50%) [5](#0-4) .

Note that unlike the rest of this same function/file — which explicitly guards against overflow (e.g. `deposit_fees` uses `checked_add_lamports` and maps overflow to `DepositFeeError::LamportOverflow`, and `capitalization.fetch_sub` uses a saturating burn total) — this specific multiplication has no such protection, exactly matching the report's described pattern of inconsistent checked-vs-unchecked math.

### Impact Explanation
- If Rust integer-overflow checks are enabled for the build (as they commonly are in Solana/Agave release profiles for safety), this multiplication will **panic**, crashing the validator process (denial of service / consensus halt) during end-of-slot fee distribution or during transaction-forwarding priority calculation.
- If overflow checks are not enabled, the multiplication silently wraps, producing an incorrect (much smaller or arbitrary) `burn` value. Since `deposit = priority_fee + (transaction_fee - burn)` uses `saturating_sub`, a wrapped/undersized `burn` value directly inflates `deposit`, which is credited to the block reward collector and also affects `capitalization.fetch_sub(total_burn)` — i.e., an incorrect burn amount causes incorrect capitalization accounting and potentially over-crediting the leader's fee collector account, a state-mutation/accounting-correctness issue.

I was unable to confirm from the index whether the Agave release build profile enables `overflow-checks` (no `overflow-checks` setting was found under `[profile]` sections in `Cargo.toml`), so I cannot definitively state whether the practical failure mode is a panic or silent miscalculation — this should be verified by checking the actual `[profile.release]` configuration in the repository (it was not indexed in the search results returned).

### Likelihood Explanation
Reaching `transaction_fee > u64::MAX / 50` (~3.69×10^17 lamports) within a single slot requires an extraordinarily large aggregate of transaction fees accumulated in that bank via normal `lamports_per_signature` fee schedules — this is not readily achievable through ordinary transaction volume under default fee parameters, which limits practical likelihood. However, the root cause (missing saturating/checked arithmetic where all sibling code paths use it) is a genuine code defect matching the reported bug class, and the affected function is reachable on both the per-slot fee-distribution path and the per-transaction forwarding/prioritization path invoked for user transactions.

### Recommendation
Replace the raw multiplication/division with saturating or checked arithmetic, consistent with the rest of the file:

```diff
- let burn = fee_details.transaction_fee * self.burn_percent() / 100;
+ let burn = fee_details
+     .transaction_fee
+     .saturating_mul(self.burn_percent())
+     .saturating_div(100);
```

This eliminates the possibility of panic-on-overflow or silent wraparound while preserving intended semantics (the value is already bounded to 50% of `transaction_fee`, so `saturating_mul` followed by division is a safe drop-in replacement).

### Proof of Concept
1. Construct or simulate a `Bank` whose `collector_fee_details.transaction_fee` accumulator has been driven, via repeated `CollectorFeeDetails::accumulate` calls (each using `saturating_add`), to a value greater than `u64::MAX / 50`.
2. Call `Bank::calculate_reward_and_burn_fee_details` (directly, or indirectly via `distribute_transaction_fee_details` at end-of-slot, or via `calculate_reward_for_transaction`/`forwarding_stage::calculate_priority` on the transaction-forwarding path).
3. Observe that `fee_details.transaction_fee * self.burn_percent()` overflows `u64`: with overflow checks enabled this panics the validator thread; without them, it wraps and yields an incorrect `burn`, which is used to compute `deposit` and adjust `capitalization`, corrupting fee/capitalization accounting for that slot.

### Citations

**File:** runtime/src/bank/fee_distribution.rs (L69-77)
```rust
    pub(super) fn distribute_transaction_fee_details(&self) {
        let fee_details = self.collector_fee_details.read().unwrap();

        let FeeDistribution { deposit, burn } =
            self.calculate_reward_and_burn_fee_details(&fee_details);

        let total_burn = self.deposit_or_burn_fee(deposit).saturating_add(burn);
        self.capitalization.fetch_sub(total_burn, Relaxed);
    }
```

**File:** runtime/src/bank/fee_distribution.rs (L97-106)
```rust
    pub fn calculate_reward_and_burn_fee_details(
        &self,
        fee_details: &CollectorFeeDetails,
    ) -> FeeDistribution {
        let burn = fee_details.transaction_fee * self.burn_percent() / 100;
        let deposit = fee_details
            .priority_fee
            .saturating_add(fee_details.transaction_fee.saturating_sub(burn));
        FeeDistribution { deposit, burn }
    }
```

**File:** runtime/src/bank/fee_distribution.rs (L108-115)
```rust
    const fn burn_percent(&self) -> u64 {
        // NOTE: burn percent is statically 50%, in case it needs to change in the future,
        // burn_percent can be bank property that being passed down from bank to bank, without
        // needing fee-rate-governor
        static_assertions::const_assert!(solana_fee_calculator::DEFAULT_BURN_PERCENT <= 100);

        solana_fee_calculator::DEFAULT_BURN_PERCENT as u64
    }
```

**File:** runtime/src/bank.rs (L295-303)
```rust
impl CollectorFeeDetails {
    pub(crate) fn accumulate(&mut self, fee_details: &FeeDetails) {
        self.transaction_fee = self
            .transaction_fee
            .saturating_add(fee_details.transaction_fee());
        self.priority_fee = self
            .priority_fee
            .saturating_add(fee_details.prioritization_fee());
    }
```

**File:** core/src/forwarding_stage.rs (L618-620)
```rust
    let reward = bank
        .calculate_reward_and_burn_fee_details(&CollectorFeeDetails::from(fee_details))
        .get_deposit();
```
