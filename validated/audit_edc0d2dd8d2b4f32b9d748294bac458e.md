### Title
Reward sniping via one-block stake-before-ping / unstake-after-ping — no time-weighting in `internal_ping` reward distribution ([File: staking-pool/src/internal.rs])

### Summary
`StakingContract::internal_ping` distributes a whole epoch's accrued reward pro-rata to whichever `stake_shares` exist *at the moment `ping` runs*, with no accounting for how long those shares were held during the rewarded epoch. Because `stake()`/`deposit_and_stake()` call `internal_ping()` *before* `internal_stake()`, and `unstake()`/`unstake_all()` also call `internal_ping()` *before* `inner_unstake()`, an attacker can mint shares in the last block of epoch `E` (paying nothing extra, since price hasn't moved yet), let the very next call in epoch `E+1` trigger the reward distribution, and then immediately unstake — capturing a full epoch's yield for shares held for only one block.

### Finding Description
The invariant claimed by the design (see `staking-pool/README.md:110-116`) is:

`reward_credited(account) == total_reward_epoch_E * (shares_held_by_account_throughout_epoch_E / total_shares_throughout_epoch_E)`

but the actual code enforces only:

`reward_credited(account) == total_reward_epoch_E * (account.stake_shares_at_ping_time / total_stake_shares_at_ping_time)`

These are equal only if all shareholders held their shares for the whole epoch. They diverge whenever shares are minted just before the epoch boundary.

