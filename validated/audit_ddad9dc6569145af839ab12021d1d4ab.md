### Title
Reward distributed by current share count with no time-weighting lets a same-epoch stake-then-unstake capture rewards earned by long-term delegators - (File: `staking-pool/src/internal.rs`)

### Summary
`internal_ping` computes the epoch reward as a lump sum (`total_balance - last_total_balance`) and mints value into `total_staked_balance` pro-rata to whatever `total_stake_shares` exist at the moment `ping` executes, with no accounting for how long each share was actually held during the rewarded epoch. An attacker can deposit and stake immediately before an epoch turnover (at the pre-reward share price) and then trigger `internal_ping` right after the turnover, so their brand-new shares participate fully in a reward their stake never economically earned, diluting the payout to delegators who were staked the entire epoch.

### Finding Description
The broken binding: for delegator `i`, `reward_i` should equal `total_reward * (shares_i_held_during_epoch / total_shares_held_during_epoch)`. Instead the contract computes: [1](#0-0) 

`total_reward = total_balance - last_total_balance` is added to `total_staked_balance` and thus to the share price used by `staked_amount_from_num_shares_rounded_down/up`, based solely on `total_stake_shares` **at the instant `ping` runs**, not on share-time-weighted exposure during the epoch that generated the reward.

`internal_stake` (called by `stake`, `stake_all`, `deposit_and_stake`) mints shares at the *current* price without any lockup or minimum holding period: [2](#0-1) 

Exploit flow:
1. Attacker calls `deposit_and_stake()` (or `stake()`) in the last block of epoch `E`, while `self.last_epoch_height == E`. `internal_ping()` is a no-op (no epoch change), so `internal_stake` mints shares at the pre-reward price and increases `total_stake_shares` without touching `last_total_balance` (which is only mutated by `internal_deposit`/`internal_withdraw`/`internal_ping`, confirming stake itself is balance-neutral for this accounting).
2. The chain rolls into epoch `E+1`; the NEAR runtime automatically credits the validator's `account_locked_balance()` with the epoch's staking reward — this happens independent of any contract call.
3. Attacker (or anyone) issues any state-changing call, which runs `internal_ping()` first. Since `epoch_height` changed, the accumulated reward is folded into `total_staked_balance`, raising the share price for **every current share holder**, including the attacker's shares minted in step 1.
4. Attacker immediately calls `unstake_all()`/`unstake()`, which itself first calls `internal_ping()` (a no-op now since already pinged this epoch) then `inner_unstake`, crediting `account.unstaked` at the already-appreciated price: [3](#0-2) 

The attacker's `unstaked` balance now reflects a slice of a reward their capital was never actually staked for during the period that generated it, at the expense of delegators who were staked the full epoch (their share of the same fixed reward pie shrinks because the denominator, `total_stake_shares`, was inflated by the attacker's late entry). No existing guard prevents this: `internal_ping`'s only assertion is that balance didn't decrease, there is no `assert_self()`/promise-result dependency to race, and no minimum-staking-duration check exists on `internal_stake`.

### Impact Explanation
This mis-credits staking reward that rightfully belongs to delegators who were staked across the whole epoch to an attacker who was exposed for as little as one block, and it is fully repeatable every epoch and across any staking pool instance deployed from this contract. This matches "High - rewards ... attributed to the wrong party," since NEAR is not moved out of the pool (funds stay internal to accounting), but an accounting value (`account.unstaked`/`stake_shares` value) diverges from what each delegator actually earned, and other delegators settle on the diminished value when they later unstake/withdraw.

### Likelihood Explanation
The precondition is only "an epoch reward is about to be, or was just, folded into `total_staked_balance`," which happens automatically every epoch. The attacker needs no privileged role, only enough capital to stake immediately before the boundary and unstake immediately after — both public, unrestricted entrypoints (`stake`/`deposit_and_stake`/`unstake`). Cost is limited to gas and the opportunity cost of one epoch's capital lockup risk, and the attack is trivially repeatable every epoch, against any pool, by any account, scaling with the amount staked.

### Recommendation
Do not distribute the epoch reward pro-rata to shares held at the instant of `ping`. Options: (a) snapshot `total_stake_shares` (and each account's shares) as of the *start* of the epoch being rewarded, and only distribute reward to shares that existed then; (b) require a minimum holding period (e.g., stake actions only take effect for reward-eligibility after N epochs, mirroring NEAR's own validator-stake activation lag) before newly staked shares participate in reward accrual; or (c) charge an entry/exit fee proportional to unrealized pending reward for stakes/unstakes that straddle an epoch boundary within a short window.

### Proof of Concept
Cargo test plan using the existing `Emulator` test harness in `staking-pool/src/lib.rs` (see `test_stake_all_unstake_all`):
1. Initialize pool, delegator A deposits and stakes `X` NEAR, `emulator.skip_epochs(N)` (staked across the full rewarded period), record locked amount increase to simulate reward `R`.
2. In a fresh scenario, delegator A stakes `X` and stays the whole epoch (control); delegator B deposits and stakes `X` in the *same block just before* `emulator.locked_amount` is bumped by `R` (simulating the reward credit), then immediately calls `ping()`/`unstake_all()` right after the bump.
3. Assert: `get_account_staked_balance(A)` reward share == `R * X_A/(X_A+X_B)` while B, despite zero epoch-long exposure, also receives `R * X_B/(X_A+X_B)` — proving reward is shared by current share count, not time-in-pool, and that A's realized reward is strictly less than `R` (the full amount it should have received alone), quantifying the diverted amount captured by B.

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
