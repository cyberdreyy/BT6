### Title
Front-running epoch reward accrual via last-block `deposit_and_stake` dilutes existing delegators' rewards - ([File: staking-pool/src/internal.rs])

### Summary
An unprivileged delegator can call `deposit_and_stake` in the last block of an epoch to mint "stake" shares at the pre-reward price, then have `internal_ping` (triggered by anyone's next call) split the just-earned validator reward across the enlarged `total_stake_shares` pool — including the shares the attacker minted milliseconds before the reward was earned. This dilutes the reward legitimately earned by pre-existing delegators' bonded stake.

### Finding Description
The broken binding: `total_staked_balance_new - total_staked_balance_old` (the reward increment credited at epoch flip) should be split only among the `total_stake_shares` that were bonded and earning that specific validator reward — but instead it is split among `total_stake_shares` as read at the moment `internal_ping` executes, with no distinction between shares that existed through the reward-earning period and shares minted in the same or immediately preceding block.

Code path:
- `deposit_and_stake` calls `internal_ping()` (no-op if epoch unchanged) then `internal_deposit()` and `internal_stake(amount)`: [1](#0-0) 
- `internal_stake` mints `num_shares` at the current `total_staked_balance`/`total_stake_shares` price and immediately adds them to `total_stake_shares`: [2](#0-1) 
- `internal_restake` submits `Promise::new(...).stake(total_staked_balance, ...)`, which moves the newly deposited amount into `env::account_locked_balance()` in the same transaction, so the deposit itself is balance-neutral for the `total_balance` calculation used by `internal_ping`: [3](#0-2) 
- On the next call (e.g. `ping`) after the epoch flips, `internal_ping` computes `total_reward = total_balance - last_total_balance` and adds `remaining_reward` to `total_staked_balance`, which raises the share price for **all** `total_stake_shares` present at that moment: [4](#0-3) 

Root cause: NEAR's protocol computes validator rewards for an epoch based on stake bonded roughly 1-2 epochs earlier (stake proposals take effect with delay), but the contract's internal accounting treats a delegator's shares as fully reward-eligible the instant `internal_stake` executes. None of the existing guards (`internal_ping`'s balance-non-decrease assert, the rounding-safe `num_shares_from_staked_amount_rounded_down/up` pair, `assert_owner`, etc.) check *when* shares were minted relative to reward accrual — they only guard against balance underflow and rounding loss, not temporal fairness of distribution.

### Impact Explanation
Existing delegators' proportional share of that epoch's validator reward is silently reduced because the reward pool is now divided by an inflated `total_stake_shares` denominator that includes stake which did not actually earn that reward. This is "rewards attributed to the wrong party" — value transferred from long-standing delegators to a delegator who deposited immediately before the reward was credited. It is repeatable every epoch by any account with capital, against any staking pool instance, and requires no special privilege. This matches the defined High-severity category.

### Likelihood Explanation
Preconditions are simple and always present: an existing pool with `total_staked_balance > 0`, a predictable epoch boundary (~12h), and an attacker with enough NEAR to deposit a large amount relative to the pool immediately before the boundary. Epoch height and approximate reward timing are public/predictable via `env::epoch_height()` and historical reward patterns, so the attacker's timing cost is low and the attack is trivially repeatable across epochs and across any staking-pool contract deployed from this code.

### Recommendation
Track reward eligibility with a time/epoch-weighted mechanism instead of instantaneous share minting — e.g., only allow newly deposited/staked amounts to participate in reward distribution starting from the next full epoch after they are bonded (mirroring the protocol's actual stake-activation delay), or snapshot `total_stake_shares` for reward-splitting purposes prior to the last deposit in the epoch.

### Proof of Concept
Using the existing `Emulator` harness in `staking-pool/src/lib.rs` tests:
1. Set up pool with delegator A staking `X` NEAR over several epochs (fee = 0 for simplicity).
2. At `epoch_height = N` (same epoch as the upcoming reward, before calling `ping`), have delegator B call `deposit_and_stake` with a large amount `Y >> X`.
3. Advance `emulator.skip_epochs(1)` and set `emulator.locked_amount += reward` to simulate the validator reward landing.
4. Call `emulator.contract.ping()`.
5. Assert: `get_account_staked_balance(A)` increased by less than `reward * X / (X)` (i.e., less than what A would have received had B not deposited), and `get_account_staked_balance(B)` increased by `reward * Y / (X + Y)` despite B's stake not having been bonded during the epoch that earned `reward` — demonstrating reward attributed to a party (B) whose capital did not earn it, at the expense of A.

### Citations

**File:** staking-pool/src/lib.rs (L228-236)
```rust
    #[payable]
    pub fn deposit_and_stake(&mut self) {
        self.internal_ping();

        let amount = self.internal_deposit();
        self.internal_stake(amount);

        self.internal_restake();
    }
```

**File:** staking-pool/src/internal.rs (L9-21)
```rust
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
```

**File:** staking-pool/src/internal.rs (L70-106)
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

        // The staked amount that will be added to the total to guarantee the "stake" share price
        // never decreases. The difference between `stake_amount` and `charge_amount` is paid
        // from the allocated STAKE_SHARE_PRICE_GUARANTEE_FUND.
        let stake_amount = self.staked_amount_from_num_shares_rounded_up(num_shares);

        self.total_staked_balance += stake_amount;
        self.total_stake_shares += num_shares;
```

**File:** staking-pool/src/internal.rs (L194-250)
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

            env::log(
                format!(
                    "Epoch {}: Contract received total rewards of {} tokens. New total staked balance is {}. Total number of shares {}",
                    epoch_height, total_reward, self.total_staked_balance, self.total_stake_shares,
                )
                    .as_bytes(),
            );
            if num_shares > 0 {
                env::log(format!("Total rewards fee is {} stake shares.", num_shares).as_bytes());
            }
        }

        self.last_total_balance = total_balance;
        true
    }
```
