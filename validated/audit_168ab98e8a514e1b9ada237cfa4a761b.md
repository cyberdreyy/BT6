## Confirmed vulnerable functions

`refresh_staking_pool_balance` → `on_get_account_total_balance` (in `lockup/src/owner_callbacks.rs:280-294`) and `withdraw_all_from_staking_pool` → `on_get_account_unstaked_balance_to_withdraw_by_owner` (`lockup/src/owner_callbacks.rs:296-339`) use a `#[callback]` parameter to deserialize the staking pool's return value. [1](#0-0) [2](#0-1) 

Similarly, the foundation termination path uses `#[callback]`-based handlers: `on_get_account_staked_balance_to_unstake` and `on_get_account_unstaked_balance_to_withdraw` in `lockup/src/foundation_callbacks.rs:7-53,95-139`. [3](#0-2) [4](#0-3) 

## The broken binding

Invariant claimed: `staking_information.status` always eventually returns to `Idle` (or `TerminationStatus` always eventually reaches `ReadyToWithdraw`), i.e. `status_after_promise_settles == Idle ∨ ReadyToWithdraw` for every dispatched query. In these `#[callback]`-annotated handlers, if the antecedent promise (`get_account_total_balance`, `get_account_unstaked_balance`, `get_account_staked_balance`) fails because the target account no longer exists, the near-sdk-generated callback body panics *before* user code executes (it cannot deserialize a value from a failed `PromiseResult`), so the `Idle`/next-status transition is never written. But the earlier call that entered the "Busy" / `UnstakingInProgress` state (`refresh_staking_pool_balance`, `withdraw_all_from_staking_pool`, `termination_prepare_to_withdraw`) already committed as a separate, already-successful transaction. Thus `status` is stuck at `Busy` (or `TerminationStatus::UnstakingInProgress` / `WithdrawingFromStakingPoolInProgress`) forever.

By contrast, handlers such as `on_staking_pool_deposit`, `on_staking_pool_withdraw`, `on_staking_pool_stake`, `on_staking_pool_unstake`, `on_staking_pool_unstake_all`, and `on_staking_pool_deposit_and_stake` use `is_promise_success()` with no `#[callback]` parameter, so they run their bodies and correctly reset status to `Idle` on failure — these are safe. [5](#0-4) 

## Exploit path

Since `assert_staking_pool_is_idle()` gates every owner staking method and `unselect_staking_pool`, and `assert_no_staking_or_idle()` (which checks the same `TransactionStatus`) gates `transfer`, once `status` is stuck at `Busy`, **all** owner staking calls, `unselect_staking_pool`, and `transfer` permanently panic. [6](#0-5) [7](#0-6) 

For the termination path specifically: `terminate_vesting` sets `VestingInformation::Terminating`; `termination_prepare_to_withdraw` (foundation-only) checks `assert_staking_pool_is_idle` then sets `TransactionStatus::Busy` and `TerminationStatus::UnstakingInProgress`, dispatching `ext_staking_pool::get_account_staked_balance` to the selected pool. [8](#0-7) 
If that pool account has been deleted, the `#[callback]` handler `on_get_account_staked_balance_to_unstake` panics on deserialization instead of resetting status, so `TerminationStatus` is stuck at `UnstakingInProgress` forever, `termination_prepare_to_withdraw` can never be re-invoked (`assert_staking_pool_is_idle` always fails), `termination_withdraw` requires `Some(TerminationStatus::ReadyToWithdraw)` which is never reached, and `assert_no_termination` blocks every owner operation for good. [9](#0-8) [10](#0-9) 

The pool becoming unreachable does not require compromising anything privileged: an attacker can operate their own staking-pool contract (a legitimate open action per the rules), get it selected by any lockup owner via `select_staking_pool` (subject only to `is_whitelisted` check against the whitelist contract, which is a separate, publicly-inspectable list — the attacker's pool merely needs to have been whitelisted at some point, e.g. before being repurposed/self-destructed), and then delete that account (`Promise::new(pool).delete_account(beneficiary)` — any account holder can delete their own account) after the lockup has staked/deposited and/or after the foundation begins termination. From then on every `get_account_*_balance` cross-contract call to that now-nonexistent account fails, permanently freezing the lockup's `Busy`/`Terminating` state and all its NEAR.

## Assessment

This matches the described exploit precisely: `vesting_information` left in `Terminating` (via `terminate_vesting`, possibly with some `termination_withdrawn_tokens` already having been paid out by prior `on_withdraw_unvested_amount` cycles before the pool became unreachable), the pool disappears, and no code path exists to reset `TransactionStatus` or `TerminationStatus` once a `#[callback]`-based query permanently fails — none of `assert_owner`, `assert_called_by_foundation`, `assert_self()`, `is_promise_success()`, `assert_no_termination`, or `assert_staking_pool_is_idle` prevents this, because the flaw is the asymmetric handling between `is_promise_success()`-based callbacks (safe) and `#[callback]`-based callbacks (unsafe on promise failure) — a code-level gap those guards were never designed to close.

### Title
Deleted staking pool permanently sticks `TransactionStatus`/`TerminationStatus` via `#[callback]` panic, freezing lockup funds - (File: `lockup/src/owner_callbacks.rs`, `lockup/src/foundation_callbacks.rs`)

### Summary
`refresh_staking_pool_balance`, `withdraw_all_from_staking_pool`, and the foundation's `termination_prepare_to_withdraw` all set `staking_information.status = Busy` (or a termination sub-status) in one committed transaction, then dispatch a cross-contract query to the selected staking pool whose response is consumed via a `#[callback]` parameter in the follow-up handler. If the staking pool account no longer exists, the query's promise fails and the `#[callback]` handler panics before running any user code, so the `Busy`/in-progress status set earlier is never reverted to `Idle`/`ReadyToWithdraw`. Because `assert_staking_pool_is_idle` and `assert_no_staking_or_idle` gate every owner staking method, `unselect_staking_pool`, and `transfer`, and `assert_no_termination` gates everything else while `vesting_information` is `Terminating`, the lockup becomes permanently unusable and its NEAR balance permanently frozen.

### Finding Description
Broken binding: `status_after_promise_settles == Idle` (or `TerminationStatus::ReadyToWithdraw`) for every dispatched staking-pool query, regardless of promise outcome. This holds for `on_staking_pool_deposit`, `on_staking_pool_withdraw`, `on_staking_pool_stake`, `on_staking_pool_unstake`, `on_staking_pool_unstake_all`, `on_staking_pool_deposit_and_stake` (all use `is_promise_success()`, no `#[callback]`, so they always run and reset status) — [5](#0-4)  — but is violated by `on_get_account_total_balance`, `on_get_account_unstaked_balance_to_withdraw_by_owner`, `on_get_account_staked_balance_to_unstake`, and `on_get_account_unstaked_balance_to_withdraw`, which take a `#[callback]` value and therefore never execute their body (including the `set_staking_pool_status(Idle)` / `set_termination_status(...)` calls) when the antecedent promise fails [11](#0-10) [3](#0-2) .

Root cause: `refresh_staking_pool_balance` (`lockup/src/owner.rs:176-209`), `withdraw_all_from_staking_pool` (`lockup/src/owner.rs:259-294`), and `termination_prepare_to_withdraw` (`lockup/src/foundation.rs:58-127`) each set `Busy`/in-progress status in their own transaction *before* the async query executes, so that state is committed independently of whether the follow-up `#[callback]` handler ever runs.

Attacker's exact action: deploy and operate a staking pool contract, have it selected by a lockup owner (`select_staking_pool`), then delete that pool account (`delete_account`) — any account controller may delete their own account. Subsequently any `refresh_staking_pool_balance`, `withdraw_all_from_staking_pool`, or foundation `termination_prepare_to_withdraw` call against that lockup permanently wedges `TransactionStatus`/`TerminationStatus`. Existing guards (`assert_owner`, `assert_called_by_foundation`, `assert_self()`, `is_promise_success()`, `assert_no_termination`, `assert_staking_pool_is_idle`) never fire to unstick the status because there is no code path that resets it once a `#[callback]`-based promise fails.

### Impact Explanation
All NEAR held by the affected lockup contract (the owner's vested/unvested balance, plus any amount already staked with the now-deleted pool) becomes permanently unrecoverable: `transfer`, `unselect_staking_pool`, and all other staking methods panic forever via `assert_no_staking_or_idle`/`assert_staking_pool_is_idle`, and if triggered mid-termination, `termination_withdraw` can never reach `TerminationStatus::ReadyToWithdraw` so the foundation cannot complete recovering the unvested remainder either. This is Critical — permanent freezing of user funds, and it is repeatable against any lockup that selects a pool later deleted by its operator.

### Likelihood Explanation
Requires only: (1) a lockup owner selects a staking pool that is or becomes attacker-controlled/deletable (owners routinely pick pools not permanently vetted beyond whitelist status at selection time), and (2) that pool account is deleted. No privileged role, multisig, or foundation compromise is needed to cause the freeze — an attacker running a whitelisted pool can simply delete it after attracting deposits. Cost to the attacker is minimal (deploy + self-destruct their own account); the affected lockup owner (and, in the termination scenario, the foundation) bear the loss.

### Recommendation
Replace all `#[callback]` parameters in `on_get_account_total_balance`, `on_get_account_unstaked_balance_to_withdraw_by_owner`, `on_get_account_staked_balance_to_unstake`, and `on_get_account_unstaked_balance_to_withdraw` with `#[callback_result]` (`Result<T, PromiseError>` / manual `is_promise_success()` + `env::promise_result` checks), so that a failed antecedent promise still runs the handler body and resets `TransactionStatus`/`TerminationStatus` to `Idle`/an appropriate fallback rather than panicking and leaving the status stuck.

### Proof of Concept
```
#[test]
fn test_deleted_pool_permanently_bricks_status() {
    // 1. testing_env! as owner; contract.select_staking_pool("pool"); resolve on_whitelist_is_whitelisted(true, "pool").
    // 2. contract.deposit_and_stake(amount) -> on_staking_pool_deposit_and_stake(Success) to establish deposit_amount > 0.
    // 3. contract.refresh_staking_pool_balance() as owner -> commits staking_information.status = Busy.
    // 4. Simulate deleted pool: invoke on_get_account_total_balance via testing_env_with_promise_results(
    //      context, PromiseResult::Failed) -- assert this call panics (near-sdk #[callback] behavior)
    //      and that the panic unwinds without persisting state changes.
    // 5. Re-read contract.staking_information: assert status == Busy (never reset to Idle).
    // 6. Assert contract.assert_staking_pool_is_idle() panics.
    // 7. Assert contract.transfer(amount, receiver) panics via assert_no_staking_or_idle.
    // 8. Assert contract.unselect_staking_pool() panics via assert_staking_pool_is_idle.
    // => equality status_after_promise_settles == Idle is FALSE; funds are permanently unreachable.
}
```
Equivalent steps apply substituting `termination_prepare_to_withdraw`/`on_get_account_staked_balance_to_unstake` to demonstrate the mid-termination variant with `vesting_information == Terminating` and non-zero `termination_withdrawn_tokens` after a partial `on_withdraw_unvested_amount` success prior to the pool's deletion.

### Citations

**File:** lockup/src/owner_callbacks.rs (L27-62)
```rust
    /// Called after a deposit amount was transferred out of this account to the staking pool.
    /// This method needs to update staking pool status.
    pub fn on_staking_pool_deposit(&mut self, amount: WrappedBalance) -> bool {
        assert_self();

        let deposit_succeeded = is_promise_success();
        self.set_staking_pool_status(TransactionStatus::Idle);

        if deposit_succeeded {
            self.staking_information.as_mut().unwrap().deposit_amount.0 += amount.0;
            env::log(
                format!(
                    "The deposit of {} to @{} succeeded",
                    amount.0,
                    self.staking_information
                        .as_ref()
                        .unwrap()
                        .staking_pool_account_id
                )
                .as_bytes(),
            );
        } else {
            env::log(
                format!(
                    "The deposit of {} to @{} has failed",
                    amount.0,
                    self.staking_information
                        .as_ref()
                        .unwrap()
                        .staking_pool_account_id
                )
                .as_bytes(),
            );
        }
        deposit_succeeded
    }
```

**File:** lockup/src/owner_callbacks.rs (L280-339)
```rust
    /// Called after the request to get the current total balance from the staking pool.
    pub fn on_get_account_total_balance(&mut self, #[callback] total_balance: WrappedBalance) {
        assert_self();
        self.set_staking_pool_status(TransactionStatus::Idle);

        env::log(
            format!(
                "The current total balance on the staking pool is {}",
                total_balance.0
            )
            .as_bytes(),
        );

        self.staking_information.as_mut().unwrap().deposit_amount = total_balance;
    }

    /// Called after the request to get the current unstaked balance to withdraw everything by th
    /// owner.
    pub fn on_get_account_unstaked_balance_to_withdraw_by_owner(
        &mut self,
        #[callback] unstaked_balance: WrappedBalance,
    ) -> PromiseOrValue<bool> {
        assert_self();
        if unstaked_balance.0 > 0 {
            // Need to withdraw
            env::log(
                format!(
                    "Withdrawing {} from the staking pool @{}",
                    unstaked_balance.0,
                    self.staking_information
                        .as_ref()
                        .unwrap()
                        .staking_pool_account_id
                )
                .as_bytes(),
            );

            ext_staking_pool::withdraw(
                unstaked_balance,
                &self
                    .staking_information
                    .as_ref()
                    .unwrap()
                    .staking_pool_account_id,
                NO_DEPOSIT,
                gas::staking_pool::WITHDRAW,
            )
            .then(ext_self_owner::on_staking_pool_withdraw(
                unstaked_balance,
                &env::current_account_id(),
                NO_DEPOSIT,
                gas::owner_callbacks::ON_STAKING_POOL_WITHDRAW,
            ))
            .into()
        } else {
            env::log(b"No unstaked balance on the staking pool to withdraw");
            self.set_staking_pool_status(TransactionStatus::Idle);
            PromiseOrValue::Value(true)
        }
    }
```

**File:** lockup/src/foundation_callbacks.rs (L7-53)
```rust
    /// Called after the request to get the current staked balance to unstake everything for vesting
    /// schedule termination.
    pub fn on_get_account_staked_balance_to_unstake(
        &mut self,
        #[callback] staked_balance: WrappedBalance,
    ) -> PromiseOrValue<bool> {
        assert_self();
        if staked_balance.0 > 0 {
            // Need to unstake
            env::log(
                format!(
                    "Termination Step: Unstaking {} from the staking pool @{}",
                    staked_balance.0,
                    self.staking_information
                        .as_ref()
                        .unwrap()
                        .staking_pool_account_id
                )
                .as_bytes(),
            );

            ext_staking_pool::unstake(
                staked_balance,
                &self
                    .staking_information
                    .as_ref()
                    .unwrap()
                    .staking_pool_account_id,
                NO_DEPOSIT,
                gas::staking_pool::UNSTAKE,
            )
            .then(
                ext_self_foundation::on_staking_pool_unstake_for_termination(
                    staked_balance,
                    &env::current_account_id(),
                    NO_DEPOSIT,
                    gas::foundation_callbacks::ON_STAKING_POOL_UNSTAKE_FOR_TERMINATION,
                ),
            )
            .into()
        } else {
            env::log(b"Termination Step: Nothing to unstake. Moving to the next status.");
            self.set_staking_pool_status(TransactionStatus::Idle);
            self.set_termination_status(TerminationStatus::EverythingUnstaked);
            PromiseOrValue::Value(true)
        }
    }
```

**File:** lockup/src/foundation_callbacks.rs (L95-139)
```rust
    pub fn on_get_account_unstaked_balance_to_withdraw(
        &mut self,
        #[callback] unstaked_balance: WrappedBalance,
    ) -> PromiseOrValue<bool> {
        assert_self();
        if unstaked_balance.0 > 0 {
            // Need to withdraw
            env::log(
                format!(
                    "Termination Step: Withdrawing {} from the staking pool @{}",
                    unstaked_balance.0,
                    self.staking_information
                        .as_ref()
                        .unwrap()
                        .staking_pool_account_id
                )
                .as_bytes(),
            );

            ext_staking_pool::withdraw(
                unstaked_balance,
                &self
                    .staking_information
                    .as_ref()
                    .unwrap()
                    .staking_pool_account_id,
                NO_DEPOSIT,
                gas::staking_pool::WITHDRAW,
            )
            .then(
                ext_self_foundation::on_staking_pool_withdraw_for_termination(
                    unstaked_balance,
                    &env::current_account_id(),
                    NO_DEPOSIT,
                    gas::foundation_callbacks::ON_STAKING_POOL_WITHDRAW_FOR_TERMINATION,
                ),
            )
            .into()
        } else {
            env::log(b"Termination Step: Nothing to withdraw from the staking pool. Ready to withdraw from the account.");
            self.set_staking_pool_status(TransactionStatus::Idle);
            self.set_termination_status(TerminationStatus::ReadyToWithdraw);
            PromiseOrValue::Value(true)
        }
    }
```

**File:** lockup/src/internal.rs (L62-66)
```rust
    pub fn assert_no_termination(&self) {
        if let VestingInformation::Terminating(_) = &self.vesting_information {
            env::panic(b"All operations are blocked until vesting termination is completed");
        }
    }
```

**File:** lockup/src/internal.rs (L79-101)
```rust
    pub fn assert_no_staking_or_idle(&self) {
        if let Some(staking_information) = &self.staking_information {
            match staking_information.status {
                TransactionStatus::Idle => (),
                TransactionStatus::Busy => {
                    env::panic(b"Contract is currently busy with another operation")
                }
            };
        }
    }

    pub fn assert_staking_pool_is_idle(&self) {
        assert!(
            self.staking_information.is_some(),
            "Staking pool is not selected"
        );
        match self.staking_information.as_ref().unwrap().status {
            TransactionStatus::Idle => (),
            TransactionStatus::Busy => {
                env::panic(b"Contract is currently busy with another operation")
            }
        };
    }
```

**File:** lockup/src/owner.rs (L467-487)
```rust
    pub fn transfer(&mut self, amount: WrappedBalance, receiver_id: AccountId) -> Promise {
        self.assert_owner();
        assert!(amount.0 > 0, "Amount should be positive");
        assert!(
            env::is_valid_account_id(receiver_id.as_bytes()),
            "The receiver account ID is invalid"
        );
        self.assert_transfers_enabled();
        self.assert_no_staking_or_idle();
        self.assert_no_termination();
        assert!(
            self.get_liquid_owners_balance().0 >= amount.0,
            "The available liquid balance {} is smaller than the requested transfer amount {}",
            self.get_liquid_owners_balance().0,
            amount.0,
        );

        env::log(format!("Transferring {} to account @{}", amount.0, receiver_id).as_bytes());

        Promise::new(receiver_id).transfer(amount.0)
    }
```

**File:** lockup/src/foundation.rs (L58-99)
```rust
    pub fn termination_prepare_to_withdraw(&mut self) -> Promise {
        self.assert_called_by_foundation();
        self.assert_staking_pool_is_idle();

        let status = self.get_termination_status();

        match status {
            None => {
                env::panic(b"There is no termination in progress");
            }
            Some(TerminationStatus::UnstakingInProgress)
            | Some(TerminationStatus::WithdrawingFromStakingPoolInProgress)
            | Some(TerminationStatus::WithdrawingFromAccountInProgress) => {
                env::panic(b"Another transaction is already in progress.");
            }
            Some(TerminationStatus::ReadyToWithdraw) => {
                env::panic(b"The account is ready to withdraw unvested balance.")
            }
            Some(TerminationStatus::VestingTerminatedWithDeficit) => {
                // Need to unstake
                self.set_termination_status(TerminationStatus::UnstakingInProgress);
                self.set_staking_pool_status(TransactionStatus::Busy);
                env::log(b"Termination Step: Going to unstake everything from the staking pool");

                ext_staking_pool::get_account_staked_balance(
                    env::current_account_id(),
                    &self
                        .staking_information
                        .as_ref()
                        .unwrap()
                        .staking_pool_account_id,
                    NO_DEPOSIT,
                    gas::staking_pool::GET_ACCOUNT_STAKED_BALANCE,
                )
                .then(
                    ext_self_foundation::on_get_account_staked_balance_to_unstake(
                        &env::current_account_id(),
                        NO_DEPOSIT,
                        gas::foundation_callbacks::ON_GET_ACCOUNT_STAKED_BALANCE_TO_UNSTAKE,
                    ),
                )
            }
```

**File:** lockup/src/foundation.rs (L134-153)
```rust
    pub fn termination_withdraw(&mut self, receiver_id: AccountId) -> Promise {
        self.assert_called_by_foundation();
        assert!(
            env::is_valid_account_id(receiver_id.as_bytes()),
            "The receiver account ID is invalid"
        );
        assert_eq!(
            self.get_termination_status(),
            Some(TerminationStatus::ReadyToWithdraw),
            "Termination status is not ready to withdraw"
        );

        let amount = std::cmp::min(
            self.get_terminated_unvested_balance().0,
            self.get_account_balance().0,
        );
        assert!(
            amount > 0,
            "The account doesn't have enough liquid balance to withdraw any amount"
        );
```
