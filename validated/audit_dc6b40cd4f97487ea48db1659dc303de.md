### Title
Flash-stake-before-ping reward theft: shares minted an instant before `internal_ping` capture a full epoch's yield they never earned - (File: `staking-pool/src/internal.rs`)

### Summary
`internal_stake` mints "stake" shares immediately at the current share price with no holding-period requirement, while `internal_ping` distributes the entire pending reward pro-rata across *whatever* `total_stake_shares` exist at the moment it runs, not the shares that were outstanding while the reward was actually earned. An attacker who stakes right before the epoch boundary and unstakes right after the next `ping`/`stake`/`unstake` call captures a share of a reward pool that was earned entirely by other delegators' capital, diluting them.

### Finding Description
The invariant the question states should hold is:
`reward_credited(account) == remaining_reward * (shares_held_by_account_throughout_epoch_E / total_shares_throughout_epoch_E)`

What the code actually implements is:
`reward_credited(account) == remaining_reward * (account.stake_shares_at_ping_call_time / total_stake_shares_at_ping_call_time)` [1](#0-0) 

`internal_ping` computes `total_reward` purely from the delta of the real on-chain balance (`account_locked_balance() + account_balance() - attached_deposit()` minus `last_total_balance`) and adds `remaining_reward` to `total_staked_balance` without changing `total_stake_shares` (aside from the owner's fee shares) — this is what causes the share price to jump for *every* currently outstanding share: [2](#0-1) 

`internal_stake` mints shares at the *current* price with no delay or vesting: [3](#0-2) 

Because NEAR's protocol-level validator seat weight (which actually determines how much real reward the pool earns) lags several epochs behind a `stake()` promise taking effect, a delegator's newly staked funds do not contribute to the reward that gets credited at the very next `ping`. Yet the contract has no way to know this — it simply distributes `remaining_reward` across `total_stake_shares` at call time. Exploit flow:

1. Near the end of epoch `E`, attacker calls `deposit_and_stake()` (or `stake_all()`), which calls `internal_ping()` first (no-op since already pinged this epoch), then `internal_stake` mints shares at the pre-reward price and bumps `total_staked_balance`/`total_stake_shares`. [4](#0-3) 
2. Epoch turns to `E+1`. Any subsequent action (attacker's own `unstake_all()`, or someone else's call) triggers `internal_ping()`, which now sees the real protocol-credited reward for epoch `E` and distributes it across *all* current shares — including the attacker's just-minted shares.
3. Attacker immediately calls `unstake_all()`, which itself first calls `internal_ping()` (no-op, already pinged) and then converts shares to unstaked balance at the new, reward-inflated price via `inner_unstake`. [5](#0-4) [6](#0-5) 

None of the existing guards address this: `internal_ping`'s only assertion is that the new balance is not less than the old one (protects against negative rewards, not against unfair distribution timing), and there is no `assert_owner`, minimum holding period, or share-price snapshot tied to when shares were minted relative to the reward-earning period. The larger the attacker's stake relative to the existing pool (`total_staked_balance`), the larger the fraction of `remaining_reward` they can siphon from long-term delegators — in the limit of a very large flash stake, the attacker can capture nearly the entire reward.

### Impact Explanation
Every delegator who was staked throughout epoch `E` ends up with fewer "stake" shares' worth of value than they should, because the pending reward pot gets split against an inflated `total_stake_shares` denominator that now includes the attacker's just-minted, non-contributing shares. This is a mis-crediting of staking rewards from the rightful long-term delegators to the attacker — matching the "rewards or owner fees attributed to the wrong party" High-severity category. It is repeatable every epoch, against any staking pool instance deployed from this contract, and scales with the size of the attacker's capital relative to the pool.

### Likelihood Explanation
The attack requires no privileged role — any account can `deposit_and_stake()` and later `unstake_all()`/`withdraw`. The only timing requirement is submitting the stake transaction near an epoch's end and the unstake shortly after the epoch rolls over and a ping-triggering call executes, which is observable/predictable since epoch boundaries are deterministic by block height. The attacker's cost is only the capital they stake for roughly one epoch transition (a few blocks), which they get back plus the captured reward; profitability increases with the amount staked relative to the existing pool, and the attack is trivially repeatable across epochs and across every deployed staking-pool instance.

### Recommendation
Decouple reward eligibility from raw share count at ping time: track a per-epoch "shares eligible for this epoch's reward" snapshot (e.g., distribute reward pro-rata only to shares that existed as of the *start* of the epoch being rewarded, not shares minted since), or require newly staked funds to only start accruing/earning from the next full epoch boundary after they are staked (mirroring nearcore's own stake-effect delay) before being counted in the `total_stake_shares` denominator used for the reward split.

### Proof of Concept
Using the existing `Emulator` test harness in `staking-pool/src/lib.rs` tests:
1. Set up two delegators, `alice` (long-term) and `mallory` (attacker).
2. `alice` deposits and stakes `X` NEAR at epoch `E0`. Advance several epochs so `alice`'s stake has been active the whole time.
3. Right before triggering the next reward (simulate `emulator.locked_amount += reward` to represent the epoch's protocol-credited reward, as done in `test_stake_with_fee`), have `mallory` call `deposit_and_stake()` with a large amount `Y` (e.g., comparable to or larger than the pool's current `total_staked_balance`) — this happens *before* the `ping()` call that will realize the reward.
4. Call `emulator.contract.ping()` to trigger `internal_ping`, distributing `remaining_reward`.
5. Immediately have `mallory` call `unstake_all()` and `withdraw_all()`.
6. Assert: `mallory`'s realized profit (`withdrawn_amount - Y`) is greater than zero and comparable in magnitude to a meaningful fraction of `remaining_reward`, while `alice`'s `get_account_staked_balance` is measurably lower than what it would be had `mallory` never staked (i.e., lower than `X + remaining_reward` when `alice` was the sole staker for that epoch) — proving reward was diverted from `alice` (who held shares for the full reward-earning period) to `mallory` (who held shares for one epoch transition only).

### Citations

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

**File:** staking-pool/src/internal.rs (L124-165)
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

        // The amount tokens that will be unstaked from the total to guarantee the "stake" share
        // price never decreases. The difference between `receive_amount` and `unstake_amount` is
        // paid from the allocated STAKE_SHARE_PRICE_GUARANTEE_FUND.
        let unstake_amount = self.staked_amount_from_num_shares_rounded_down(num_shares);

        self.total_staked_balance -= unstake_amount;
        self.total_stake_shares -= num_shares;
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

**File:** staking-pool/src/lib.rs (L227-236)
```rust
    /// Deposits the attached amount into the inner account of the predecessor and stakes it.
    #[payable]
    pub fn deposit_and_stake(&mut self) {
        self.internal_ping();

        let amount = self.internal_deposit();
        self.internal_stake(amount);

        self.internal_restake();
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
