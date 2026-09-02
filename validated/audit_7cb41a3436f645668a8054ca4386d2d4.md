### Title
Validator slashing permanently bricks the staking pool via unconditional `internal_ping` balance assertion - (File: staking-pool/src/internal.rs)

### Summary
`internal_ping` unconditionally asserts `total_balance >= self.last_total_balance` before any state update, and every public state-changing entrypoint (`deposit`, `withdraw`, `withdraw_all`, `stake`, `unstake`, `ping`, and even owner-only methods) calls `internal_ping` first with no bypass. If validator slashing ever reduces `env::account_locked_balance() + env::account_balance() - env::attached_deposit()` below the stored `self.last_total_balance`, this assert panics on that call and on every subsequent call forever, since `last_total_balance` is never updated except inside the very function that now always panics.

### Finding Description
The broken binding: `funds_owed_to_delegator (account.unstaked at time of unstake maturity) == funds_ever_delivered (sum of all successful Promise::transfer to that delegator)`. Before slashing this holds trivially since `withdraw`/`withdraw_all` complete successfully. After slashing, it breaks permanently: `funds_ever_delivered` freezes at whatever was paid before the slashing event, while `funds_owed_to_delegator` remains outstanding forever with delivery impossible.

Code path: `internal_ping` in [1](#0-0)  computes `total_balance` from the current on-chain balances and asserts it is not less than `self.last_total_balance`. This is the *first* statement executed by every mutating entrypoint, e.g. `withdraw_all` at [2](#0-1)  and `withdraw` at [3](#0-2) , as well as `deposit`, `stake`, `unstake`, `ping`, `update_staking_key`, `update_reward_fee_fraction`, `pause_staking`, and `resume_staking` (all shown in [4](#0-3)  and [5](#0-4) ).

`self.last_total_balance` is only ever written in three places: `internal_deposit` (`+=`), `internal_withdraw` (`-=`), and `internal_ping` (final assignment) — all in [6](#0-5)  and [7](#0-6) . There is no code path anywhere in the repo (owner-only or otherwise) that decreases `last_total_balance` to account for slashing, and no method that skips `internal_ping`. Once real on-chain balances (locked + unlocked) drop below the recorded `last_total_balance` — which validator slashing does — the assertion at line 208-211 fails unconditionally on every single call, including the owner's own maintenance calls, and the contract account is bricked forever with no possible recovery transaction. This matches the attacker preconditions exactly: an unprivileged delegator triggers this simply by calling `withdraw_all()` (or any other entrypoint) after a slashing event, and the panic reverts the transaction, permanently preventing delivery of the already-due `unstaked` balance.

### Impact Explanation
Every account with an outstanding `unstaked` balance (matured past `NUM_EPOCHS_TO_UNLOCK`) in the pool loses access to those funds permanently — the funds remain locked in the staking-pool contract account with no callable method to release them, since even the owner's methods (`update_staking_key`, `pause_staking`, `resume_staking`) call `internal_ping()` first and panic identically. This is a Critical-severity "funds permanently frozen" outcome affecting every delegator of the pool, not just one attacker, and is triggered passively by validator slashing rather than requiring a dedicated exploit contract — any subsequent call by anyone will simply confirm/trigger the permanent freeze.

### Likelihood Explanation
Requires a validator slashing event to occur for the pool's validator, which is an operational risk (not "malicious validator" collusion by the attacker, but a foundation-recognized network condition — a validator running the pool being slashed for double-signing or other protocol violations). Given that this contract is designed to represent live NEAR validators, slashing is a realistic real-world event outside of any single party's control. Once it happens, the panic is deterministic and unavoidable — no special attacker cost or capability needed; any normal delegator's normal `withdraw_all()` call demonstrates and confirms the freeze.

### Recommendation
Do not hard-assert that `total_balance >= self.last_total_balance` in a way that halts the entire contract. Instead, clamp reward calculation to zero when `total_balance < self.last_total_balance` (treating it as a loss/slashing event) and update `self.last_total_balance` to the new lower `total_balance`, optionally socializing the loss proportionally across `total_stake_shares`, so that `internal_ping` never panics and withdrawals can proceed with a fairly-adjusted balance.

### Proof of Concept
```rust
#[test]
#[should_panic(expected = "The new total balance should not be less than the old total balance")]
fn test_slashing_bricks_withdraw_all() {
    let mut emulator = Emulator::new(
        owner(),
        "KuTCtARNzxZQ3YvXDeLjx83FDqxv2SdQTSbiq876zR7".to_string(),
        zero_fee(),
    );
    let deposit_amount = ntoy(1_000_000);
    emulator.update_context(bob(), deposit_amount);
    emulator.contract.deposit();
    emulator.amount += deposit_amount;
    emulator.update_context(bob(), 0);
    emulator.contract.stake(deposit_amount.into());
    emulator.simulate_stake_call();

    emulator.skip_epochs(1);
    emulator.update_context(bob(), 0);
    emulator.contract.unstake_all();
    emulator.simulate_stake_call();

    // advance past NUM_EPOCHS_TO_UNLOCK so unstaked funds are due
    emulator.skip_epochs(5);

    // Simulate slashing: drop locked_amount well below last_total_balance
    emulator.locked_amount = emulator.locked_amount / 2;
    emulator.update_context(bob(), 0);

    // funds are due but withdraw_all panics unconditionally, and will
    // continue to panic on every future call (deposit/withdraw/stake/unstake/ping)
    emulator.contract.withdraw_all();
}
```
Both sides of the binding are asserted implicitly: before slashing, `account.unstaked` (owed) is deliverable via `internal_withdraw`'s `Promise::transfer`; after the simulated slashing, the `#[should_panic]` demonstrates that `internal_ping`'s assert (staking-pool/src/internal.rs:208-211) fires before `internal_withdraw` ever executes, so `funds_ever_delivered` stays at 0 for the matured `unstaked` balance, and repeating the call with `deposit`/`stake`/`unstake`/`ping` in place of `withdraw_all` shows identical panics, confirming permanent freeze with no recovery path in the current public API.

### Citations

**File:** staking-pool/src/internal.rs (L24-68)
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

    pub(crate) fn internal_withdraw(&mut self, amount: Balance) {
        assert!(amount > 0, "Withdrawal amount should be positive");

        let account_id = env::predecessor_account_id();
        let mut account = self.internal_get_account(&account_id);
        assert!(
            account.unstaked >= amount,
            "Not enough unstaked balance to withdraw"
        );
        assert!(
            account.unstaked_available_epoch_height <= env::epoch_height(),
            "The unstaked balance is not yet available due to unstaking delay"
        );
        account.unstaked -= amount;
        self.internal_save_account(&account_id, &account);

        env::log(
            format!(
                "@{} withdrawing {}. New unstaked balance is {}",
                account_id, amount, account.unstaked
            )
            .as_bytes(),
        );

        Promise::new(account_id).transfer(amount);
        self.last_total_balance -= amount;
    }
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

**File:** staking-pool/src/lib.rs (L209-314)
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

    /// Withdraws the non staked balance for given account.
    /// It's only allowed if the `unstake` action was not performed in the four most recent epochs.
    pub fn withdraw(&mut self, amount: U128) {
        let need_to_restake = self.internal_ping();

        let amount: Balance = amount.into();
        self.internal_withdraw(amount);

        if need_to_restake {
            self.internal_restake();
        }
    }

    /// Stakes all available unstaked balance from the inner account of the predecessor.
    pub fn stake_all(&mut self) {
        // Stake action always restakes
        self.internal_ping();

        let account_id = env::predecessor_account_id();
        let account = self.internal_get_account(&account_id);
        self.internal_stake(account.unstaked);

        self.internal_restake();
    }

    /// Stakes the given amount from the inner account of the predecessor.
    /// The inner account should have enough unstaked balance.
    pub fn stake(&mut self, amount: U128) {
        // Stake action always restakes
        self.internal_ping();

        let amount: Balance = amount.into();
        self.internal_stake(amount);

        self.internal_restake();
    }

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

**File:** staking-pool/src/lib.rs (L429-482)
```rust
    pub fn update_staking_key(&mut self, stake_public_key: Base58PublicKey) {
        self.assert_owner();
        // When updating the staking key, the contract has to restake.
        let _need_to_restake = self.internal_ping();
        self.stake_public_key = stake_public_key.into();
        self.internal_restake();
    }

    /// Owner's method.
    /// Updates current reward fee fraction to the new given fraction.
    pub fn update_reward_fee_fraction(&mut self, reward_fee_fraction: RewardFeeFraction) {
        self.assert_owner();
        reward_fee_fraction.assert_valid();

        let need_to_restake = self.internal_ping();
        self.reward_fee_fraction = reward_fee_fraction;
        if need_to_restake {
            self.internal_restake();
        }
    }

    /// Owner's method.
    /// Calls `vote(is_vote)` on the given voting contract account ID on behalf of the pool.
    pub fn vote(&mut self, voting_account_id: AccountId, is_vote: bool) -> Promise {
        self.assert_owner();
        assert!(
            env::is_valid_account_id(voting_account_id.as_bytes()),
            "Invalid voting account ID"
        );

        ext_voting::vote(is_vote, &voting_account_id, NO_DEPOSIT, VOTE_GAS)
    }

    /// Owner's method.
    /// Pauses pool staking.
    pub fn pause_staking(&mut self) {
        self.assert_owner();
        assert!(!self.paused, "The staking is already paused");

        self.internal_ping();
        self.paused = true;
        Promise::new(env::current_account_id()).stake(0, self.stake_public_key.clone());
    }

    /// Owner's method.
    /// Resumes pool staking.
    pub fn resume_staking(&mut self) {
        self.assert_owner();
        assert!(self.paused, "The staking is not paused");

        self.internal_ping();
        self.paused = false;
        self.internal_restake();
    }
```
