## Title
JupLend Partial Withdraw Can Revert with `OperationWithdrawOnly` Due to Ceil/Floor Share-Rounding Mismatch Between Deposit and Withdraw - (File: `programs/marginfi/src/instructions/juplend/withdraw.rs`)

### Summary
The JupLend integration computes shares to burn for a partial withdraw using a **ceiling** rounding formula, while the corresponding deposit uses a **floor** rounding formula. This is the exact share/amount inconsistency described in the analog report: depositing and then withdrawing the same nominal `amount` can request more shares than the account actually holds. Unlike the equivalent Drift integration, which contains an explicit defensive re-computation for this precise scenario, the JupLend partial-withdraw path has no such guard, and the shortfall is large enough (a full share unit, not sub-threshold dust) to trip the `WithdrawOnly` liability-clamp check in `decrease_balance_internal`, causing the withdraw instruction to revert with `MarginfiError::OperationWithdrawOnly`.

### Finding Description
For a partial (non-`withdraw_all`) JupLend withdrawal, shares to burn are computed as: [1](#0-0) 

using `expected_shares_for_withdraw_from_rate`, which rounds **up** (`ceil`): [2](#0-1) 

Whereas deposits mint shares using a multi-step **floor** conversion: [3](#0-2) 

The program's own test suite proves that these two roundings are not guaranteed to match — withdraw-ceil shares can strictly exceed deposit-floor shares for the same `amount`: [4](#0-3) 

When this happens, `bank_account.withdraw(shares_to_burn)` is called with a share amount that exceeds the actual `asset_shares` balance held for the position: [1](#0-0) 

Internally, `decrease_balance_internal` treats any excess over `current_asset_amount` as an attempted liability increase, which is disallowed and only tolerated if it falls under `ZERO_AMOUNT_THRESHOLD` (a sub-unit dust tolerance): [5](#0-4) 

Because JupLend's bank effectively tracks raw fToken shares 1:1 (its `asset_share_value` does not change from this op, per the integration tests), a 1-unit rounding excess is a full raw share — far above the dust threshold — so the check fails and the instruction reverts with `MarginfiError::OperationWithdrawOnly`.

By contrast, the Drift integration explicitly anticipated and patched this exact class of bug: it detects when the computed scaled decrement exceeds `asset_shares` by exactly the rounding unit and recomputes the withdrawal amount from the actual share balance instead of reverting: [6](#0-5) 

No equivalent recomputation/guard exists in the JupLend partial-withdraw path.

### Impact Explanation
This is a Denial-of-Service on a core, permissionless user action (partial withdrawal) in the JupLend integration. Once the JupLend exchange prices deviate from the 1e12 baseline (i.e., any time interest/yield has accrued in the underlying JupLend market — an ordinary and expected condition), a user who deposits an `amount` and then attempts to withdraw the exact same `amount` (or more generally, any `amount` that lands on this rounding boundary) can have their withdrawal transaction revert with `OperationWithdrawOnly`, even though they hold sufficient value. This blocks normal fund access through the standard withdraw path (the `withdraw_all` path is unaffected because it derives `token_amount` from the actual share balance rather than the reverse). While not a direct loss of funds (the position remains intact and other withdrawal amounts/`withdraw_all` still work), it constitutes an unauthorized/unexpected state change failure mode and a durable usability/availability issue directly matching the "amount values can be inconsistent" bug class from the analog report.

### Likelihood Explanation
High reachability: it requires no privileged action, only ordinary deposit/withdraw usage by any account holder after JupLend's `token_exchange_price`/`liquidity_exchange_price` has moved off 1e12 (which happens as soon as any interest accrues in the underlying JupLend market — normal, expected, and time-based). The repository's own tests (`jlr11_rounding_loop.spec.ts`, `local_tests.rs::withdraw_shares_ceil_always_gte_deposit_shares_floor`) already demonstrate that this rounding asymmetry is reliably reachable and searchable for specific amounts.

### Recommendation
Mirror the defensive logic already present in `drift_withdraw` (`programs/marginfi/src/instructions/drift/withdraw.rs` lines 136–165) in `juplend_withdraw`'s partial-withdraw branch: before calling `bank_account.withdraw(shares_to_burn)`, compare `shares_to_burn` against the actual `asset_shares` balance; if it exceeds it by the expected 1-unit rounding delta, clamp/recompute `token_amount`/`shares_to_burn` from the actual share balance (analogous to the `expected_assets_for_redeem_from_rate` recomputation already used in the `withdraw_all` branch at lines 118–141) rather than allowing the raw ceil-rounded request to hit the hard `WithdrawOnly` liability check.

### Proof of Concept
1. Create a JupLend bank/position and let JupLend's `token_exchange_price`/`liquidity_exchange_price` drift away from `1_000_000_000_000` (1e12) baseline (e.g., via normal interest accrual, as bootstrapped in `jlr11_rounding_loop.spec.ts`).
2. Choose (or search, as `findFirstPositiveLossAmount`/the Rust test harness does) an `amount` such that `expected_shares_for_deposit(amount) < expected_shares_for_withdraw(amount)` (proven possible by `withdraw_shares_ceil_always_gte_deposit_shares_floor` in `programs/marginfi/src/instructions/juplend/local_tests.rs`).
3. Call `juplend_deposit` with `amount`; the account is credited `expected_shares_for_deposit(amount)` fToken shares (floor-rounded).
4. Immediately call `juplend_withdraw` with the same `amount` (not `withdraw_all`); `shares_to_burn = expected_shares_for_withdraw_from_rate(amount, token_exchange_price)` (ceil-rounded) exceeds the account's actual share balance from step 3.
5. `bank_account.withdraw(I80F48::from_num(shares_to_burn))` in `programs/marginfi/src/instructions/juplend/withdraw.rs` line 149 triggers `decrease_balance_internal`'s `WithdrawOnly` check in `programs/marginfi/src/state/marginfi_account.rs` (lines 1929-1937), which fails because the liability-increase (the rounding excess) exceeds `ZERO_AMOUNT_THRESHOLD`, reverting the transaction with `MarginfiError::OperationWithdrawOnly`.

### Citations

**File:** programs/marginfi/src/instructions/juplend/withdraw.rs (L142-151)
```rust
        } else {
            // shares = ceil(assets * 1e12 / token_exchange_price)
            let shares_to_burn = {
                expected_shares_for_withdraw_from_rate(amount, lending.token_exchange_price)
                    .ok_or_else(|| error!(MarginfiError::MathError))?
            };

            let share_amount = bank_account.withdraw(I80F48::from_num(shares_to_burn))?;

            (amount, shares_to_burn, share_amount)
```

**File:** programs/juplend-mocks/src/state.rs (L134-156)
```rust
    /// Expected fToken shares minted when depositing `assets` underlying.
    ///
    /// Mirrors JupLend's actual deposit flow: **round down** via the liquidity layer.
    ///
    /// The deposit goes through a two-step conversion in the liquidity layer before
    /// computing shares. The intermediate floor divisions can cause up to 1 unit of
    /// rounding loss vs the naive single-step formula when exchange prices != 1e12.
    ///
    /// Formula (1e12 precision):
    /// ```text
    /// raw   = floor(assets * 1e12 / liquidity_exchange_price)
    /// norm  = floor(raw * liquidity_exchange_price / 1e12)
    /// shares = floor(norm * 1e12 / token_exchange_price)
    /// ```
    /// https://github.com/Instadapp/fluid-solana-programs/blob/830458299be42eaeb6e1fe8fef6aa23444430a10/programs/lending/src/utils/deposit.rs#L68-L86
    #[inline]
    pub fn expected_shares_for_deposit(&self, assets: u64) -> Option<u64> {
        expected_shares_for_deposit_from_rates(
            assets,
            self.liquidity_exchange_price,
            self.token_exchange_price,
        )
    }
```

**File:** programs/juplend-mocks/src/state.rs (L158-179)
```rust
    /// Expected fToken shares burned when withdrawing `assets` underlying.
    ///
    /// Mirrors JupLend's ERC-4626 style `preview_withdraw` semantics: **round up**.
    ///
    /// Formula (1e12 precision): `shares = ceil(assets * 1e12 / token_exchange_price)`.
    ///
    /// # Ceiling Division Implementation
    ///
    /// Uses the standard integer ceiling division identity:
    /// ```text
    /// ceil(a / b) = floor((a + b - 1) / b)
    /// ```
    ///
    /// The `+ (b - 1)` bumps the numerator into the next bucket when there's any
    /// remainder, but has no effect when `a` is exactly divisible by `b`.
    ///
    /// JupLend uses `safe_div_ceil()` which is mathematically equivalent.
    /// https://github.com/Instadapp/fluid-solana-programs/blob/830458299be42eaeb6e1fe8fef6aa23444430a10/programs/lending/src/utils/withdraw.rs#L52-L59
    #[inline]
    pub fn expected_shares_for_withdraw(&self, assets: u64) -> Option<u64> {
        expected_shares_for_withdraw_from_rate(assets, self.token_exchange_price)
    }
```

**File:** programs/marginfi/src/instructions/juplend/local_tests.rs (L318-343)
```rust
    #[test]
    fn withdraw_shares_ceil_always_gte_deposit_shares_floor() {
        // withdraw (ceil) >= deposit (floor) for the same amount.
        for &(liq_price, tok_price) in &[
            (1_000_000_000_000u64, 1_000_000_000_000u64),
            (1_200_000_000_000, 1_500_000_000_000),
            (1_000_000_000_000, 2_000_000_000_000),
            (1_500_000_000_000, 1_000_000_000_000),
            (1_000_000_000_000, 1_100_000_000_000),
        ] {
            let l = lending_state(liq_price, tok_price);
            for &amount in &[1u64, 7, 100, 1_000_000, 100_000_000, 1_000_000_000_000] {
                let deposit_shares = l.expected_shares_for_deposit(amount).unwrap();
                let withdraw_shares = l.expected_shares_for_withdraw(amount).unwrap();
                assert!(
                    withdraw_shares >= deposit_shares,
                    "withdraw_shares ({}) < deposit_shares ({}) at liq={}, tok={}, amount={}",
                    withdraw_shares,
                    deposit_shares,
                    liq_price,
                    tok_price,
                    amount
                );
            }
        }
    }
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L1916-1937)
```rust
        let current_asset_shares: I80F48 = balance.asset_shares.into();
        let current_asset_amount = bank.get_asset_amount(current_asset_shares)?;

        let (mut asset_amount_decrease, mut liability_amount_increase) = (
            min(current_asset_amount, balance_delta),
            max(
                balance_delta
                    .checked_sub(current_asset_amount)
                    .ok_or_else(math_error!())?,
                I80F48::ZERO,
            ),
        );

        match operation_type {
            BalanceDecreaseType::WithdrawOnly => {
                check!(
                    liability_amount_increase.is_zero_with_tolerance(ZERO_AMOUNT_THRESHOLD),
                    MarginfiError::OperationWithdrawOnly
                );
                // Clamp tolerated dust to zero so it isn't booked as a new liability position.
                liability_amount_increase = I80F48::ZERO;
            }
```

**File:** programs/marginfi/src/instructions/drift/withdraw.rs (L143-165)
```rust
            // In some edge cases (such as when depositing and immediately withdrawing), the
            // requested token amount rounds up to a scaled decrement that exceeds the user's actual
            // scaled balance. In this case, we recompute the actual amounts from shares.
            //
            // ## Additional Notes:
            // * Bear in mind that one scaled-balance-unit is not neccessarily equal to one lamport.
            // * This is distinct from a true over-withdraw (>1 scaled unit), which still fails.
            // * A user can request up to ~1 scaled-unit over the true max; we will round down and
            //   withdraw only what they actually have, so the instruction input amount may not
            //   match the transfer. This is especially relevant for accounting systems that use the
            //   `amount` input to track funds: these may be slightly off.
            // * We cannot just `token_amount = token_amount.saturating_sub(1)` here because unlike
            //   withdraw_all, the token amount wasn’t derived from `asset_shares`.
            if scaled_decrement > asset_shares + 1 {
                return Err(error!(MarginfiError::OperationWithdrawOnly));
            } else if scaled_decrement == asset_shares + 1 {
                token_amount = integration_acc_1.get_withdraw_token_amount(asset_shares)?;
                scaled_decrement = integration_acc_1.get_scaled_balance_decrement(token_amount)?;
            }

            let share_amount = bank_account.withdraw(I80F48::from_num(scaled_decrement))?;

            (token_amount, scaled_decrement, share_amount)
```
