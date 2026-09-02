### No vulnerability found for this question.

**Reasoning:**

The described attack requires a window where `env::account_locked_balance()` (or `account_balance()`) already reflects the epoch's validator reward while `env::epoch_height()` still reports the *old* epoch, so that a no-op `ping()` (`self.last_epoch_height == epoch_height`) can be locked in before `stake()` mints new shares. That window does not exist on NEAR: the runtime credits validator rewards to `account_locked_balance` atomically with the epoch transition, so `epoch_height` and the reward-inflated balance always change together in the same block. There is no observable state where the reward is present but `epoch_height` hasn't advanced yet. [1](#0-0) 

Even granting the attacker perfect timing at the epoch boundary, every mutating entry point (`deposit`, `deposit_and_stake`, `stake`, `stake_all`, `withdraw*`, `unstake*`) calls `internal_ping()` as its very first statement: [2](#0-1) [3](#0-2) 

So the first call landing in a genuinely new epoch — whoever makes it — necessarily triggers `internal_ping()`'s real (non-no-op) branch, which realizes `total_reward` and distributes it across `total_stake_shares` as they exist **before** that caller's own `deposit`/`stake` logic runs (`internal_ping()` executes first, then `internal_deposit()`/`internal_stake()` execute afterward in the same function body). There is no call sequence available to an attacker that inserts new shares between "reward becomes visible on-chain" and "reward gets distributed by `internal_ping`" — the guard is structurally the first statement of the very call that would otherwise be used to sneak in new shares. [4](#0-3) [5](#0-4) 

The binding "shares receiving a slice of `total_reward` in `internal_ping` == shares that existed at the start of the epoch during which that reward accrued" holds because the only way to observe the epoch-turnover reward is via a call that itself performs the realization first, before any new stake/shares can be added in that same call chain. The premise of "call `ping()` once to lock in a no-op guard, then `stake()`" cannot be satisfied against a genuinely pending, not-yet-visible reward, since visibility of the reward and the epoch height change are inseparable on-chain.

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

**File:** staking-pool/src/internal.rs (L194-212)
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
```

**File:** staking-pool/src/lib.rs (L216-225)
```rust
    #[payable]
    pub fn deposit(&mut self) {
        let need_to_restake = self.internal_ping();

        self.internal_deposit();

        if need_to_restake {
            self.internal_restake();
        }
    }
```

**File:** staking-pool/src/lib.rs (L279-287)
```rust
    pub fn stake(&mut self, amount: U128) {
        // Stake action always restakes
        self.internal_ping();

        let amount: Balance = amount.into();
        self.internal_stake(amount);

        self.internal_restake();
    }
```
