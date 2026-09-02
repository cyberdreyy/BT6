### Title
Rewards accrued while `total_stake_shares == 0` inflate `total_staked_balance` without minting shares, permanently locking rewards and bricking staking - ([File: staking-pool/src/internal.rs])

### Summary
`internal_ping` distributes epoch rewards by adding them to `total_staked_balance` unconditionally, but only mints new "stake" shares to the owner when the computed `num_shares > 0`. When `total_stake_shares == 0` (a state reachable at genesis, since `new()` derives it 1:1 from `total_staked_balance` which can be `0` at deploy time), any reward observed by `ping()` inflates `total_staked_balance` while `total_stake_shares` stays `0`. This exactly mirrors the reported bug: an accounting balance is bumped while the accompanying "index"/shares bookkeeping is skipped because the divisor/guard condition is zero.

### Finding Description
`StakingContract::new` sets both fields from the same source value: [1](#0-0) [1](#0-0) 

If the account balance at deploy time equals exactly `STAKE_SHARE_PRICE_GUARANTEE_FUND`, both `total_staked_balance` and `total_stake_shares` are initialized to `0`.

`internal_ping` (called automatically before every action, and directly via the permissionless `ping()` entrypoint) computes `total_reward` from the raw NEAR account balance delta and unconditionally credits it to `total_staked_balance`, independent of whether any shares exist to represent it: [2](#0-1) 

The owner-share minting is gated behind `if num_shares > 0`, but the `total_staked_balance += owners_fee` line executes regardless: [3](#0-2) 

`num_shares_from_staked_amount_rounded_down` returns `total_stake_shares * amount / total_staked_balance`, which is always `0` when `total_stake_shares == 0`, regardless of `amount`: [4](#0-3) 

Once `total_staked_balance > 0` while `total_stake_shares == 0`, the very first delegator calling `stake()` will also compute `num_shares == 0` and hit the assertion that requires a positive share count: [5](#0-4) 

This breaks the binding `total_staked_balance == sum(stake_shares) * share_price`: the pool records a non-zero staked balance backed by zero shares, so no account can ever redeem it, and new stakers can no longer stake at all (their `stake` call reverts on the `num_shares > 0` assertion).

### Impact Explanation
Any NEAR that lands in the contract's balance while `total_stake_shares == 0` (e.g., gas fee rebates, or a plain unprivileged transfer to the pool account before the first delegator stakes) is folded into `total_staked_balance` by the very next `ping()` call, but is not represented by any "stake" share. This value becomes permanently unclaimable — it inflates the recorded staked balance with no corresponding claimant, and additionally permanently disables `stake()`/`deposit_and_stake()` going forward since `num_shares_from_staked_amount_rounded_down` will keep returning `0`. This matches the "funds frozen" / "accounting value diverging from reality" High-impact category: the ledger (`total_staked_balance`) diverges from what is actually redeemable (`total_stake_shares == 0`).

### Likelihood Explanation
The precondition (`total_stake_shares == 0` while the contract still holds a positive account balance) is reachable right after deployment, before the first delegator ever calls `deposit_and_stake`/`stake` — this is a normal window every staking pool passes through, not a contrived edge case. `ping()` is permissionless and can be triggered by anyone, and simply sending NEAR to the pool account (a plain transfer, no special privilege) is enough to create a reward delta that gets absorbed under this broken condition.

### Recommendation
In `internal_ping`, ensure that `total_staked_balance` is only incremented when `total_stake_shares > 0`, or seed `total_stake_shares` deterministically together with `total_staked_balance` so they can never diverge from a `0`/non-`0` mismatch. Alternatively, gate the entire reward-distribution branch behind `total_stake_shares > 0`, mirroring the corresponding check that already exists for `num_shares_from_staked_amount_rounded_down`/`_up`.

### Proof of Concept
1. Deploy the staking pool such that `env::account_balance()` at `new()` equals exactly `STAKE_SHARE_PRICE_GUARANTEE_FUND` (1 trillion yoctoNEAR), making `total_staked_balance == 0` and `total_stake_shares == 0`.
2. Before any delegator calls `deposit`/`stake`, an unprivileged account sends a plain NEAR transfer (e.g. 10 NEAR) directly to the pool's account ID.
3. Anyone calls `ping()`. `internal_ping` observes `total_balance > last_total_balance`, computes `total_reward = 10 NEAR`, and adds `remaining_reward`/`owners_fee` into `total_staked_balance`, while `num_shares_from_staked_amount_rounded_down(owners_fee)` returns `0` (since `total_stake_shares == 0`), so no shares are minted anywhere.
4. `total_staked_balance` is now `> 0` with `total_stake_shares == 0`.
5. A delegator calls `deposit_and_stake`/`stake`; `num_shares_from_staked_amount_rounded_down` returns `0` and the transaction reverts on `assert!(num_shares > 0, ...)` in `internal_stake`, permanently freezing both the phantom reward and all future staking on the pool. [6](#0-5) [4](#0-3)

### Citations

**File:** staking-pool/src/lib.rs (L185-199)
```rust
        let account_balance = env::account_balance();
        let total_staked_balance = account_balance - STAKE_SHARE_PRICE_GUARANTEE_FUND;
        assert_eq!(
            env::account_locked_balance(),
            0,
            "The staking pool shouldn't be staking at the initialization"
        );
        let mut this = Self {
            owner_id,
            stake_public_key: stake_public_key.into(),
            last_epoch_height: env::epoch_height(),
            last_total_balance: account_balance,
            total_staked_balance,
            total_stake_shares: NumStakeShares::from(total_staked_balance),
            reward_fee_fraction,
```

**File:** staking-pool/src/internal.rs (L70-82)
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
```

**File:** staking-pool/src/internal.rs (L192-234)
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
```

**File:** staking-pool/src/internal.rs (L261-272)
```rust
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
