### Title
Epoch-boundary reward sniping via last-second `deposit_and_stake`/`stake` lets an attacker capture rewards earned by other delegators - ([File: staking-pool/src/internal.rs])

### Summary
`StakingContract::internal_ping` distributes an entire epoch's accumulated reward pro-rata over `total_stake_shares` *at the moment `ping` is executed*, with no accounting for how long each share was actually staked during that epoch. Because `deposit`/`stake`/`deposit_and_stake` call `internal_ping()` and then immediately mint new shares at the pre-reward price, an attacker can join the pool right at an epoch boundary — before the first transaction of the new epoch triggers reward distribution — and receive a share of validation/gas-rebate/transfer rewards that were actually earned by capital that other delegators had locked in the pool for the entire prior epoch.

### Finding Description
The reward flow is:
1. `internal_stake` (`staking-pool/src/internal.rs:70-122`) mints `num_shares` at the *current* `total_staked_balance / total_stake_shares` price for whatever amount the caller deposits and stakes.
2. `internal_ping` (`staking-pool/src/internal.rs:194-250`) fires once per epoch transition. It computes `total_reward = total_balance - last_total_balance` and adds `remaining_reward` directly to `self.total_staked_balance`, which raises the price of *every* outstanding share equally — regardless of whether that share existed for the whole epoch or was minted seconds earlier in the same transaction sequence.
3. `stake`, `stake_all`, and `deposit_and_stake` (`staking-pool/src/lib.rs:227-287`) call `internal_ping()` first, then `internal_deposit`/`internal_stake`. If the caller is the one who crosses the epoch boundary, the ping executes *before* their new stake is added, so a normal “stake right after ping” doesn’t itself leak value.

However, the actual protocol-level stake change from `internal_restake` (`staking-pool/src/internal.rs:9-22`) only takes effect via a `Promise::stake` call that is applied by the runtime at a *future* epoch boundary (staking changes are not retroactive). This means the contract's internal share accounting (`total_stake_shares`, `total_staked_balance`) treats a delegator as a full economic participant in the *current* epoch's reward pool the instant they call `deposit_and_stake`/`stake`, even though their capital was never actually locked as validator stake during the epoch whose rewards are about to be distributed.

Consequently: an attacker who observes (via mempool/next-block visibility) that an epoch is about to roll over — or who simply calls `deposit_and_stake` with a very large amount immediately before anyone triggers the next `ping()` for the new epoch — obtains a large fraction of `total_stake_shares` before the pending epoch's `total_reward` (already accrued in `env::account_locked_balance()`/`account_balance()` from validation rewards, gas rebates, or even accidental direct transfers per the README's own admission) is folded into `total_staked_balance`. When that `ping()` fires, `remaining_reward` is split proportionally to shares held *at that instant*, so the attacker collects a share of the reward disproportionate to the time/capital they actually had at risk, at the expense of delegators who were staked the entire epoch. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation
This breaks the custody binding "shares charged versus shares redeemed" / "rewards mis-attributed": the number of stake shares an attacker is charged for their deposit does not correspond to the time-weighted contribution that legitimately earned the reward being distributed. Existing long-term delegators receive a smaller share of the reward pie than the capital and time they actually contributed, while the attacker — who bore no real epoch-long staking risk — captures value manufactured by other users' locked capital. This matches the "rewards or fees mis-attributed" High-impact category, since the divergence between recorded share claims and actually-earned rewards persists on the ledger (share price) permanently once diluted.

### Likelihood Explanation
The attack requires only: (a) capital to deposit a large stake for a short window, (b) visibility into when a new epoch has started or a large reward-generating transfer is imminent, and (c) a single `deposit_and_stake`/`stake` call timed before the first `ping()`-triggering transaction of the new epoch. No privileged role, owner key, or governance action is required — any account can call these public methods. The main constraint is capital cost and gas, similar to the "whale sniping" analog in the source report, making this a medium-likelihood, high-impact griefing/theft vector against passive delegators.

### Recommendation
Track staked shares against the epoch in which they became "locked" (i.e., only count a delegator's shares toward reward eligibility for rewards accrued after their stake was actually applied by `internal_restake`/validated by the protocol), or snapshot `total_stake_shares` at the start of the epoch for reward-splitting purposes rather than using the value at the moment `ping` executes. Alternatively, require `internal_ping()` to be invoked and settled independently of same-transaction stake/deposit actions so that new deposits never participate in a reward computation for a period during which they were not actually staked.

### Proof of Concept
1. Delegators A and B each `deposit_and_stake(1_000_000)` in epoch N and remain staked through epoch N+1, earning validator rewards throughout.
2. Near the very end of epoch N+1 (before anyone has called `ping()` for the epoch-N+1→N+2 transition), attacker C submits `deposit_and_stake(10_000_000)`. Because `internal_ping()` inside this call sees `last_epoch_height == epoch_height` (still epoch N+1), no reward distribution happens yet — C's shares are minted at the pre-reward price for epoch N+1's accrued (but not-yet-recognized) reward.
3. The epoch rolls over to N+2. The next call to any pool method (by A, B, or C) invokes `internal_ping()`, which now sees `epoch_height` changed and computes `total_reward` for all of epoch N+1's validation rewards, splitting it pro-rata over `total_stake_shares` — which now includes C's freshly minted, disproportionately large share count.
4. C receives a share of epoch N+1's rewards proportional to `10_000_000 / 12_000_000` of the pool, despite having contributed zero time-at-risk during epoch N+1, while A and B's expected share of the same reward is diluted below what their multi-epoch stake should have earned. [4](#0-3) [5](#0-4)

### Citations

**File:** staking-pool/src/internal.rs (L70-122)
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

        env::log(
            format!(
                "@{} staking {}. Received {} new staking shares. Total {} unstaked balance and {} staking shares",
                account_id, charge_amount, num_shares, account.unstaked, account.stake_shares
            )
                .as_bytes(),
        );
        env::log(
            format!(
                "Contract total staked balance is {}. Total number of shares {}",
                self.total_staked_balance, self.total_stake_shares
            )
            .as_bytes(),
        );
    }
```

**File:** staking-pool/src/internal.rs (L192-250)
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

**File:** staking-pool/src/lib.rs (L209-236)
```rust
    pub fn ping(&mut self) {
        if self.internal_ping() {
            self.internal_restake();
        }
    }

    /// Deposits the attached amount into the inner account of the predecessor.
    #[payable]
    pub fn deposit(&mut self) {
        let need_to_restake = self.internal_ping();

        self.internal_deposit();

        if need_to_restake {
            self.internal_restake();
        }
    }

    /// Deposits the attached amount into the inner account of the predecessor and stakes it.
    #[payable]
    pub fn deposit_and_stake(&mut self) {
        self.internal_ping();

        let amount = self.internal_deposit();
        self.internal_stake(amount);

        self.internal_restake();
    }
```