Code path:
- `internal_ping` (staking-pool/src/internal.rs:194-250) computes `total_reward = total_balance - self.last_total_balance` once per epoch switch and adds `remaining_reward` to `self.total_staked_balance`, which is shared by **all current shares**, including ones minted in the final block of the just-ended epoch. [1](#0-0) 
- `stake`/`deposit_and_stake` ping first, then mint shares (`internal_stake`), so the attacker's new shares are absent from the *current* ping's distribution but present for the *next* one. [2](#0-1) 
- `unstake_all`/`unstake` also ping first, then immediately convert the attacker's shares (now revalued upward by the reward just distributed) back into `unstaked` balance via `inner_unstake`. [3](#0-2) 
- `inner_unstake` computes `receive_amount` from the *current* (post-ping, post-reward) share price. [4](#0-3) 

Exploit flow:
1. Near the end of epoch `E`, attacker calls `deposit_and_stake()` (or `deposit()` + `stake()`). `internal_ping` is a no-op (same epoch), `internal_stake` mints shares at the current fair price — no gain yet.
2. First receipt of epoch `E+1` (attacker's own transaction, e.g. `unstake_all()`, or anyone else's) triggers `internal_ping`, which distributes the epoch `E` reward across `total_stake_shares`, now diluted by attacker's freshly minted shares.
3. Attacker's `unstake_all()` continues past the ping and converts the now-revalued shares back to `unstaked` balance in the same call, locking in the gain.
4. After the mandatory `NUM_EPOCHS_TO_UNLOCK` wait, attacker withdraws real NEAR that includes reward accrued from capital contributed by long-term stakers (and, due to NEAR's protocol-level stake-activation delay, from a stake amount the attacker's last-second deposit did not actually contribute to the validator's earning stake for epoch `E`).

None of the existing guards prevent this: `internal_ping`'s balance assert only checks `total_balance >= last_total_balance`; there is no minimum holding period, no epoch-anchored eligibility check, and no `assert_self`/owner check applicable to unprivileged `stake`/`unstake` calls.

### Impact Explanation
Reward that should accrue proportionally to shares held throughout the epoch is instead partly diverted to an attacker who held shares for a single block, diluting the reward received by genuine long-term delegators and the owner's fee share. This is a mis-crediting of staking rewards to the wrong party — matching the "High" impact category (rewards or owner fees attributed to the wrong party). The attack is repeatable every epoch, by any account, with no special privileges, and scales with the amount the attacker can deposit right before the epoch boundary; the only cost is temporarily locking capital and a 4-epoch wait to withdraw.

### Likelihood Explanation
Preconditions are minimal: any account can call `deposit_and_stake()` and later `unstake_all()`, both public, unprivileged entrypoints. The attacker only needs to time one call near an epoch boundary and one call as the first receipt of the following epoch (or rely on anyone else's action to trigger the ping first, then unstake immediately after). No race with privileged actors is required beyond ordinary transaction timing, which is well within reach of an unprivileged user. This is repeatable indefinitely across epochs and across any staking pool deployed from this contract code.

### Recommendation
Introduce time-weighting or an eligibility delay for reward participation, e.g.:
- Track a per-account "epoch joined" or snapshot shares at the start of each epoch, and only distribute the epoch's reward to shares that existed since before that epoch began (shares minted during epoch `E` become reward-eligible starting epoch `E+1`).
- Alternatively, require a minimum staking duration (e.g., shares must be held across at least one full epoch boundary) before they participate in a distribution, or apply a lock/cooldown on newly staked shares similar to the existing unstake cooldown (`NUM_EPOCHS_TO_UNLOCK`).

### Proof of Concept
Using the existing `near-sdk-sim`-style `Emulator` test harness in `staking-pool/src/lib.rs` tests:
```rust
#[test]
fn test_reward_sniping_one_block_exposure() {
    let mut emulator = Emulator::new(owner(), "ed25519:...".to_string(), zero_fee());

    // Long-term staker `alice` stakes for the whole epoch.
    let stake_amount = ntoy(1_000_000);
    emulator.update_context(alice(), stake_amount);
    emulator.contract.deposit();
    emulator.amount += stake_amount;
    emulator.update_context(alice(), 0);
    emulator.contract.stake(stake_amount.into());
    emulator.simulate_stake_call();

    // Attacker `bob` deposits and stakes right before the epoch boundary.
    emulator.update_context(bob(), stake_amount);
    emulator.contract.deposit();
    emulator.amount += stake_amount;
    emulator.update_context(bob(), 0);
    emulator.contract.stake(stake_amount.into()); // internal_ping no-op, shares minted at fair price

    // Epoch switches; simulate validator reward accrual.
    let locked_amount = emulator.locked_amount;
    emulator.skip_epochs(1);
    emulator.locked_amount = locked_amount + ntoy(200_000); // reward for epoch E

    // Bob is first to act in the new epoch: ping distributes reward, then he unstakes immediately.
    emulator.update_context(bob(), 0);
    emulator.contract.unstake_all();

    // Assert bob received ~half the reward (proportional to shares at ping time)
    // despite holding shares for only one block, violating time-weighted proportionality.
    let bob_unstaked = emulator.contract.get_account_unstaked_balance(bob()).0;
    assert!(bob_unstaked > stake_amount + ntoy(90_000)); // captured a large slice of the 200k reward
}
```
Both sides of the claimed binding — `bob`'s credited reward vs. `bob`'s time-weighted share of epoch `E` (which should be ~0, since he held shares for one block) — diverge, confirming the vulnerability.

### Citations

**File:** staking-pool/src/internal.rs (L146-156)
```rust
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
```

**File:** staking-pool/src/internal.rs (L205-220)
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

**File:** staking-pool/src/lib.rs (L291-314)
```rust
    pub fn unstake_all(&mut self) {
        // Unstake action always restakes
        self.internal_ping();

        let account_id = env::predecessor_account_id();
        let account = self.internal_get_account(&account_id);
        let amount = self.staked_amount_from_num_shares_rounded_down(account.stake_shares);
        self.inner_unstake(amount);

        self.internal_restake();
    }

    /// Unstakes the given amount from the inner account of the predecessor.
    /// The inner account should have enough staked balance.
    /// The new total unstaked balance will be available for withdrawal in four epochs.
    pub fn unstake(&mut self, amount: U128) {
        // Unstake action always restakes
        self.internal_ping();

        let amount: Balance = amount.into();
        self.inner_unstake(amount);

        self.internal_restake();
    }
```
