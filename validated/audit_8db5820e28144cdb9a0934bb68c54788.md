## Title
Reward-distribution front-running lets an attacker skim rewards from committed delegators - (File: `staking-pool/src/internal.rs`)

### Summary
The staking pool's `internal_ping` function distributes accrued rewards (validator rewards + gas fee rebates + accidental transfers) to all current stake-share holders in proportion to the shares they hold *at the moment `ping` executes*, with no time-weighting. An attacker can watch for an imminent reward-increasing event (e.g. a large incoming transfer/gas rebate, or the predictable epoch-boundary validator reward credit), call `deposit_and_stake` immediately before that event lands, then call `unstake_all`/`withdraw_all` right after, capturing a share of the reward proportional to capital that was never actually staked for the period that generated the reward. This dilutes the payout of delegators who kept capital staked and bore the actual risk/duration, exactly mirroring the "Guaranteed citadel profit" sandwich pattern (deposit before a value-increasing distribution, withdraw after).

### Finding Description
`internal_ping` computes `total_reward` as the difference between the current total balance and `last_total_balance`, then adds the non-owner portion directly to `total_staked_balance` without minting new shares for delegators [1](#0-0) . This raises the "stake" share price for every share outstanding at that instant, regardless of how long those shares have been held [2](#0-1) .

Every state-changing entrypoint calls `internal_ping()` first and is fully permissionless/unprivileged: `deposit_and_stake` (deposit + stake in one call) [3](#0-2) , `unstake_all` (converts current shares to unstaked balance at current price) [4](#0-3) , and `withdraw_all` (transfers out the unstaked balance after the unlock period) [5](#0-4) .

Formally, let `TSB`/`TSS` be total staked balance/shares before the attacker acts, and `R` the reward about to be credited. An attacker stakes `A` just before the reward lands, receiving `num_shares = A*TSS/TSB` (price-neutral) via `internal_stake` / `num_shares_from_staked_amount_rounded_down` [6](#0-5) . When `ping` then credits `R`, the resulting share price is strictly lower than it would have been had the attacker not joined, because the denominator of the no-attacker price `(TSB+R)/TSS` is smaller than the with-attacker-adjusted comparison by exactly `A*R/TSB` in relative terms — i.e. the attacker extracts `A*R/(TSB+A)` in value that would otherwise have accrued entirely to the pre-existing delegators. The attacker never bore the exposure (time, validator risk) that produced `R`, yet receives a proportional cut of it purely by timing their deposit around the reward event.

This is the same class of bug as the referenced Badger Citadel finding: sandwich a discrete, value-increasing distribution event by depositing right before and withdrawing right after, extracting reward that should belong to longer-term participants.

### Impact Explanation
This matches the "rewards or fees mis-attributed" High-impact category: value that should accrue to delegators who actually staked through the period that generated the reward is instead diverted to a short-term, opportunistic depositor. Every ping-triggering event (validator reward at epoch boundary, gas-fee rebates, or the explicitly documented case of accidental/intentional direct transfers to the pool account being redistributed to "active stake participants" [7](#0-6) ) is an exploitable window. Unlike the Badger case (21-day vesting exposure to a volatile governance token), here the "locked" period is only the 4-epoch unstake delay [8](#0-7)  and the asset recovered is the same NEAR, so the attacker bears essentially no price risk while waiting to withdraw — making this attack lower-risk and more attractive than the original report's analog.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to anticipate a reward-crediting `ping()` call and to have capital ready to stake immediately beforehand and unstake immediately after. Predictable/periodic validator rewards at epoch boundaries and mempool-visible large transfers to the pool account both provide realistic, repeatable triggers, and the attack requires no special privilege — any delegator account can execute it via the public `deposit_and_stake` / `unstake_all` / `withdraw_all` methods.

### Recommendation
Introduce time-weighting or a vesting/lock component for newly staked shares so that rewards accrued in a given epoch are distributed only to shares that were staked for the full period generating that reward (e.g., a minimum staking duration before a deposit participates in the next reward distribution), or otherwise decouple "just deposited" shares from the very next `ping()`'s reward allocation.

### Proof of Concept
1. Pool state: `total_staked_balance = TSB`, `total_stake_shares = TSS` (share price `TSB/TSS`).
2. Attacker observes an imminent reward-increasing event (e.g., a large transfer sitting in the pool's balance, or the epoch boundary about to trigger a validator reward `R`).
3. Attacker calls `deposit_and_stake` with amount `A`, receiving `num_shares = A*TSS/TSB` at unchanged price [3](#0-2) .
4. Any account calls (or the attacker's own next tx triggers) `internal_ping()`, crediting reward `R` into `total_staked_balance` without minting delegator shares [9](#0-8) , raising the share price for all current shareholders including the attacker.
5. Attacker immediately calls `unstake_all()` [4](#0-3) , locking in `A*(1 + R/(TSB+A))` — a guaranteed profit of `A*R/(TSB+A)` extracted from the reward that would otherwise have gone entirely to pre-existing delegators.
6. After 4 epochs, attacker calls `withdraw_all()` to realize the profit [5](#0-4) .

### Citations

**File:** staking-pool/src/internal.rs (L205-234)
```rust
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

**File:** staking-pool/src/internal.rs (L252-272)
```rust
    /// Returns the number of "stake" shares rounded down corresponding to the given staked balance
    /// amount.
    ///
    /// price = total_staked / total_shares
    /// Price is fixed
    /// (total_staked + amount) / (total_shares + num_shares) = total_staked / total_shares
    /// (total_staked + amount) * total_shares = total_staked * (total_shares + num_shares)
    /// amount * total_shares = total_staked * num_shares
    /// num_shares = amount * total_shares / total_staked
    pub(crate) fn num_shares_from_staked_amount_rounded_down(
        &self,
        amount: Balance,
    ) -> NumStakeShares {
        assert!(
            self.total_staked_balance > 0,
            "The total staked balance can't be 0"
        );
        (U256::from(self.total_stake_shares) * U256::from(amount)
            / U256::from(self.total_staked_balance))
        .as_u128()
    }
```

**File:** staking-pool/README.md (L90-94)
```markdown
Note, the if someone accidentally (or intentionally) transfers tokens to the contract (without function call), then
tokens from the transfer will be distributed to the active stake participants of the contract in the next epoch.
Note, in a rare scenario, where the owner withdraws tokens and while the call is being processed deletes their account, the
withdraw transfer will fail and the tokens will be returned to the staking pool. These tokens will also be distributed as
a reward in the next epoch.
```

**File:** staking-pool/README.md (L110-114)
```markdown
The remaining part of the reward is added to the total staked balance. This action increases the price of each "stake" share without
changing the amount of "stake" shares owned by different accounts. Which is effectively distributing the reward based on the number of shares.

The owner's reward is converted into "stake" shares at the new price and added to the owner's account.
It's done similarly to `stake` method but without debiting the unstaked balance of owner's account.
```

**File:** staking-pool/src/lib.rs (L81-85)
```rust
/// The number of epochs required for the locked balance to become unlocked.
/// NOTE: The actual number of epochs when the funds are unlocked is 3. But there is a corner case
/// when the unstaking promise can arrive at the next epoch, while the inner state is already
/// updated in the previous epoch. It will not unlock the funds for 4 epochs.
const NUM_EPOCHS_TO_UNLOCK: EpochHeight = 4;
```

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

**File:** staking-pool/src/lib.rs (L238-250)
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
```

**File:** staking-pool/src/lib.rs (L289-301)
```rust
    /// Unstakes all staked balance from the inner account of the predecessor.
    /// The new total unstaked balance will be available for withdrawal in four epochs.
    pub fn unstake_all(&mut self) {
        // Unstake action always restakes
        self.internal_ping();

        let account_id = env::predecessor_account_id();
        let account = self.internal_get_account(&account_id);
        let amount = self.staked_amount_from_num_shares_rounded_down(account.stake_shares);
        self.inner_unstake(amount);

        self.internal_restake();
    }
```
