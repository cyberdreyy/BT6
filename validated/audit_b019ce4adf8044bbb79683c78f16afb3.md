## No vulnerability found for this question.

**Analysis:**

The claimed exploit requires deleting an account row via `internal_save_account` while the account still has a "pending" `unstaked_available_epoch_height` in the future, then recreating the row via `internal_deposit` (which produces a `Default` account with `unstaked_available_epoch_height = 0`), and withdrawing immediately.

Tracing `internal_save_account`: [1](#0-0) 

The row is deleted only when **both** `account.unstaked == 0` and `account.stake_shares == 0`. The only code paths that mutate `account.unstaked`:

- `internal_deposit` — always increases `unstaked` (`+=`), never zero after this call unless amount is 0. [2](#0-1) 
- `internal_withdraw` — decreases `unstaked` (`-=`), but only after asserting `account.unstaked_available_epoch_height <= env::epoch_height()`. [3](#0-2) 
- `internal_stake` — decreases `unstaked` by `charge_amount`, but simultaneously increases `stake_shares` by `num_shares > 0` (asserted), so `stake_shares` becomes nonzero, preventing deletion. [4](#0-3) 
- `inner_unstake` — increases `unstaked` and (re)sets `unstaked_available_epoch_height` to a new future epoch, but this always makes `unstaked > 0` afterward, so the row is never deleted at this step. [5](#0-4) 

So the **only** way `account.unstaked` can reach `0` while a pending future `unstaked_available_epoch_height` existed is through `internal_withdraw`, and that function already enforces `account.unstaked_available_epoch_height <= env::epoch_height()` before zeroing the balance. Therefore, by the time the row is actually eligible for deletion (both fields zero), the pending unlock condition has already been satisfied — the reset to the `Default` value of `unstaked_available_epoch_height = 0` upon recreation causes no bypass, because there was no still-locked balance left to protect.

A newly deposited amount via `internal_deposit` was never staked/unstaked, so it legitimately has no delay — this is intended behavior (deposited-but-never-staked funds are immediately withdrawable), not a bug.

No path exists for an attacker to zero out `account.unstaked` (triggering row deletion) while `unstaked_available_epoch_height` still points to a future epoch, because the assertion in `internal_withdraw` gates the only balance-decreasing operation with a pending epoch value.

### Citations

**File:** staking-pool/src/internal.rs (L24-40)
```rust
    pub(crate) fn internal_deposit(&mut self) -> u128 {
        let account_id = env::predecessor_account_id();
        let mut account = self.internal_get_account(&account_id);
        let amount = env::attached_deposit();
        account.unstaked += amount;
        self.internal_save_account(&account_id, &account);
        self.last_total_balance += amount;

        env::log(
            format!(
                "@{} deposited {}. New unstaked balance is {}",
                account_id, amount, account.unstaked
            )
            .as_bytes(),
        );
        amount
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

**File:** staking-pool/src/internal.rs (L70-98)
```rust
    pub(crate) fn internal_stake(&mut self, amount: Balance) {
        assert!(amount > 0, "Staking amount should be positive");

        let account_id = env::predecessor_account_id();
        let mut account = self.internal_get_account(&account_id);

        // Calculate the number of "stake" shares that the account will receive for staking the
        // given amount.
        let num_shares = self.num_shares_from_staked_amount_rounded_down(amount);
        assert!(
            num_shares > 0,
            "The calculated number of \"stake\" shares received for staking should be positive"
        );
        // The amount of tokens the account will be charged from the unstaked balance.
        // Rounded down to avoid overcharging the account to guarantee that the account can always
        // unstake at least the same amount as staked.
        let charge_amount = self.staked_amount_from_num_shares_rounded_down(num_shares);
        assert!(
            charge_amount > 0,
            "Invariant violation. Calculated staked amount must be positive, because \"stake\" share price should be at least 1"
        );

        assert!(
            account.unstaked >= charge_amount,
            "Not enough unstaked balance to stake"
        );
        account.unstaked -= charge_amount;
        account.stake_shares += num_shares;
        self.internal_save_account(&account_id, &account);
```

**File:** staking-pool/src/internal.rs (L124-157)
```rust
    pub(crate) fn inner_unstake(&mut self, amount: u128) {
        assert!(amount > 0, "Unstaking amount should be positive");

        let account_id = env::predecessor_account_id();
        let mut account = self.internal_get_account(&account_id);

        assert!(
            self.total_staked_balance > 0,
            "The contract doesn't have staked balance"
        );
        // Calculate the number of shares required to unstake the given amount.
        // NOTE: The number of shares the account will pay is rounded up.
        let num_shares = self.num_shares_from_staked_amount_rounded_up(amount);
        assert!(
            num_shares > 0,
            "Invariant violation. The calculated number of \"stake\" shares for unstaking should be positive"
        );
        assert!(
            account.stake_shares >= num_shares,
            "Not enough staked balance to unstake"
        );

        // Calculating the amount of tokens the account will receive by unstaking the corresponding
        // number of "stake" shares, rounding up.
        let receive_amount = self.staked_amount_from_num_shares_rounded_up(num_shares);
        assert!(
            receive_amount > 0,
            "Invariant violation. Calculated staked amount must be positive, because \"stake\" share price should be at least 1"
        );

        account.stake_shares -= num_shares;
        account.unstaked += receive_amount;
        account.unstaked_available_epoch_height = env::epoch_height() + NUM_EPOCHS_TO_UNLOCK;
        self.internal_save_account(&account_id, &account);
```

**File:** staking-pool/src/internal.rs (L328-336)
```rust
    /// Inner method to save the given account for a given account ID.
    /// If the account balances are 0, the account is deleted instead to release storage.
    pub(crate) fn internal_save_account(&mut self, account_id: &AccountId, account: &Account) {
        if account.unstaked > 0 || account.stake_shares > 0 {
            self.accounts.insert(account_id, &account);
        } else {
            self.accounts.remove(account_id);
        }
    }
```
