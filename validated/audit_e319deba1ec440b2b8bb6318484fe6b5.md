### Title
Permanent freeze of lockup funds via `#[callback]`-panic in `on_get_account_total_balance` / `on_get_account_unstaked_balance_to_withdraw_by_owner` before `set_staking_pool_status(Idle)` runs - (File: lockup/src/owner_callbacks.rs)

### Summary
`refresh_staking_pool_balance` and `withdraw_all_from_staking_pool` set `TransactionStatus::Busy` and then call the staking pool's `get_account_total_balance` / `get_account_unstaked_balance`, whose results are consumed with a bare `#[callback]` argument (not `is_promise_success()` and not `#[callback_result]`). If that cross-contract call fails (the pool panics, runs out of gas, or is otherwise unresponsive), the near-sdk-generated wrapper for the `#[callback]` parameter panics ("Callback computation failed") *before* the callback body (and thus `assert_self(); set_staking_pool_status(Idle)`) ever runs. `staking_information.status` is left at `Busy` forever, and every method guarded by `assert_staking_pool_is_idle` / `assert_no_staking_or_idle` becomes permanently unusable.

### Finding Description
Binding claimed: `staking_information.status == TransactionStatus::Idle` after every owner-initiated staking-pool operation completes, whether the pool call succeeds or fails.

