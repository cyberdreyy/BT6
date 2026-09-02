Confirmed: every state-changing method (`deposit`, `deposit_and_stake`, `withdraw`, `withdraw_all`, `stake`, `stake_all`, `unstake`, `unstake_all`, `ping`) unconditionally calls `internal_ping()` first, and `internal_ping` contains a hard `assert!(total_balance >= self.last_total_balance, ...)` that panics if the pool's real NEAR balance (`account_locked_balance() + account_balance()`) ever drops below the last recorded total — which happens on validator slashing. This is a direct analog of the reported bug class (an assumed monotonic value that reverts on loss), and since literally every entrypoint funnels through this assert, a single slashing event permanently bricks the whole contract, freezing all delegator funds. [1](#0-0) [2](#0-1) 

### Title
Slashing-induced balance decrease permanently freezes the staking pool via a hard revert in `internal_ping` - (File: `staking-pool/src/internal.rs`)

### Summary
`internal_ping` (called by every mutating entrypoint of `StakingContract`) asserts that the current total account balance (`env::account_locked_balance() + env::account_balance() - env::attached_deposit()`) is never less than `self.last_total_balance`. NEAR's protocol allows validators to be slashed, which reduces `account_locked_balance()` for a validator account. Once that happens, every call to `ping`, `deposit`, `deposit_and_stake`, `withdraw`, `withdraw_all`, `stake`, `stake_all`, `unstake`, and `unstake_all` reverts, because each of them calls `internal_ping()` unconditionally before doing anything else.

### Finding Description
The contract maintains an invariant `total_balance >= last_total_balance` under the assumption that the pool's balance can only grow (via staking rewards or gas rebates): [3](#0-2) 

```rust
let total_balance =
    env::account_locked_balance() + env::account_balance() - env::attached_deposit();

assert!(
    total_balance >= self.last_total_balance,
    "The new total balance should not be less than the old total balance"
);
```

This mirrors the reported ActivePool bug: `sharesToAssets.sub(currentAllocated)` assumes the recorded/allocated value can never exceed the live, redeemable value, and reverts (underflow) the moment a loss occurs. Here, `last_total_balance` plays the role of "recorded claim" and `total_balance` (derived from the live locked+unlocked balance) plays the role of "value actually held." The binding that should hold is:

`total_balance (actual NEAR held/locked by the pool) >= last_total_balance (recorded claim)`

A validator slashing event breaks this equality by directly reducing `env::account_locked_balance()` below what was last recorded, without any code path in this contract to reconcile the loss.

Every state-changing function in `staking-pool/src/lib.rs` calls `self.internal_ping()` as its first action, with no way to skip it: [4](#0-3) [5](#0-4) [6](#0-5) 

Once the assert fires, it fires unconditionally and permanently on all future calls too — there is no admin function, owner method, or self-healing mechanism in `staking-pool/src/owner.rs` or elsewhere that lowers `last_total_balance` or otherwise reconciles a loss. The README itself acknowledges this design gap: "NOTE: Guarantees are based on the no-slashing condition. Once slashing is introduced, the contract will no longer provide some guarantees" — but the code has no defensive handling, it simply panics.

### Impact Explanation
Once slashing (or any other mechanism that reduces `account_locked_balance`/`account_balance` relative to the recorded total) occurs, the contract becomes permanently unusable:
- Delegators cannot `unstake` or `withdraw` their unstaked balance.
- The owner cannot `ping`, change keys, or restake.
- All previously staked/unstaked funds in the pool are frozen indefinitely, since no code path lowers `last_total_balance` to unblock the assert.

This matches the "Critical: funds permanently frozen" / "High: funds frozen for at least one epoch" impact categories, since the freeze is total and permanent absent a contract redeploy.

### Likelihood Explanation
This does not require any privileged actor, malicious insider, or contract owner action — it is triggered purely by the underlying NEAR protocol's validator slashing mechanism, which the staking pool contract explicitly exists to interact with (it stakes on behalf of delegators via `Promise::stake` in `internal_restake`). Any validator downtime or double-signing event affecting the pool's staked keys is sufficient. The delegator/unprivileged-attacker angle is that once any account has funds staked/unstaked in the pool, that party's funds become frozen through no fault or action of the owner (assuming an unprivileged party is simply relying on the pool's guarantees).

### Recommendation
Do not hard-assert `total_balance >= last_total_balance`. Instead, detect a decrease as a loss event: clamp `total_reward` to zero, optionally record/emit the loss, and set `last_total_balance = total_balance` (i.e., accept the lower balance) rather than reverting. This allows `unstake`/`withdraw`/`ping` to keep functioning after a slashing event, consistent with the "stake price never decreases" guarantee being explicitly a no-slashing-only guarantee — the contract should degrade gracefully rather than bricking entirely.

### Proof of Concept
1. Deploy `staking-pool` contract, initialize with `new(...)`, delegators deposit and stake NEAR via `deposit_and_stake`.
2. `internal_restake` stakes the pool's `total_staked_balance` with the validator key [7](#0-6) .
3. The validator running on behalf of this pool gets slashed by the NEAR protocol (e.g., double-signing or extended downtime), reducing `env::account_locked_balance()` for the pool's account below the previously recorded `last_total_balance`.
4. Any subsequent call — `ping`, `deposit`, `withdraw`, `stake`, `unstake`, etc. — invokes `internal_ping()` first, which computes `total_balance < self.last_total_balance` and panics on the assert [8](#0-7) .
5. Since the assert can never be satisfied again (there's no mechanism to lower `last_total_balance`), all delegators are permanently unable to unstake or withdraw their funds from the pool.

### Citations

**File:** staking-pool/src/internal.rs (L8-22)
```rust
    /// Restakes the current `total_staked_balance` again.
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
    }
```

**File:** staking-pool/src/internal.rs (L192-212)
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
```

**File:** staking-pool/src/lib.rs (L208-314)
```rust
    /// Distributes rewards and restakes if needed.
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
