### Title
Zero-share reward accrual permanently locks staked NEAR when all delegators unstake - ([File: staking-pool/src/internal.rs])

### Summary
`internal_ping` credits epoch rewards (staking rewards and gas-fee rebates) into `total_staked_balance` unconditionally whenever `total_reward > 0`, without checking whether `total_stake_shares` is `0`. If all delegators fully unstake (a normal, unprivileged action reducing both `total_staked_balance` and `total_stake_shares` to `0`), the very next reward tick (an automatic gas-rebate or validator reward, or even an unsolicited plain transfer to the pool account) re-inflates `total_staked_balance` above `0` while `total_stake_shares` stays `0`. From that point on, no account can ever mint new "stake" shares (`stake`/`deposit_and_stake`) or redeem existing ones (`unstake`), because the share-conversion formulas multiply by `total_stake_shares == 0`, permanently freezing the NEAR value recorded in `total_staked_balance`.

### Finding Description
`internal_ping` in [1](#0-0)  computes:
```
let owners_fee = self.reward_fee_fraction.multiply(total_reward);
let remaining_reward = total_reward - owners_fee;
self.total_staked_balance += remaining_reward;
let num_shares = self.num_shares_from_staked_amount_rounded_down(owners_fee);
...
self.total_staked_balance += owners_fee;
```
This code path runs unconditionally whenever `total_reward > 0` regardless of the value of `total_stake_shares`. It never checks `total_stake_shares == 0` before adding the reward to `total_staked_balance`.

`num_shares_from_staked_amount_rounded_down`, used both for the owner's fee shares and for every subsequent `stake` call, is defined as:
```
num_shares = total_stake_shares * amount / total_staked_balance
``` [2](#0-1) 

If `total_stake_shares == 0`, this always evaluates to `0` no matter how large `amount`/`total_staked_balance` are.

`internal_stake` then asserts `num_shares > 0` and reverts otherwise: [3](#0-2) . Likewise `inner_unstake` computes `num_shares` via `num_shares_from_staked_amount_rounded_up` (same `total_stake_shares` numerator) and asserts `num_shares > 0`: [4](#0-3) .

Sequence to reach the broken state (no owner/foundation/multisig involvement, purely delegator-driven):
1. A single delegator deposits and stakes, becoming the only shareholder: `total_staked_balance = X`, `total_stake_shares = S` (`S>0`).
2. That delegator calls `unstake_all`/`unstake` for their full position. `inner_unstake` drives both `total_staked_balance` and `total_stake_shares` down to `0` together (proportional accounting), leaving the pool with `total_staked_balance == 0` and `total_stake_shares == 0`.
3. Any subsequent epoch change with `total_reward > 0` — which happens automatically from validator rewards/gas-fee rebates credited to the pool account, or from an unsolicited plain NEAR transfer to the pool account (explicitly documented as being absorbed as reward, see `staking-pool/README.md:90-94`) — triggers `internal_ping`. Because `total_reward > 0`, `total_staked_balance` becomes `> 0` while `total_stake_shares` remains `0` (the owner's fee-share computation also yields `0` shares under the same zero-share numerator, so no shares are minted anywhere).
4. From now on `total_staked_balance` (equality to be maintained: `sum(account.stake_shares) * price == total_staked_balance`) is desynchronized from `total_stake_shares == 0`. Any `deposit_and_stake`/`stake` call reverts on `assert!(num_shares > 0, ...)`, and any `unstake` call similarly reverts, because both derive `num_shares` from the now-zero `total_stake_shares`. The NEAR value recorded in `total_staked_balance` can never again be attributed to, minted for, or withdrawn by any account.

This breaks the custody binding that "claims recorded" (`total_stake_shares`, and per-account `stake_shares`) must track NEAR actually locked/backing `total_staked_balance`: after step 3, `total_stake_shares == 0` but `total_staked_balance > 0`, and this state is unrecoverable through any public entry point.

### Impact Explanation
This is a Critical/High-severity "funds permanently frozen" condition: the NEAR credited into `total_staked_balance` after the pool's shares reach `0` becomes permanently unstakeable/unclaimable by any party, and the staking function is permanently bricked for the entire pool going forward (no future delegator can ever stake again since every `stake` call will revert). This matches the report's bug class of rewards being distributed and getting permanently stuck when there is no underlying liquidity/shares to receive them, translated to the NEAR staking-pool's share-accounting model.

### Likelihood Explanation
The precondition (all delegators unstaking down to `total_stake_shares == 0`) is a normal, permissionless action requiring no special privilege — a small pool with one or few delegators fully exiting is plausible, especially for a pool being wound down. Once that state is reached, the very next epoch's automatic reward/gas-rebate credit (which happens passively, without any explicit call) triggers the permanent freeze.

### Recommendation
In `internal_ping`, guard the reward crediting logic with an explicit check for `total_stake_shares == 0` (or equivalently `total_staked_balance == 0` at entry), and route any reward accrued during a zero-share interval to a separate, owner-claimable/accumulated-fees style variable instead of `total_staked_balance`, mirroring the external report's suggested fix of tracking such amounts in `accumulatedFees` rather than mixing them into the shared pool accounting that `num_shares_from_staked_amount_rounded_down`/`_up` depend on.

### Proof of Concept
1. Initialize the staking pool; delegator `bob` deposits and stakes `X` NEAR (`total_staked_balance = X`, `total_stake_shares = S`).
2. `bob` calls `unstake_all()` — `total_staked_balance` and `total_stake_shares` both go to `0` (see `internal.rs` unstake logic at [5](#0-4) ).
3. Advance one epoch so the pool account balance grows (validator reward or gas rebate, or an explicit plain transfer to the pool account as noted in `staking-pool/README.md:90-94`), then call `ping()`. `internal_ping` executes the block at [1](#0-0) , setting `total_staked_balance > 0` while `total_stake_shares` stays `0`.
4. Any new delegator (or `bob` again) calling `deposit_and_stake` will hit `assert!(num_shares > 0, ...)` in `internal_stake` ( [6](#0-5) ) and the transaction reverts, since `num_shares_from_staked_amount_rounded_down` returns `0` whenever `total_stake_shares == 0`. The pool is permanently unable to accept stake, and the reward NEAR recorded in `total_staked_balance` is unclaimable by anyone.

### Citations

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

**File:** staking-pool/src/internal.rs (L124-181)
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

        env::log(
            format!(
                "@{} unstaking {}. Spent {} staking shares. Total {} unstaked balance and {} staking shares",
                account_id, receive_amount, num_shares, account.unstaked, account.stake_shares
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

**File:** staking-pool/src/internal.rs (L212-234)
```rust
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
