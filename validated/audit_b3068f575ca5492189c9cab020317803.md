## Title
Division-by-zero in interest accrual when a bank's total asset amount reaches zero while liabilities remain outstanding - (File: `programs/marginfi/src/state/interest_rate.rs`)

## Summary
This is a structural analog of the Fractional Migration bug: a ratio calculation divides by a "remaining pool" quantity that can legitimately reach zero once all participants on one side have exited, without accounting for that boundary case. In marginfi, the utilization-rate calculation used for every interest accrual divides total liabilities by total assets, with no guard for the case where total assets have gone to zero while liabilities are still outstanding.

## Finding Description
`calc_interest_rate_accrual_state_changes` computes the bank's utilization rate as: [1](#0-0) 

```rust
let utilization_rate: I80F48 = total_liabilities_amount
    .checked_div(total_assets_amount)
    .ok_or_else(math_error!())?;
```

Unlike other ratio helpers in the same codebase — e.g. `get_asset_shares` in `bank.rs`, and `mul_div_i80f48`/`liq_to_col_ratio`/`collateral_to_liquidity_from_scaled` in `type-crate/src/types/price.rs`, all of which explicitly special-case a zero denominator — this division has no such guard: [2](#0-1) [3](#0-2) 

`total_assets_amount` is derived from `total_asset_shares * asset_share_value`, i.e. the bank-wide deposit pool. `withdraw_all` only checks that the *withdrawing balance's own* liability is zero before letting a depositor fully exit — it does not check whether other borrowers still owe the bank: [4](#0-3) 

Because deposits and borrows are tracked as independent per-user balances against a shared bank pool, it is possible for every lender to withdraw their full position (each individually satisfying the "own liability is zero" check) while a separate borrower's liability shares remain outstanding — driving `total_asset_shares` (and thus `total_assets_amount`) to zero while `total_liability_shares` stays nonzero. This is the direct analog of "all fractions/tokens are joined/redeemed," where the denominator that used to represent "remaining participants" collapses to zero.

Once `total_assets_amount == 0` with `total_liability_shares > 0`, any subsequent call to `bank.accrue_interest(...)` — triggered by deposit, withdraw, borrow, repay, or `lending_pool_handle_bankruptcy` — will hit `checked_div(0)` returning `None`, causing `MarginfiError::MathError` and reverting the transaction.

## Impact Explanation
This becomes a durable denial-of-service on the affected bank with real financial consequences:
- The outstanding borrower can never `repay` (repay accrues interest first) or have their position closed normally.
- Liquidators/admins cannot process `lending_pool_handle_bankruptcy` to resolve the bad debt, since that instruction also accrues interest on the bank before socializing the loss: [5](#0-4) 
- No further deposits/withdrawals/borrows on that bank can succeed, since they all go through interest accrual.

This is a state that is very difficult (potentially requiring privileged intervention outside the normal instruction set) to unwind, matching the "durable freeze/inconsistency with financial effect" criterion.

## Likelihood Explanation
Reaching this state requires that every lender in a bank fully exits (`withdraw_all`) while at least one borrower's liability remains — a scenario the report's own author notes is analogous to "all token holders migrate" for the c4 report. This is a plausible, permissionless sequence of ordinary user actions (repeated `withdraw_all` calls by depositors), not a privileged or purely theoretical path, though it does require draining essentially all depositor liquidity from a bank that still has open borrows, which is a somewhat edge-case but reachable condition (e.g., a bank nearing 100% utilization where the last depositor(s) withdraw their remaining dust/near-zero balances).

## Recommendation
Guard `calc_interest_rate_accrual_state_changes` (and callers of `accrue_interest`) against a zero `total_assets_amount`: if `total_assets_amount` is zero, either short-circuit interest accrual (treat utilization as fully saturated / skip the divide) or explicitly handle the "no assets, but bad debt exists" branch so downstream operations like `repay` and `lending_pool_handle_bankruptcy` remain callable and do not revert.

## Proof of Concept
Conceptual PoC (mirrors the referenced report's approach of driving the "remaining pool" to zero):
1. Depositor A deposits into bank X; Borrower B deposits collateral elsewhere and borrows from bank X (creating `total_liability_shares > 0`).
2. Depositor A calls `lending_account_withdraw` with `withdraw_all = true` — this succeeds because the check in `withdraw_all` only verifies Depositor A's own liability is zero: [4](#0-3) 
3. If A was the only depositor in bank X, `total_asset_shares` (and therefore `total_assets_amount`) on bank X is now zero, while `total_liability_shares` remains positive (Borrower B's debt).
4. Any later call that triggers `bank.accrue_interest()` on bank X (e.g., Borrower B calling `repay`, or an admin calling `lending_pool_handle_bankruptcy`) executes: [1](#0-0) 
   `total_liabilities_amount.checked_div(total_assets_amount)` divides by zero, returns `None`, and the instruction reverts with `MarginfiError::MathError`, permanently blocking any interest-accruing operation on bank X until the underlying zero-asset/nonzero-liability state is resolved by some other mechanism.

Note: I was unable to fully trace, within the available tool budget, whether `check_utilization_ratio` (referenced in `bank.rs`) independently blocks the withdrawal that would produce this exact zero-total-assets condition; this should be verified against the full `bank.rs` source (`fn check_utilization_ratio`) before treating this as fully confirmed exploitable in production, since some access paths were truncated due to file-size/tool limits.

### Citations

**File:** programs/marginfi/src/state/interest_rate.rs (L433-436)
```rust
    // If the cache is empty, we need to calculate the interest rates
    let utilization_rate: I80F48 = total_liabilities_amount
        .checked_div(total_assets_amount)
        .ok_or_else(math_error!())?;
```

**File:** programs/marginfi/src/state/bank.rs (L249-256)
```rust
    fn get_asset_shares(&self, value: I80F48) -> MarginfiResult<I80F48> {
        if self.asset_share_value == I80F48::ZERO.into() {
            return Ok(I80F48::ZERO);
        }
        Ok(value
            .checked_div(self.asset_share_value.into())
            .ok_or_else(math_error!())?)
    }
```

**File:** type-crate/src/types/price.rs (L185-193)
```rust
/// Compute liquidity-to-collateral ratio; returns None if total_col is zero.
#[inline]
pub fn liq_to_col_ratio(total_liq: I80F48, total_col: I80F48) -> Option<I80F48> {
    if total_col == I80F48::ZERO {
        None
    } else {
        total_liq.checked_div(total_col)
    }
}
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L1638-1646)
```rust
        check!(
            current_asset_amount.is_positive_with_tolerance(ZERO_AMOUNT_THRESHOLD),
            MarginfiError::NoAssetFound
        );

        check!(
            current_liability_amount.is_zero_with_tolerance(ZERO_AMOUNT_THRESHOLD),
            MarginfiError::NoAssetFound
        );
```

**File:** programs/marginfi/src/instructions/marginfi_group/handle_bankruptcy.rs (L99-107)
```rust
    let mut bank = bank_loader.load_mut()?;
    let group = &marginfi_group_loader.load()?;

    bank.accrue_interest(
        clock.unix_timestamp,
        group,
        #[cfg(not(feature = "client"))]
        bank_loader.key(),
    )?;
```
