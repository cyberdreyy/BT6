### Title
Unconditional `last_total_balance -= amount` before an unverified `Promise::transfer` in `internal_withdraw` allows failed-transfer NEAR to be misclassified as reward - (File: staking-pool/src/internal.rs)

### Summary
`internal_withdraw` decrements `self.last_total_balance` by `amount` immediately after scheduling `Promise::new(account_id).transfer(amount)`, without any callback (`.then(...)`) to confirm the transfer succeeded. If the transfer receipt fails (e.g., because the destination account no longer exists), the attached NEAR is refunded to the staking pool contract's own balance, but `last_total_balance` has already been permanently reduced by `amount`, so the next `internal_ping` treats the returned NEAR as a staking reward.

### Finding Description
The binding that should hold is: `last_total_balance == account_balance() + account_locked_balance()` at all times outside of pending, unresolved promises whose deposit is accounted for. In `internal_withdraw` [1](#0-0) , the code does:
1. asserts `account.unstaked >= amount` and `unstaked_available_epoch_height <= env::epoch_height()`,
2. decrements `account.unstaked -= amount` and saves the account,
3. schedules `Promise::new(account_id).transfer(amount)`,
4. unconditionally does `self.last_total_balance -= amount`.

There is no `.then(ext_self::on_withdraw(...))` callback and no `is_promise_success()` check anywhere in this path — `internal_withdraw` is fire-and-forget with respect to the promise result. `account_id` here is `env::predecessor_account_id()`, i.e., the caller's own account, so an attacker withdrawing to themselves fully controls whether that account exists when the transfer receipt executes.

Exploit flow: the attacker calls `withdraw(amount)` on their own delegated account, then submits a separate `DeleteAccount` transaction against that same account (using their own full-access key, which is legitimate/unprivileged for their own account) timed so it lands before the `transfer` receipt is applied. When the transfer's destination account no longer exists, the receipt fails and NEAR protocol refunds the attached deposit back to the predecessor of that action — the staking pool contract itself — landing back in `env::account_balance()`. Meanwhile `self.last_total_balance` was already reduced by `amount` in step 4, and `account.unstaked` was already debited in step 2, so the attacker's internal ledger entry for that NEAR is fully destroyed.

At the next `internal_ping` [2](#0-1) , `total_balance = account_locked_balance() + account_balance() - attached_deposit()` includes the refunded `amount`, while `self.last_total_balance` does not, so `total_reward = total_balance - self.last_total_balance` is inflated by exactly `amount`. This reward is then distributed as `owners_fee` to the owner and `remaining_reward` added to `self.total_staked_balance` for all current delegators [3](#0-2) , none of whom contributed real, externally-earned NEAR for it.

No guard prevents this: `internal_ping`'s only assertion is `total_balance >= self.last_total_balance` [4](#0-3) , which is satisfied (the balance is higher, just for the wrong reason), and there is no `assert_self()`/`is_promise_success()` callback gating the `last_total_balance` write in `internal_withdraw`.

### Impact Explanation
The `amount` that should have remained the withdrawing account's claim (or been returned to their `unstaked` balance on failure) is instead redistributed as phantom reward to the owner (`owners_fee`) and to all delegators via `total_staked_balance` growth, permanently diverging `last_total_balance`'s intended meaning ("NEAR actually earned this epoch") from reality. This matches the High-severity category: "rewards or owner fees attributed to the wrong party" / "an accounting value diverging from reality where another party settles on it," since delegators and the owner then hold stake-share value backed partly by NEAR that was never an actual validator reward, and the withdrawing attacker's claim to that `amount` is destroyed with no compensating credit back to their `account.unstaked`.

### Likelihood Explanation
Preconditions are attacker-controlled and unprivileged: the attacker needs an unstaked balance ≥ `amount` past the unlock delay (achievable via normal `unstake`/`ping` flow) and the ability to delete their own account with a full-access key they hold — both ordinary, permitted actions. The main uncertainty is the race timing: the attacker must ensure the `DeleteAccount` action executes before the `transfer` receipt from `withdraw` is applied on the same account, which requires precise transaction/receipt sequencing rather than a guaranteed one-shot call. This makes the attack technically feasible but timing-sensitive, and it is self-destructive for the attacker's own withdrawn balance (they only recoup a fraction back if they still hold `stake_shares` in the same pool), so the primary effect is misattributing funds away from the attacker toward other parties rather than a direct attacker profit — repeatable per attempt but requiring the attacker to sacrifice `amount` of their own claim each time.

### Recommendation
Use a callback pattern for `withdraw`/`internal_withdraw`: schedule `Promise::new(account_id).transfer(amount).then(ext_self::on_withdraw(amount, account_id, ...))`, and only decrement `self.last_total_balance` (already done eagerly) — or better, only finalize the debit — inside an `assert_self()`-gated callback that checks `is_promise_success()`. On failure, credit `amount` back to `account.unstaked` (and do not decrement `last_total_balance`, or restore it) so a failed transfer is fully reverted from the contract's accounting perspective.

### Proof of Concept
Using `testing_env!` state-only assertions (since promise callback execution isn't observable in unit tests):
1. Set up a `StakingContract` with an account having `unstaked = amount`, `unstaked_available_epoch_height <= current_epoch`, and `last_total_balance = account_balance() + account_locked_balance()` (established invariant).
2. Call `internal_withdraw(amount)`; assert `account.unstaked == 0` and `self.last_total_balance == old_last_total_balance - amount`.
3. Emulate a failed transfer by leaving the `amount` in the mocked `account_balance()` (i.e., do not actually reduce the simulated NEAR balance, representing the protocol-level refund on receipt failure).
4. Advance `env::epoch_height()` and call `internal_ping()`.
5. Assert the broken binding: `total_balance (env::account_locked_balance() + env::account_balance()) != self.last_total_balance` before ping, and after ping `self.total_staked_balance` has increased by `amount` (minus rounding) even though no real validator reward occurred and `account.unstaked` was never restored — demonstrating the phantom-reward misattribution.

### Citations

**File:** staking-pool/src/internal.rs (L42-68)
```rust
    pub(crate) fn internal_withdraw(&mut self, amount: Balance) {
        assert!(amount > 0, "Withdrawal amount should be positive");

        let account_id = env::predecessor_account_id();
        let mut account = self.internal_get_account(&account_id);
        assert!(
            account.unstaked >= amount,
            "Not enough unstaked balance to withdraw"
        );
        assert!(
            account.unstaked_available_epoch_height <= env::epoch_height(),
            "The unstaked balance is not yet available due to unstaking delay"
        );
        account.unstaked -= amount;
        self.internal_save_account(&account_id, &account);

        env::log(
            format!(
                "@{} withdrawing {}. New unstaked balance is {}",
                account_id, amount, account.unstaked
            )
            .as_bytes(),
        );

        Promise::new(account_id).transfer(amount);
        self.last_total_balance -= amount;
    }
```

**File:** staking-pool/src/internal.rs (L205-212)
```rust
        let total_balance =
            env::account_locked_balance() + env::account_balance() - env::attached_deposit();

        assert!(
            total_balance >= self.last_total_balance,
            "The new total balance should not be less than the old total balance"
        );
        let total_reward = total_balance - self.last_total_balance;
```

**File:** staking-pool/src/internal.rs (L213-234)
```rust
        if total_reward > 0 {
            // The validation fee that the contract owner takes.
            let owners_fee = self.reward_fee_fraction.multiply(total_reward);

            // Distributing the remaining reward to the delegators first.
            let remaining_reward = total_reward - owners_fee;
            self.total_staked_balance += remaining_reward;

            // Now buying "stake" shares for the contract owner at the new share price.
            let num_shares = self.num_shares_from_staked_amount_rounded_down(owners_fee);
            if num_shares > 0 {
                // Updating owner's inner account
                let owner_id = self.owner_id.clone();
                let mut account = self.internal_get_account(&owner_id);
                account.stake_shares += num_shares;
                self.internal_save_account(&owner_id, &account);
                // Increasing the total amount of "stake" shares.
                self.total_stake_shares += num_shares;
            }
            // Increasing the total staked balance by the owners fee, no matter whether the owner
            // received any shares or not.
            self.total_staked_balance += owners_fee;
```
