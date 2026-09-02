## No vulnerability found for this question.

**Analysis supporting this conclusion:**

The binding claimed to be broken is `self.total_staked_balance == env::account_locked_balance()`. Tracing the code shows this binding can indeed diverge after `on_stake_action`'s failure branch fires [1](#0-0) , and this is explicitly acknowledged in the interface doc comment as a known limitation: "This might lead to inconsistent state and the follow withdraw calls might fail" [2](#0-1) .

However, this divergence does not enable fund drainage because:

1. `internal_withdraw` never reads `env::account_locked_balance()` — it only checks per-account bookkeeping (`account.unstaked >= amount`) and then issues `Promise::new(account_id).transfer(amount)` [3](#0-2) . Withdrawal correctness depends solely on `account.unstaked`, not on the locked/unlocked classification of the contract's NEAR balance.

2. When the validator-level "stake" action fails and `on_stake_action` re-stakes with `0`, the NEAR that was previously locked does not leave the contract account — it becomes unlocked (i.e., moves from `account_locked_balance()` into `account_balance()`). The total NEAR held by the contract is unaffected by this transition [4](#0-3) .

3. `internal_ping` computes `total_balance = account_locked_balance() + account_balance() - attached_deposit()` [5](#0-4) , which remains consistent across the locked→unlocked transition since the sum doesn't change, so this doesn't cause reward mis-accounting either.

4. The scheduled `Promise::new(env::current_account_id()).stake(self.total_staked_balance, ...)` in `internal_restake` [6](#0-5)  captures a fixed stake amount at scheduling time as an immutable queued action; it cannot be retroactively altered by another delegator's later `deposit_and_stake()` call. Each top-level call (`unstake`, `deposit_and_stake`, etc.) runs to completion atomically before the next transaction executes, so there is no way for one user's in-flight promise chain to be "hijacked" by another's unrelated call to change its staked amount or trigger a different failure branch than what was already determined by protocol-level minimum-stake rules.

5. The `on_stake_action` failure branch is triggered by a genuine protocol-level stake-action failure (e.g., below minimum validator stake threshold), which is not something an unprivileged delegator can arbitrarily "craft" via call timing against another user's unrelated deposit.

Since the premise requires NEAR to actually leave the pool's account or be double-claimed beyond what the account holds, and the mechanism described only reclassifies locked vs. unlocked NEAR within the same account (funds remain available for legitimate future withdrawals), there is no path to Critical impact as defined by the rules. This matches a documented, acknowledged accounting corner case rather than an exploitable fund-drain vector.

### Citations

**File:** staking-pool/src/lib.rs (L157-163)
```rust
    /// A callback to check the result of the staking action.
    /// In case the stake amount is less than the minimum staking threshold, the staking action
    /// fails, and the stake amount is not changed. This might lead to inconsistent state and the
    /// follow withdraw calls might fail. To mitigate this, the contract will issue a new unstaking
    /// action in case of the failure of the first staking action.
    fn on_stake_action(&mut self);
}
```

**File:** staking-pool/src/lib.rs (L399-421)
```rust
    pub fn on_stake_action(&mut self) {
        assert_eq!(
            env::current_account_id(),
            env::predecessor_account_id(),
            "Can be called only as a callback"
        );

        assert_eq!(
            env::promise_results_count(),
            1,
            "Contract expected a result on the callback"
        );
        let stake_action_succeeded = match env::promise_result(0) {
            PromiseResult::Successful(_) => true,
            _ => false,
        };

        // If the stake action failed and the current locked amount is positive, then the contract
        // has to unstake.
        if !stake_action_succeeded && env::account_locked_balance() > 0 {
            Promise::new(env::current_account_id()).stake(0, self.stake_public_key.clone());
        }
    }
```

**File:** staking-pool/src/internal.rs (L9-22)
```rust
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

**File:** staking-pool/src/internal.rs (L42-67)
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
```

**File:** staking-pool/src/internal.rs (L205-206)
```rust
        let total_balance =
            env::account_locked_balance() + env::account_balance() - env::attached_deposit();
```
