## Finding

Both `withdraw` and `withdraw_all` in `staking-pool/src/lib.rs` route through `internal_withdraw`, which debits the delegator's ledger and the pool's global accounting **before** confirming that the outbound NEAR transfer actually completed, and never attaches a callback to verify or roll back on failure. This is the same "debit-before-delivery" pattern (an unprivileged party's recorded claim diverging from what was actually delivered) that the report flags for `ragequit`/`safeRagequit`.

### Title
Unverified fire-and-forget `Promise::new(account_id).transfer(amount)` in `internal_withdraw` can permanently desync delegator ledger from actual NEAR delivered - (File: `staking-pool/src/internal.rs`)

### Summary
`internal_withdraw` decreases `account.unstaked` and `self.last_total_balance` and only afterward issues a bare `Promise::new(account_id).transfer(amount)` with no `.then()` callback to check `is_promise_success()`. [1](#0-0)  If that outbound transfer receipt fails to reach the destination account (e.g. the delegator deletes/replaces their account in a concurrent, cross-shard-delayed transaction, or the receipt fails for any other async reason), NEAR runtime refunds the transferred amount back to the sending contract rather than delivering it - but the pool has already permanently zeroed the delegator's `unstaked` balance and reduced `last_total_balance`, with no callback to detect the failure and restore the account.

### Finding Description
Compare this to the pattern the same repository uses elsewhere for withdrawal safety: in `lockup/src/owner_callbacks.rs`, `on_staking_pool_withdraw` explicitly checks `is_promise_success()` before mutating `staking_information.deposit_amount`, only decrementing it when the transfer succeeded. [2](#0-1)  The staking pool's own `withdraw`/`withdraw_all` entry points, however, call `internal_withdraw` synchronously with no such verification: [3](#0-2) 

```rust
pub(crate) fn internal_withdraw(&mut self, amount: Balance) {
    ...
    account.unstaked -= amount;
    self.internal_save_account(&account_id, &account);
    ...
    Promise::new(account_id).transfer(amount);   // fire-and-forget, no callback
    self.last_total_balance -= amount;
}
``` [4](#0-3) 

Because the `Transfer` action executes in a separate, later receipt, the state mutation in the calling receipt (`account.unstaked -= amount`, `last_total_balance -= amount`) is already committed and irreversible by the time the transfer executes. If the transfer receipt fails and the NEAR is refunded to the pool contract instead of the delegator, the pool's tracked `last_total_balance` no longer matches its actual account balance. On the next `internal_ping()`, that stranded balance is computed as `total_reward = total_balance - self.last_total_balance` and is redistributed as staking rewards to the owner and currently-staked delegators - not returned to the delegator who was supposed to receive it. [5](#0-4)  This is exactly the class described in the report: the withdrawing party's recorded claim (their ledger entry, now zero) diverges from the NEAR actually delivered, and the difference silently accrues to other parties with no pull-based recovery path for the original delegator.

### Impact Explanation
This crosses a solvency/settlement boundary: `last_total_balance` (the pool's internal accounting of assets owed to depositors and reflecting actual balance) diverges from the real contract balance in a way that is settled upon by other delegators/the owner via reward distribution in `internal_ping`. Funds that should belong to the withdrawing delegator become permanently unattributable to them and get redistributed as rewards to unrelated parties - matching the "accounting value diverging from reality where another party settles on it" / "funds frozen for at least one epoch" High-impact criteria.

### Likelihood Explanation
Triggering this requires an unusual, low-probability async failure of a native `Transfer` receipt to the predecessor's own account (e.g., account deletion racing with a pending outbound transfer receipt across shards). It is not attacker-repeatable at will and depends on transient conditions outside direct control of a caller in a single transaction, which lowers likelihood relative to a straightforward ERC20-blacklist scenario, but the missing verification/rollback mechanism (unlike the lockup contract's equivalent pattern) is a genuine, reachable code-level gap.

### Recommendation
Mirror the pattern already used in `lockup/src/owner_callbacks.rs::on_staking_pool_withdraw`: attach a `.then()` callback to the `Promise::new(account_id).transfer(amount)` in `internal_withdraw`, and only finalize the ledger/`last_total_balance` decrement once `is_promise_success()` confirms delivery; otherwise restore `account.unstaked` so the delegator retains a pull-based claim on their funds.

### Proof of Concept
1. Delegator deposits and unstakes, waits out the unstaking delay, then calls `withdraw_all`, which invokes `internal_withdraw` synchronously decrementing `account.unstaked` and `last_total_balance` and issuing an un-awaited `Promise::new(account_id).transfer(amount)`. [6](#0-5) 
2. Before that transfer receipt executes (cross-shard delay), the delegator's account becomes unable to receive the transfer (e.g. deleted/reused), causing the `Transfer` action's receipt to fail and the NEAR to be refunded to the staking-pool contract rather than the delegator.
3. The delegator's `unstaked` balance is already zero and `last_total_balance` was already decremented - there is no callback to detect the failure or restore state.
4. On the next `ping()`, the refunded NEAR is counted as `total_reward` in `internal_ping` and distributed to the owner and currently staked delegators, permanently reassigning the withdrawing delegator's funds to other parties. [7](#0-6)

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

**File:** staking-pool/src/internal.rs (L194-234)
```rust
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

**File:** lockup/src/owner_callbacks.rs (L105-120)
```rust
    pub fn on_staking_pool_withdraw(&mut self, amount: WrappedBalance) -> bool {
        assert_self();

        let withdraw_succeeded = is_promise_success();
        self.set_staking_pool_status(TransactionStatus::Idle);

        if withdraw_succeeded {
            {
                let staking_information = self.staking_information.as_mut().unwrap();
                // Due to staking rewards the deposit amount can become negative.
                staking_information.deposit_amount.0 = staking_information
                    .deposit_amount
                    .0
                    .saturating_sub(amount.0);
            }
            env::log(
```

**File:** staking-pool/src/lib.rs (L238-263)
```rust
    /// Withdraws the entire unstaked balance from the predecessor account.
    /// It's only allowed if the `unstake` action was not performed in the four most recent epochs.
    pub fn withdraw_all(&mut self) {
        let need_to_restake = self.internal_ping();

        let account_id = env::predecessor_account_id();
        let account = self.internal_get_account(&account_id);
        self.internal_withdraw(account.unstaked);

        if need_to_restake {
            self.internal_restake();
        }
    }

    /// Withdraws the non staked balance for given account.
    /// It's only allowed if the `unstake` action was not performed in the four most recent epochs.
    pub fn withdraw(&mut self, amount: U128) {
        let need_to_restake = self.internal_ping();

        let amount: Balance = amount.into();
        self.internal_withdraw(amount);

        if need_to_restake {
            self.internal_restake();
        }
    }
```
