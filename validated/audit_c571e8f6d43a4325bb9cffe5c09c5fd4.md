### Title
`internal_ping` reward-accounting invariant panics on any balance decrease, permanently freezing all delegator funds - ([File: staking-pool/src/internal.rs])

### Summary
`internal_ping`, which is invoked as the mandatory first step of every state-changing staking-pool method (`deposit`, `deposit_and_stake`, `withdraw_all`, `withdraw`, `stake`, `unstake`, `unstake_all`, `ping`), asserts that the current total account balance is never less than the previously recorded balance. If the contract's on-chain balance ever legitimately decreases between calls (e.g. validator slashing reduces `env::account_locked_balance()`, or a `Promise` transfer that was already debited from `last_total_balance` fails and returns funds inconsistently, or any other externally-driven balance reduction), this assertion panics unconditionally and there is no code path that tolerates or resets it. Because the assertion is unconditional and gates every subsequent action, this is analogous to the OpenLeverage finding: a defensive check meant to protect accounting integrity instead becomes a hard revert with no fallback, and once triggered it can never be un-triggered by any user or owner action, freezing every delegator's stake and unstaked balance indefinitely.

### Finding Description
`internal_ping` in `staking-pool/src/internal.rs` computes: [1](#0-0) 

```
let total_balance =
    env::account_locked_balance() + env::account_balance() - env::attached_deposit();

assert!(
    total_balance >= self.last_total_balance,
    "The new total balance should not be less than the old total balance"
);
```

This binding assumes `total_balance_now >= total_balance_recorded_last_epoch` always holds. That equality/inequality is not actually guaranteed by the protocol: it depends on external validator behavior (slashing), and on the assumption that `Promise::new(account_id).transfer(amount)` in `internal_withdraw` always succeeds and that `self.last_total_balance -= amount` there stays perfectly synced with the real on-chain balance.

Every mutating entrypoint calls `internal_ping()` unconditionally before doing anything else: [2](#0-1) 

So if `total_balance < self.last_total_balance` occurs even once (validator slashing event, or divergence introduced by the withdraw-then-account-deletion edge case documented in the README itself — "in a rare scenario, where the owner withdraws tokens and while the call is being processed deletes their account, the withdraw transfer will fail and the tokens will be returned to the staking pool" — which changes `env::account_balance()` without updating `last_total_balance`), then:
- `ping()` panics.
- `deposit`, `deposit_and_stake`, `withdraw_all`, `withdraw`, `stake`, `unstake`, `unstake_all` all panic identically, since they all call `internal_ping()` first.
- There is no owner method that can reset `last_total_balance` or bypass the check; the struct field is private and not exposed to any settable API.

This mirrors the OpenLeverage bug class exactly: a hard `require`/`assert` guarding against an "unexpected" price/balance movement instead reverts every attempted remediation, preventing the system from reacting to the very condition (balance drop) that most needs handling.

### Impact Explanation
Once triggered, no delegator (including the pool owner) can deposit, stake, unstake, or withdraw from the pool ever again — the staking pool contract becomes permanently bricked with all delegator funds (`Account.unstaked` and `Account.stake_shares` balances) frozen inside it. This satisfies the High/Critical impact criteria: "funds frozen for at least one epoch" (in practice indefinitely, since there is no recovery path without a full redeploy/migration, which is out of scope for an unprivileged actor but is nonetheless the practical consequence of this reachable, non-owner-gated invariant failure). The `lockup` contract that delegates through this staking pool (`lockup/src/owner.rs` `withdraw_from_staking_pool`, `unstake`, `refresh_staking_pool_balance`) would likewise be unable to retrieve locked owner funds, compounding the freeze across dependent contracts. [3](#0-2) 

### Likelihood Explanation
The trigger condition (`account_locked_balance() + account_balance() - attached_deposit() < last_total_balance`) is reachable without any privileged actor: it can occur from ordinary validator slashing (any staking pool's validator node can be slashed for downtime/double-signing, which is outside contract control) or from the withdraw-failure/account-deletion edge case the README itself calls out as "a rare scenario" but does not actually guard against in code. No malicious deployment or owner action is required — a normal NEAR protocol slashing event or transfer-fails race condition suffices. This is not a hypothetical "requires deployment ignoring init" scenario; it is a defect reachable in the documented, correctly-initialized contract during normal operation.

### Recommendation
Replace the hard `assert!` with a saturating/clamping comparison, e.g. treat `total_balance < last_total_balance` as "reward = 0" (no negative reward) rather than panicking, and separately track/report the shortfall (e.g. via an event/log) so delegators' effective balances degrade gracefully instead of the whole contract becoming permanently unusable. Additionally, ensure `internal_withdraw`'s `Promise::new(account_id).transfer(amount)` failure path (if any) reconciles `last_total_balance` rather than assuming the transfer's effect on balance always matches the pre-decremented bookkeeping.

### Proof of Concept
1. Deploy `staking-pool` contract and have delegators stake, causing `total_staked_balance` (and thus `account_locked_balance`) to be non-zero, recorded via `last_total_balance` in `internal_ping`. [4](#0-3) 
2. The pool's validator node gets slashed by the network (external event, no attacker action against this contract needed), reducing the real locked balance below `last_total_balance` on the next epoch boundary.
3. Any delegator calls `ping`, `deposit`, `withdraw`, `stake`, or `unstake` — `internal_ping` computes `total_balance < self.last_total_balance` and panics with `"The new total balance should not be less than the old total balance"`.
4. All future calls to any staking-pool mutating method panic identically forever, since `internal_ping()` is unconditionally called first in every method (`staking-pool/src/lib.rs:209-250` and remaining stake/unstake/withdraw methods), leaving all delegator `unstaked` and `stake_shares` balances permanently inaccessible.

### Citations

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

**File:** staking-pool/src/lib.rs (L209-250)
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
```
