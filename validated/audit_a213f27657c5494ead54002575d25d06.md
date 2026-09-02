### Title
`internal_withdraw` updates account/pool balances before the NEAR transfer settles, with no callback to reverse state on failure - (File: `staking-pool/src/internal.rs`)

### Summary
`StakingContract::internal_withdraw` decrements the caller's `unstaked` balance and the contract's `last_total_balance` *before* firing a fire-and-forget `Promise::new(account_id).transfer(amount)`, and there is no `.then()` callback anywhere in `staking-pool/src` that checks whether this transfer promise actually succeeded and reverts the accounting if it doesn't.

### Finding Description
The `_call()` issue in the external report is a "fire transfer, don't verify it landed" pattern. `internal_withdraw` follows the same anti-pattern in the NEAR context: [1](#0-0) 

The function:
1. Asserts the account has enough `unstaked` balance.
2. Subtracts `amount` from `account.unstaked` and persists it via `internal_save_account`.
3. Fires `Promise::new(account_id).transfer(amount)` with no `.then(...)` callback.
4. Decrements `self.last_total_balance -= amount`.

Compare this to the contract's own `on_stake_action` pattern used elsewhere in the same crate, which *does* attach a callback (`ext_self::on_stake_action`) and inspects `PromiseResult` before deciding on follow-up state changes: [2](#0-1) . No equivalent verification exists for the withdrawal transfer path, and `SelfContract`'s `ext_contract` interface only exposes `on_stake_action`, confirming no withdrawal callback is defined: [3](#0-2) .

The custody binding broken is: `sum(account.unstaked across all accounts) + total_staked_balance == last_total_balance == actual NEAR held by the contract`. Once `internal_withdraw` runs, the ledger already reflects `amount` as paid out and removed from `last_total_balance`, regardless of whether the outbound `transfer` promise is ultimately delivered.

### Impact Explanation
On NEAR, a `Promise::new(receiver).transfer(amount)` action can still fail at execution time distinct from the calling receipt (e.g., insufficient prepaid gas for the transfer's execution outcome, the receiving account/contract rejecting/failing on receipt in ways that generate a separate failed receipt, or any other asynchronous execution failure of that action). Because `staking-pool` never attaches a callback to inspect the outcome of the withdrawal transfer, a failed transfer leaves `last_total_balance` permanently understated relative to the NEAR actually retained by the contract, and the withdrawing account's `unstaked` balance is already zeroed/reduced even though the funds never left the contract — the account cannot re-claim them, and the pool's accounting no longer matches its true holdings. This matches the "accounting value diverging from reality where another party settles on it" / "funds frozen" impact class, since subsequent computations (share price, `get_account_total_balance`, further withdrawals) rely on `last_total_balance` and `account.unstaked`, which are now wrong.

### Likelihood Explanation
Any unprivileged staking-pool depositor who has an available unstaked balance can trigger `internal_withdraw` via the public `withdraw`/`withdraw_all` entry points in `staking-pool/src/lib.rs`. No special preconditions beyond a normal deposit/unstake/withdraw lifecycle are required; the only requirement for the transfer promise to fail while the state update has already committed is an asynchronous execution failure of the transfer action itself, which is outside the withdrawing account's control but not outside its ability to trigger the vulnerable code path.

### Recommendation
Attach a `.then(ext_self::on_withdraw(...))` callback to the `Promise::new(account_id).transfer(amount)` in `internal_withdraw`, and only finalize the debit to `account.unstaked`/`last_total_balance` (or roll it back) after inspecting `is_promise_success()` in that callback, mirroring the pattern already used for `on_stake_action`.

### Proof of Concept
1. An account deposits and later unstakes, giving it a positive `account.unstaked` balance.
2. The account calls the public `withdraw` (or `withdraw_all`) method, which invokes `internal_withdraw(amount)`.
3. `internal_withdraw` immediately reduces `account.unstaked` and `self.last_total_balance` by `amount`, then issues `Promise::new(account_id).transfer(amount)` with no completion check: [4](#0-3) .
4. If the transfer promise fails to execute successfully (asynchronous failure of the transfer action), the NEAR never leaves the contract, but the ledger already reflects it as withdrawn — `last_total_balance` under-reports actual holdings and the account has lost access to the corresponding balance with no way to retry, since `account.unstaked` was already decremented.

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

**File:** staking-pool/src/lib.rs (L154-163)
```rust
/// Interface for the contract itself.
#[ext_contract(ext_self)]
pub trait SelfContract {
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