Contrast the two callback styles in `lockup/src/owner_callbacks.rs`:
- `on_staking_pool_stake`, `on_staking_pool_deposit`, `on_staking_pool_withdraw`, `on_staking_pool_unstake`, `on_staking_pool_unstake_all` all call `is_promise_success()` first and unconditionally call `self.set_staking_pool_status(TransactionStatus::Idle)` next [1](#0-0) . `is_promise_success()` only inspects the promise-result discriminant, so it never panics regardless of success/failure — these paths are safe.
- `on_get_account_total_balance`, reached from `refresh_staking_pool_balance`, instead takes a mandatory `#[callback] total_balance: WrappedBalance` parameter and only *then* calls `set_staking_pool_status(TransactionStatus::Idle)` [2](#0-1) . Likewise `on_get_account_unstaked_balance_to_withdraw_by_owner`, reached from `withdraw_all_from_staking_pool`, takes `#[callback] unstaked_balance: WrappedBalance` and only sets `Idle` in the branch after successfully reading that value [3](#0-2) .

In near-sdk 3.1.0 (the version pinned by `lockup/Cargo.toml`, `near-sdk = "3.1.0"`), a plain `#[callback]` parameter is extracted by generated wrapper code that reads `env::promise_result(0)` and panics immediately if the result is not `PromiseResult::Successful` — this happens *before* the annotated method body executes, i.e. before `assert_self()` and before `set_staking_pool_status(Idle)` can run. There is no `#[callback_result]`/`Result`-wrapped variant used here to catch a failed promise gracefully.

Call path:
1. Owner calls `refresh_staking_pool_balance()` (or `withdraw_all_from_staking_pool()`), which asserts idle, sets `Busy`, and schedules `ext_staking_pool::get_account_total_balance(...).then(ext_self_owner::on_get_account_total_balance(...))` [4](#0-3) .
2. The staking pool account (any account named as the selected pool — an unprivileged attacker is explicitly allowed to "deploy contracts they control and name as a ... pool" per the rules) fails to return a successful result for `get_account_total_balance`/`get_account_unstaked_balance` — e.g. by panicking, exhausting attached gas, or crashing before producing a value.
3. The scheduled callback receipt executes with `PromiseResult::Failed`. The `#[callback]` wrapper for `on_get_account_total_balance` panics before ever reaching `set_staking_pool_status(Idle)`.
4. `staking_information.status` remains `Busy` permanently. `assert_staking_pool_is_idle` (`lockup/src/internal.rs`, lines 90-101) and `assert_no_staking_or_idle` (lines 79-88) then reject every subsequent call to `deposit_to_staking_pool`, `deposit_and_stake`, `withdraw_from_staking_pool`, `stake`, `unstake`, `unstake_all`, `refresh_staking_pool_balance`, `unselect_staking_pool`, and `transfer` [5](#0-4) .

Existing guards do not prevent this: `assert_self()` only checks the caller is the contract itself and runs *after* the panic point; `is_promise_success()` is not used in these two callbacks at all, unlike the sibling callbacks that are immune to this exact issue.

### Impact Explanation
The owner's NEAR held in the lockup becomes permanently frozen — no further staking, withdrawal, unselection, or transfer operation can ever succeed once this state is reached, since every gating check on `staking_information.status` will panic forever. This matches the Critical category "funds permanently frozen." The blast radius is scoped to lockups that have selected a staking pool which experiences (or is deliberately made to produce, if attacker-controlled) a failed response to `get_account_total_balance` or `get_account_unstaked_balance` while the lockup is in the corresponding `Busy` window; it is repeatable in the sense that any single failed response is sufficient and irreversible (there is no recovery path back to `Idle`).

### Likelihood Explanation
Triggering requires: (a) a staking pool already selected via `select_staking_pool` (which itself requires the pool to pass the whitelist check), and (b) the owner calling `refresh_staking_pool_balance()` or `withdraw_all_from_staking_pool()` while that pool's `get_account_total_balance`/`get_account_unstaked_balance` call fails to resolve successfully. If the selected pool is attacker-controlled (deployed and named by the attacker as allowed under the rules), the attacker can deterministically make its own view method panic or exhaust gas on every invocation, guaranteeing the freeze the very first time the owner calls either method. No deposit or special balance is required from the attacker beyond deploying and naming the pool contract; the cost is the deployment of a simple malicious contract. The main precondition softening this is that the pool must have been whitelisted/selected by the owner in the first place, which depends on the owner's (or whitelist operator's) choice, not an unprivileged bypass — but once selected, the freeze is deterministic and requires no further attacker privilege.

### Recommendation
Replace the bare `#[callback]` parameters in `on_get_account_total_balance` and `on_get_account_unstaked_balance_to_withdraw_by_owner` with a failure-tolerant pattern (e.g. `#[callback_result] total_balance: Result<WrappedBalance, PromiseError>`, or check `is_promise_success()` first) so that `set_staking_pool_status(TransactionStatus::Idle)` is guaranteed to execute on both success and failure of the underlying promise, consistent with the other `on_staking_pool_*` callbacks in `lockup/src/owner_callbacks.rs`.

### Proof of Concept
```rust
// lockup/src/tests (conceptual, not present in current tests dir)
#[test]
fn callback_panic_leaves_status_busy_forever() {
    // 1. testing_env! as the lockup contract itself (assert_self()-compatible context).
    // 2. Construct a LockupContract with staking_information = Some(StakingInformation {
    //      status: TransactionStatus::Busy, ... }) to emulate the state right after
    //      refresh_staking_pool_balance() scheduled the promise.
    // 3. Simulate the callback receipt with a failed promise result for slot 0
    //    (testing_env! promise_results: vec![PromiseResult::Failed]).
    // 4. Call contract.on_get_account_total_balance(WrappedBalance(0)) through the
    //    generated #[callback] extraction path (or directly invoke the near-sdk-sim /
    //    near-workspaces cross-contract flow with a pool contract that panics in
    //    get_account_total_balance).
    // 5. Assert the call panics with "Callback computation failed" (or equivalent)
    //    BEFORE reaching set_staking_pool_status(Idle).
    // 6. Assert contract.staking_information.unwrap().status == TransactionStatus::Busy
    //    (binding violated: expected Idle, actual Busy).
    // 7. Assert that a subsequent contract.stake(1.into()) call panics with
    //    "Contract is currently busy with another operation", proving the permanent freeze.
}
```
Using `near-sdk-sim`/`near-workspaces`, an end-to-end version deploys a malicious pool contract whose `get_account_total_balance` method calls `env::panic` unconditionally, has the lockup owner call `refresh_staking_pool_balance()`, and observes that `status` never returns to `Idle` and all subsequent owner methods revert with "Contract is currently busy with another operation".

### Citations

**File:** lockup/src/owner_callbacks.rs (L149-153)
```rust
    pub fn on_staking_pool_stake(&mut self, amount: WrappedBalance) -> bool {
        assert_self();

        let stake_succeeded = is_promise_success();
        self.set_staking_pool_status(TransactionStatus::Idle);
```

**File:** lockup/src/owner_callbacks.rs (L281-294)
```rust
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
```

**File:** lockup/src/owner_callbacks.rs (L298-339)
```rust
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

**File:** lockup/src/owner.rs (L192-209)
```rust
        self.set_staking_pool_status(TransactionStatus::Busy);

        ext_staking_pool::get_account_total_balance(
            env::current_account_id(),
            &self
                .staking_information
                .as_ref()
                .unwrap()
                .staking_pool_account_id,
            NO_DEPOSIT,
            gas::staking_pool::GET_ACCOUNT_TOTAL_BALANCE,
        )
        .then(ext_self_owner::on_get_account_total_balance(
            &env::current_account_id(),
            NO_DEPOSIT,
            gas::owner_callbacks::ON_GET_ACCOUNT_TOTAL_BALANCE,
        ))
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
