This confirms the analog. `internal_withdraw` in `staking-pool/src/internal.rs` decrements `account.unstaked` and `self.last_total_balance`, then fires `Promise::new(account_id).transfer(amount)` with no `.then()` callback checking success, unlike every other cross-contract action in this same contract (`internal_restake` uses `.then(ext_self::on_stake_action(...))`, and lockup's `foundation.rs`/`owner_callbacks.rs` all verify `is_promise_success()` before mutating state). This is the same bug class as the `Bet::cancel` report: state is advanced as if the transfer succeeded, but if the transfer actually fails, the user's recorded claim is erased permanently with no way to recover it, while the actual NEAR stays in the contract and is silently redistributed to other stakers as "reward" on the next `ping` (as the README itself documents happens for the owner's failed withdraw, at README lines 92-94: `staking-pool/README.md:92-94`). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Title
`withdraw`/`withdraw_all` debit a delegator's unstaked balance before confirming the NEAR transfer succeeded, permanently erasing the claim if the transfer fails - (File: staking-pool/src/internal.rs)

### Summary
`StakingContract::internal_withdraw`, invoked by both the public `withdraw` and `withdraw_all` entry points, decrements the delegator's `account.unstaked` balance and the contract's `last_total_balance` and then fires an unchecked `Promise::new(account_id).transfer(amount)`. Unlike every other asynchronous cross-contract call in this codebase (`internal_restake`'s `.then(ext_self::on_stake_action(...))`, and the analogous staking-pool interactions in `lockup/src/owner_callbacks.rs` / `lockup/src/foundation_callbacks.rs`), there is no `.then()` callback that checks `is_promise_success()` and rolls the state back if the transfer fails.

### Finding Description
`internal_withdraw` (`staking-pool/src/internal.rs:42-68`) does:
```rust
account.unstaked -= amount;
self.internal_save_account(&account_id, &account);
...
Promise::new(account_id).transfer(amount);
self.last_total_balance -= amount;
```
Both `account.unstaked` (the delegator's recorded claim) and `self.last_total_balance` (the value the contract uses as its "actual NEAR held" baseline for reward accounting in `internal_ping`, `staking-pool/src/internal.rs:192-249`) are decremented unconditionally, before the outcome of the `transfer` Promise is known. If the transfer fails - for example because the receiving account no longer exists, is over storage limits, or any other reason a plain `transfer` action can fail on NEAR - the receipt fails and the yoctoNEAR remains inside the staking-pool contract's actual balance, but:
1. The delegator's on-chain record (`account.unstaked`) no longer reflects those funds - they have no `unstaked` balance left to claim them, and there is no other function that can restore or re-credit it.
2. `self.last_total_balance` was reduced by `amount`, so at the very next `internal_ping()`/`ping()` call, `total_balance - self.last_total_balance` counts these stranded yoctoNEAR as "reward" and distributes them pro-rata to `total_staked_balance` (benefiting the owner via `owners_fee` and all other stakers via `remaining_reward`), i.e. an accounting value (`last_total_balance`/reward baseline) diverges from reality and other delegators settle on it as legitimate reward.

This mirrors the `Bet::cancel` bug exactly: an external transfer's success is not verified before the contract's bookkeeping is advanced as if it succeeded, so the affected party's claim is permanently and silently lost while the value stays trapped in the contract and gets reassigned to other parties.

### Impact Explanation
This is a genuine solvency/accounting divergence for the individual delegator's claim vs. the contract's actual held NEAR, and it results in funds effectively being moved to other stakers (the owner and remaining delegators) without authorization from the affected delegator — matching the "funds permanently frozen"/"accounting value diverging from reality where another party settles on it" impact categories. It requires no privileged role; any delegator calling their own `withdraw`/`withdraw_all` is exposed if their receiving account becomes unable to receive the transfer between call submission and the transfer receipt executing (e.g., the account is deleted concurrently, which a user or an attacker controlling that account state could trigger).

### Likelihood Explanation
Triggering requires the withdrawing account to become unable to receive a plain `transfer` at the exact moment the async transfer executes (e.g., self-deletion of the account, which the README already documents happening "accidentally or intentionally" for the owner case). This is a narrow, self-inflicted window for a normal delegator, but it is concretely reachable without any privileged role, redeploy, or external actor, and the codebase's own documentation acknowledges the exact failure mode occurring for the owner already.

### Recommendation
Add a `.then()` callback (mirroring `on_stake_action`/`on_staking_pool_withdraw`) to `internal_withdraw`'s `Promise::new(account_id).transfer(amount)` that checks `is_promise_success()`; if the transfer failed, restore `account.unstaked += amount` and `self.last_total_balance += amount` instead of leaving the debit permanent.

### Proof of Concept
1. Delegator deposits and later unstakes, waits 4 epochs so `account.unstaked_available_epoch_height <= env::epoch_height()`.
2. Delegator calls `withdraw`/`withdraw_all`; `internal_withdraw` decrements `account.unstaked` and `self.last_total_balance` by `amount`, then schedules `Promise::new(account_id).transfer(amount)`.
3. Before that transfer receipt executes, the delegator's account is deleted (self-inflicted or via a race with another action on that account) so the `transfer` action fails on execution.
4. The `amount` yoctoNEAR remains in the staking-pool contract's actual balance, but `account.unstaked` is already zero for that delegator with no code path to restore it.
5. On the next `ping`, `internal_ping` computes `total_reward = total_balance - self.last_total_balance` including the stranded `amount`, and distributes it as reward to `total_staked_balance` (owner fee + remaining delegators) — the original delegator's claim is permanently gone.

### Citations

**File:** staking-pool/src/internal.rs (L8-22)
```rust
    /// Restakes the current `total_staked_balance` again.
    pub(crate) fn internal_restake(&mut self) {
        if self.paused {
            return;
        }
        // Stakes with the staking public key. If the public key is invalid the entire function
        // call will be rolled back.
        Promise::new(env::current_account_id())
            .stake(self.total_staked_balance, self.stake_public_key.clone())
            .then(ext_self::on_stake_action(
                &env::current_account_id(),
                NO_DEPOSIT,
                ON_STAKE_ACTION_GAS,
            ));
    }
```

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

**File:** staking-pool/src/internal.rs (L192-212)
```rust
    /// Distributes rewards after the new epoch. It's automatically called before every action.
    /// Returns true if the current epoch height is different from the last epoch height.
    pub(crate) fn internal_ping(&mut self) -> bool {
        let epoch_height = env::epoch_height();
        if self.last_epoch_height == epoch_height {
            return false;
        }
        self.last_epoch_height = epoch_height;

        // New total amount (both locked and unlocked balances).
        // NOTE: We need to subtract `attached_deposit` in case `ping` called from `deposit` call
        // since the attached deposit gets included in the `account_balance`, and we have not
        // accounted it yet.
        let total_balance =
            env::account_locked_balance() + env::account_balance() - env::attached_deposit();

        assert!(
            total_balance >= self.last_total_balance,
            "The new total balance should not be less than the old total balance"
        );
        let total_reward = total_balance - self.last_total_balance;
```

**File:** staking-pool/README.md (L92-94)
```markdown
Note, in a rare scenario, where the owner withdraws tokens and while the call is being processed deletes their account, the
withdraw transfer will fail and the tokens will be returned to the staking pool. These tokens will also be distributed as
a reward in the next epoch.
```
