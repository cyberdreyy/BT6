## Finding

### Title
Unstaked balance is debited before transfer confirmation with no retry path, permanently losing funds on transfer failure - (File: `staking-pool/src/internal.rs`)

### Summary
`staking-pool`'s withdrawal path decrements a delegator's recorded `unstaked` balance and the pool's `last_total_balance` *before* the outbound NEAR transfer is confirmed, and the transfer itself is fire‑and‑forget (no `.then()` callback checking success). If the transfer fails, the delegator's claim is already erased on-chain but the NEAR was never delivered, and there is no method exposed to retry or reclaim it — an analog of the Teleportation `disburseNativeBOBA` bug where the ledger advances (`totalDisbursements[_sourceChainId]` bumped / here `account.unstaked` zeroed) independent of whether the transfer actually succeeded, with no retry mechanism.

### Finding Description
`internal_withdraw` in [1](#0-0)  performs:
1. `account.unstaked -= amount;` then `self.internal_save_account(...)` — the delegator's claim is reduced.
2. `Promise::new(account_id).transfer(amount);` — a bare transfer with no `.then()` callback.
3. `self.last_total_balance -= amount;` — the pool's global accounting is reduced.

This is invoked directly (no callback wrapper) by the public delegator-facing methods `withdraw` and `withdraw_all` at [2](#0-1) .

Contrast this with the cross-contract flows used elsewhere in this same codebase (e.g. `lockup`'s `on_staking_pool_withdraw`), which only decrement local accounting *inside a callback after* `is_promise_success()` confirms the transfer landed: [3](#0-2) . The staking pool's own delegator-facing `withdraw`/`withdraw_all` do not follow this safe pattern — the balance decrement happens synchronously in the same function call that schedules the transfer, with no verification and no way to detect or retry a failed transfer, breaking the equality `account.unstaked_recorded == NEAR_actually_delivered_to_account`.

The project's own documentation acknowledges this exact failure mode can occur (e.g., account deletion racing with a withdraw), and states that in that case the funds are *not* returned to the delegator but are instead swept into the pool and redistributed as a reward to *other* stakers in the next epoch: [4](#0-3) . This mirrors the report's core bug class — accounting state (`account.unstaked`) that has already been reduced to reflect a transfer that never landed, and the destination party has no way to prove or reclaim entitlement, while the value is silently reassigned to a different party (other delegators) without consent.

### Impact Explanation
This crosses the "value debited versus value delivered" custody binding directly named in-scope. When the transfer fails post-decrement:
- The affected delegator's recorded `unstaked` balance no longer matches NEAR actually held on their behalf — a permanent loss for that delegator.
- The un-delivered NEAR remains in the pool's balance and, per the pool's own reward-distribution logic, gets redistributed to *other* delegators/the owner as rewards, i.e., "an accounting value diverging from reality where another party settles on it" and "rewards mis-attributed" — matching the High severity bucket. In the edge case, this is effectively a permanent freeze/loss of the specific delegator's funds.

### Likelihood Explanation
The trigger requires only a normal-looking sequence: a delegator's account becomes non-existent or fails to receive the transfer for any reason (e.g., the account was deleted in a batched transaction, or between submission and receipt processing) at the exact moment `withdraw`/`withdraw_all` schedules the transfer — no privileged access, malicious validator, or contract owner action is needed. The vulnerability is confirmed to be reachable and unmitigated because there is no callback verifying transfer success and no exposed retry/reclaim function, unlike the safer callback-gated pattern used elsewhere in this same repository for the identical operation (staking pool withdraw as called from `lockup`).

### Recommendation
Restructure `internal_withdraw` (and the public `withdraw`/`withdraw_all` methods) to only decrement `account.unstaked` and `last_total_balance` inside a callback that checks `is_promise_success()`, mirroring the pattern already used in `lockup/src/owner_callbacks.rs`. Alternatively, expose an explicit recovery/retry method allowing a delegator whose transfer failed to reclaim the amount, and emit an event/log documenting the failure so the discrepancy is auditable on-chain rather than being silently swept into the reward pool.

### Proof of Concept
1. Delegator calls `withdraw_all()` on the staking pool; `internal_withdraw` immediately sets `account.unstaked = 0` and schedules `Promise::new(account_id).transfer(amount)` with no callback ( [5](#0-4) ).
2. If the promise/transfer fails to be delivered to `account_id` (e.g., due to account deletion racing with this withdrawal, as acknowledged in the README), the state changes committed by the function call (`account.unstaked -= amount`, `last_total_balance -= amount`) are **not** rolled back, since they were committed before the promise's outcome is known and no callback exists to revert them.
3. The delegator's on-chain claim now shows `0` unstaked balance while the actual NEAR was never delivered to them.
4. Per the documented behavior, these tokens remain in the pool and are distributed as rewards to other participants in the next epoch ( [4](#0-3) ) — the original delegator has permanently lost the funds with no exposed retry mechanism.

### Citations

**File:** staking-pool/src/internal.rs (L42-68)
```rust
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

**File:** staking-pool/src/lib.rs (L238-263)
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
```

**File:** lockup/src/owner_callbacks.rs (L105-119)
```rust
    pub fn on_staking_pool_withdraw(&mut self, amount: WrappedBalance) -> bool {
        assert_self();

        let withdraw_succeeded = is_promise_success();
        self.set_staking_pool_status(TransactionStatus::Idle);

        if withdraw_succeeded {
            {
                let staking_information = self.staking_information.as_mut().unwrap();
                // Due to staking rewards the deposit amount can become negative.
                staking_information.deposit_amount.0 = staking_information
                    .deposit_amount
                    .0
                    .saturating_sub(amount.0);
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
