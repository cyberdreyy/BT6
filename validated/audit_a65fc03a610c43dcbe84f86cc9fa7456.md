### Title
Reward distributed pro‑rata to *current* stake shares rather than time‑weighted shares, letting a last‑block staker capture rewards earned entirely by earlier stakers - (`staking-pool/src/internal.rs`)

### Summary
`internal_ping` distributes the entire reward accrued since the last ping (`total_balance - last_total_balance`) by uniformly raising the "stake" share price for **whoever currently holds shares**, with no record of how long those shares were held during the rewarded period. Because every public entry point (`stake`, `deposit_and_stake`, `unstake`, `unstake_all`, …) calls `internal_ping()` *before* mutating the caller's own shares, an attacker with zero prior footprint (`internal_get_account` → `Account::default()`) can stake right before the reward gets banked and unstake right after, collecting a full pro‑rata cut of a reward that its capital did not actually help earn.

### Finding Description
The binding the design should preserve is:
`account.stake_shares_held_during_epoch / total_stake_shares_during_epoch == reward credited to account / total_reward`
i.e., reward attribution should be proportional to *time-weighted* share ownership across the rewarded period.

What the code actually implements is:
`account.stake_shares_at_ping_time / total_stake_shares_at_ping_time == reward credited to account / total_reward`

Trace:
- `internal_ping` (`staking-pool/src/internal.rs:194-250`) computes `total_reward = total_balance - self.last_total_balance` and does `self.total_staked_balance += remaining_reward` — a single global share-price bump applied to **all** shares that exist at the moment `ping` executes, with no per-account timestamp or checkpoint of when those shares were minted. [1](#0-0) 
- `stake()` / `unstake()` / `unstake_all()` / `deposit_and_stake()` all call `self.internal_ping()` first, then mutate the caller's shares via `internal_stake` / `inner_unstake`. [2](#0-1) [3](#0-2) 
- `internal_stake` mints `num_shares` at the pre-ping share price and immediately adds them to `total_stake_shares`/`total_staked_balance`. [4](#0-3) 
- A brand-new attacker address has no row in `accounts`, so `internal_get_account` returns `Account::default()` with `unstaked=0, stake_shares=0` — nothing prevents a first-time caller from immediately depositing and staking. [5](#0-4) 

Exploit flow: attacker calls `deposit_and_stake()` late in epoch `N` (before any account calls `ping`, i.e. before the epoch's accrued validator reward is banked). Because `internal_ping` in that same call sees `last_epoch_height == epoch_height`, it does nothing — the attacker's new shares are minted at the *old* (pre-reward) share price without paying anything for the reward that is about to be banked. As soon as the epoch height changes (protocol-level event, not attacker-controlled but publicly observable), the attacker calls `unstake_all()`; its embedded `internal_ping()` now detects the epoch change, computes `total_reward` (the reward already earned collectively by pre-existing stakers over epoch `N`), and raises `total_staked_balance` — uniformly increasing the redemption value of **every** outstanding share, including the ones the attacker minted moments earlier. `inner_unstake` then converts the attacker's shares back to `unstaked` balance at this now-inflated price, crediting the attacker a full pro-rata slice of the epoch's reward for having been exposed for a single block.

No existing guard stops this: `internal_ping`'s only assertion is `total_balance >= last_total_balance` (monotonicity, not fairness); there is no `assert_self`, no minimum holding period, no time-weighted share accounting, and the U256 rounding pairs only bound rounding error, not the fundamental non-time-weighted distribution.

### Impact Explanation
Existing long-term delegators (and the pool owner, whose fee is also computed from `total_reward` at the same ping) see their effective yield diluted every time an outsider times a stake/unstake pair around a ping-triggering epoch boundary. NEAR (in the form of stake-share value) that should have accrued only to the delegators who bore stake exposure for the epoch is instead partially redirected to a delegator who was exposed for a single block/transaction. This is repeatable every epoch, against any pool running this contract, at zero cost beyond gas and the amount temporarily staked (which is fully recoverable). This matches "rewards … attributed to the wrong party" — High severity.

### Likelihood Explanation
No special privileges, roles, or balances are required — this is exactly the class of caller the threat model designates as in-scope (unprivileged, choosing amounts and call ordering, repeatable across epochs and pools). The only precondition is public, deterministic information: the current `epoch_height()` and whether `ping` has been called yet this epoch, both readable via view calls before submitting the stake/unstake transactions. The attacker's capital is at risk for at most the unstaking-delay withdrawal lock (`NUM_EPOCHS_TO_UNLOCK` = 4 epochs) but that capital is never lost, only temporarily illiquid, while the extracted reward share is a net gain extracted from other delegators/the owner.

### Recommendation
Introduce time-weighted (or at least ping-boundary-aligned) share accounting: either (a) force `internal_ping` to run and bank rewards *before* any new stake is accepted so that newly minted shares never retroactively participate in a reward whose accrual period predates them, and *after* an unstake is fully processed so unstaking shares don't get credited for a reward period they weren't exposed to at its start, or (b) track a `last_deposit_epoch`/checkpoint per account and only allow shares to participate in reward distributions for epochs that started after they were minted (e.g., a minimum holding-epoch requirement mirroring `NUM_EPOCHS_TO_UNLOCK`).

### Proof of Concept
`cargo test` plan (extend `staking-pool/src/lib.rs` test module using the existing `Emulator` harness):
1. Set up the pool with `zero_fee()`; have `alice()` `deposit_and_stake()` a large amount in epoch 0 and call `simulate_stake_call()`.
2. `skip_epochs(N)` to accrue reward via `emulator.locked_amount += reward`, but do **not** call `ping()` yet.
3. In the *same* epoch (still `last_epoch_height` stale), have `bob()` (fresh account, no prior row — verify via `get_account_staked_balance(bob())==0` beforehand) call `deposit_and_stake(small_amount)`. Assert `internal_ping` did not fire a reward for this call (`total_reward` unbanked).
4. `skip_epochs(1)` to move to a new epoch height (simulating the protocol epoch boundary), still without calling `ping`.
5. Have `bob()` call `unstake_all()`, which triggers `internal_ping` internally and bank the entire multi-epoch reward.
6. Assert: `bob()`'s `unstaked_balance` after step 5 exceeds `small_amount` by a reward-proportional amount `small_amount/total_stake_shares_at_step5 * total_reward`, even though `bob()` was only present for one epoch transition, while `alice()`'s reward share (via `get_account_staked_balance(alice())`) is diluted below what it would have been absent `bob()`'s participation — directly comparing the two delegators' reward-per-epoch-of-exposure ratios, showing they are not proportional to actual epoch exposure, falsifying the claimed invariant.

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

**File:** staking-pool/src/internal.rs (L212-220)
```rust
        let total_reward = total_balance - self.last_total_balance;
        if total_reward > 0 {
            // The validation fee that the contract owner takes.
            let owners_fee = self.reward_fee_fraction.multiply(total_reward);

            // Distributing the remaining reward to the delegators first.
            let remaining_reward = total_reward - owners_fee;
            self.total_staked_balance += remaining_reward;

```

**File:** staking-pool/src/internal.rs (L323-326)
```rust
    /// Inner method to get the given account or a new default value account.
    pub(crate) fn internal_get_account(&self, account_id: &AccountId) -> Account {
        self.accounts.get(account_id).unwrap_or_default()
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

**File:** staking-pool/src/lib.rs (L306-314)
```rust
    pub fn unstake(&mut self, amount: U128) {
        // Unstake action always restakes
        self.internal_ping();

        let amount: Balance = amount.into();
        self.inner_unstake(amount);

        self.internal_restake();
    }
```
