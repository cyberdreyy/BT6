### Title
Unstaked balance withdrawal debits the delegator's ledger before transfer settles, with no rollback on transfer failure - (File: `staking-pool/src/internal.rs`)

### Summary
`StakingContract::internal_withdraw` decrements the delegator's internal `unstaked` balance and the contract's `last_total_balance` and *then* fires an unconfirmed `Promise::new(account_id).transfer(amount)`, with no callback to verify the transfer succeeded and no state rollback path if it fails.

### Finding Description
The binding that must hold is: `sum(account.unstaked for all delegators) == NEAR actually recoverable/transferable by the contract`. `internal_withdraw` breaks this by updating the accounting side of the equation unconditionally before/independently of the transfer side: [1](#0-0) 

Specifically:
- `account.unstaked -= amount;` and `self.internal_save_account(...)` commit the debit to the delegator's ledger.
- `self.last_total_balance -= amount;` commits the debit to the contract-wide accounting.
- `Promise::new(account_id).transfer(amount);` is a fire-and-forget scheduled action with no `.then(...)` callback attached, unlike other flows in this same codebase (e.g. `internal_restake` at [2](#0-1) , or the lockup contract's staking callbacks such as `on_staking_pool_withdraw` in `lockup/src/owner_callbacks.rs`, which do check promise results before mutating state).

If the outgoing transfer fails at execution time (the deferred action executes in a receipt separate from the one that already committed the ledger debit), the NEAR is never delivered to the delegator, yet the internal `unstaked` balance and `last_total_balance` have already been permanently reduced. This is structurally the same class of bug as the reported MasterChef issue: the "claim recorded" (internal ledger) is decremented unconditionally, while the "value delivered" (the transfer) can silently diverge from it, and there is no `require`/assertion that guards against this divergence before mutating state.

### Impact Explanation
This breaks the "claims versus assets held" invariant for a staking pool contract, which the project's own README explicitly guarantees will not happen: "The contract can't lose or lock tokens of users" and "If a user deposited X, the user should be able to withdraw at least X" [3](#0-2) . If the transfer fails after the debit is committed, the delegator's tokens are effectively lost from their perspective — their tracked `unstaked` balance is zeroed/reduced but the NEAR was never delivered and cannot be re-claimed through `withdraw`/`withdraw_all` again (the ledger already reflects zero). This is a High-severity "accounting value diverging from reality" / frozen-funds scenario.

### Likelihood Explanation
The contract maintains a small `STAKE_SHARE_PRICE_GUARANTEE_FUND` (1 trillion yoctoNEAR) to absorb rounding losses from repeated `stake`/`unstake` rounding-down operations [4](#0-3) . Heavy or adversarial stake/unstake churn (analogous to the "excess user activity" that depleted MasterChef's Concur token supply in the original report) can erode this guarantee fund and the real NEAR balance backing delegator claims over time, at which point a subsequent `withdraw` call's `Promise::new(account_id).transfer(amount)` can fail against insufficient real balance while the ledger has already been debited. The bug is directly reachable by any unprivileged delegator calling `withdraw`/`withdraw_all`, requiring no privileged actor.

### Recommendation
Attach a callback to the withdrawal transfer (following the same pattern already used elsewhere in the codebase for `on_stake_action`/lockup owner callbacks) and only finalize the ledger debit (`account.unstaked -= amount`, `last_total_balance -= amount`) after confirming successful execution of the transfer, or restore the account's `unstaked` balance if the transfer promise fails.

### Proof of Concept
Conceptual reproduction (cannot be executed without deploying and modifying visibility, similar to the original report's methodology):
1. Multiple delegators perform many small `stake`/`unstake` cycles to erode the `STAKE_SHARE_PRICE_GUARANTEE_FUND` and drift the real contract NEAR balance below `last_total_balance`.
2. A delegator calls `withdraw(amount)`. `internal_withdraw` (`staking-pool/src/internal.rs:42-68`) decrements `account.unstaked` and `self.last_total_balance` by `amount`, then schedules `Promise::new(account_id).transfer(amount)`.
3. If the contract's actual NEAR balance is insufficient at the point the promise executes, the transfer fails; there is no `.then()` callback to detect this failure and restore `account.unstaked`.
4. The delegator's internal balance is now permanently short by `amount` even though the NEAR was never received.

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

**File:** staking-pool/README.md (L38-40)
```markdown
At the initialization the contract allocates one trillion yocto NEAR tokens towards "stake" share price guarantees.
This fund is later used to adjust the the amount of staked and unstaked tokens due to rounding error.
For each stake and unstake action, the contract may spend at most 1 yocto NEAR from this fund (implicitly).
```

**File:** staking-pool/README.md (L131-134)
```markdown
- The contract can't lose or lock tokens of users.
- If a user deposited X, the user should be able to withdraw at least X.
- If a user successfully staked X, the user can unstake at least X.
- The contract should not lock unstaked funds for longer than 4 epochs after unstake action.
```
