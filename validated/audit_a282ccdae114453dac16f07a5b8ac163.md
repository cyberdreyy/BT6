### Title
Delegator's withdrawn balance is redistributed as staking rewards if the outgoing NEAR transfer fails - (File: `staking-pool/src/internal.rs`)

### Summary
`internal_withdraw` in the staking pool decrements the delegator's `unstaked` balance and the pool's `last_total_balance` before the outgoing `Promise::new(account_id).transfer(amount)` is guaranteed to succeed, and no callback checks whether that transfer actually completed. If the transfer fails (e.g. the delegator's account was deleted or refuses the transfer), the NEAR is refunded to the pool's actual account balance, but the pool's bookkeeping (`last_total_balance`) has already been reduced as if the funds left. The next `ping()` computes rewards as `actual_balance - last_total_balance`, so the refunded, un-delivered withdrawal is counted as a reward and distributed pro-rata to stake shares — including the owner — instead of to the delegator who never received it.

### Finding Description
`internal_withdraw` unconditionally mutates state and fires a transfer with no success check: [1](#0-0) 

Unlike the lockup contract's analogous withdraw flow, which attaches a callback (`on_staking_pool_withdraw`) that checks `is_promise_success()` and reverts local accounting on failure: [2](#0-1) 

the staking-pool's `internal_withdraw` does not attach any `.then(...)` callback at all — `account.unstaked -= amount` and `self.last_total_balance -= amount` are committed synchronously and are never rolled back regardless of whether `Promise::new(account_id).transfer(amount)` actually succeeds. This is called from both `withdraw` and `withdraw_all`: [3](#0-2) 

The equality that should hold is: `last_total_balance` (recorded liabilities) == actual NEAR held by the contract that is attributable to delegators. When the transfer fails, the actual balance is unchanged (refunded) but `last_total_balance` is permanently reduced by `amount`, breaking this equality by `amount` in the pool's favor.

The README itself documents that this exact class of discrepancy — a failed transfer whose NEAR returns to the contract — is treated as a reward at the next epoch boundary: [4](#0-3) 

This is precisely the reported bug pattern: an unchecked/unverified value-transfer causes (1) an accounting update (`collateralReturnedCount` analog: `account.unstaked`/`last_total_balance`) that doesn't match what was actually delivered, and (2) a `balance`-style reward computation that redistributes the undelivered funds as yield to other participants.

### Impact Explanation
This is High severity: the delegator's collateral/withdrawal (rightfully theirs) is mis-attributed as staking reward and distributed to the owner and other active delegators via `total_staked_balance`/shares, while the original delegator's `unstaked` balance has already been debited and cannot be reclaimed. This is an accounting value (`last_total_balance`/`account.unstaked`) diverging from the NEAR actually held/owed, with another party (all stakers, especially the owner via `reward_fee_fraction`) settling on the incorrect value at the next `ping()`.

### Likelihood Explanation
No special privilege is required — any delegator whose withdrawal transfer fails (e.g., due to a deleted/invalid receiving account, or the receiving account rejecting the transfer for any protocol-level reason) triggers this path automatically as part of normal `withdraw`/`withdraw_all` usage. There is no retry or verification mechanism, so the loss is deterministic once the transfer fails.

### Recommendation
Attach a callback to the transfer Promise in `internal_withdraw` (mirroring the lockup contract's pattern), check `is_promise_success()`, and only finalize the debit of `account.unstaked` and `last_total_balance` on confirmed success; on failure, restore the account's `unstaked` balance (and `last_total_balance`) so the delegator can retry the withdrawal, and add a cooldown to prevent repeated failed-withdraw griefing.

### Proof of Concept
1. Delegator deposits and unstakes, waits 4 epochs, then calls `withdraw`/`withdraw_all`. [3](#0-2) 
2. `internal_withdraw` decrements `account.unstaked` and `last_total_balance` and fires `Promise::new(account_id).transfer(amount)` with no callback. [5](#0-4) 
3. The receiving account no longer exists (or otherwise cannot accept the transfer), so the transfer fails and the NEAR is refunded to the pool's contract balance instead of the delegator.
4. On the next `ping()`, the contract compares its actual (now higher than expected) balance to the reduced `last_total_balance`, computes the difference as a reward, and distributes it via stake shares to the owner and remaining delegators — permanently absorbing the failed delegator's withdrawal as yield for others, per the documented behavior. [6](#0-5)

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

**File:** lockup/src/owner_callbacks.rs (L105-144)
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
                format!(
                    "The withdrawal of {} from @{} succeeded",
                    amount.0,
                    self.staking_information
                        .as_ref()
                        .unwrap()
                        .staking_pool_account_id
                )
                .as_bytes(),
            );
        } else {
            env::log(
                format!(
                    "The withdrawal of {} from @{} failed",
                    amount.0,
                    self.staking_information
                        .as_ref()
                        .unwrap()
                        .staking_pool_account_id
                )
                .as_bytes(),
            );
        }
        withdraw_succeeded
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

**File:** staking-pool/README.md (L90-103)
```markdown
Note, the if someone accidentally (or intentionally) transfers tokens to the contract (without function call), then
tokens from the transfer will be distributed to the active stake participants of the contract in the next epoch.
Note, in a rare scenario, where the owner withdraws tokens and while the call is being processed deletes their account, the
withdraw transfer will fail and the tokens will be returned to the staking pool. These tokens will also be distributed as
a reward in the next epoch.

The method first checks that the current epoch is different from the last epoch, and if it's not changed exits the method.

The reward are computed the following way. The contract keeps track of the last known total account balance.
This balance consist of the initial contract balance, and all delegator account balances (including the owner) and all accumulated rewards.
(Validation rewards are added automatically at the beginning of the epoch, while contract execution gas rebates are added after each transaction)

When the method is called the contract uses the current total account balance (without attached deposit) and the subtracts the last total account balance.
The difference is the total reward that has to be distributed.
```
