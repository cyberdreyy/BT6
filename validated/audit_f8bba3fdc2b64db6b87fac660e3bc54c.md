### Title
Malicious staking pool can exfiltrate NEAR while returning a failed `withdraw` promise, leaving stale `deposit_amount` and inflating the foundation's termination claim - (File: lockup/src/foundation_callbacks.rs)

### Summary
`on_staking_pool_withdraw_for_termination` only inspects `is_promise_success()` of the single promise returned by the staking pool's `withdraw` call, and only decrements `staking_information.deposit_amount` on success. A malicious staking-pool contract can perform an independent NEAR transfer to a third account while returning a separate, deliberately failing promise as the call's result, so the lockup rolls the termination status back to `EverythingUnstaked` without ever reducing `deposit_amount`, even though the pool no longer holds those funds.

### Finding Description
Binding claimed to hold: `get_known_deposited_balance()` (i.e. `staking_information.deposit_amount`) == NEAR actually held/owed by the selected `staking_pool_account_id` for this lockup account.

Code path: `termination_prepare_to_withdraw` (lockup/src/foundation.rs) drives the state machine `EverythingUnstaked -> WithdrawingFromStakingPoolInProgress`, calling `ext_staking_pool::get_account_unstaked_balance` then `on_get_account_unstaked_balance_to_withdraw`, which issues `ext_staking_pool::withdraw(unstaked_balance, &staking_pool_account_id, ...)` and chains `.then(on_staking_pool_withdraw_for_termination(amount))`. [1](#0-0) 

In `on_staking_pool_withdraw_for_termination`, `withdraw_succeeded = is_promise_success()` is the only signal used to decide whether NEAR actually moved: [2](#0-1) 

On success, `deposit_amount` is decremented by `amount` (saturating). On failure, the code assumes nothing happened to the pool balance and simply reverts `TerminationStatus` to `EverythingUnstaked`, leaving `deposit_amount` untouched.

Root cause: `is_promise_success()` reflects only the outcome of the specific promise object returned by the attacker-controlled `withdraw` function call — not all actions that function performed. A malicious pool contract can, inside its own `withdraw` handler, schedule an independent `Promise::new(third_party).transfer(amount)` (which executes and moves real NEAR out of the pool account) and separately return/chain a different promise designed to fail (e.g., a call to a nonexistent method or one that panics). Since NEAR receipts scheduled as independent (non-chained) actions are not rolled back by the failure of a different, unrelated returned promise, the transfer to the attacker-chosen third party succeeds while the lockup's callback observes `is_promise_success() == false`.

Existing guards do not catch this: `assert_self()` only ensures the callback is invoked by the contract itself, and `is_promise_success()` is precisely the value being gamed — there is no independent verification (e.g., re-querying `get_account_unstaked_balance`/`get_account_total_balance`) before trusting the "failure" branch's assumption that no funds moved.

Attacker requirement: the attacker's pool contract must already be the selected `staking_pool_account_id` for the victim lockup (via whitelist + owner selection) prior to foundation-initiated termination — consistent with the given threat model where an attacker can deploy and get whitelisted a pool contract.

Exploit flow:
1. Attacker deploys and gets whitelisted a malicious staking-pool contract; a lockup owner (attacker or unwitting party) selects it and deposits/stakes.
2. Foundation terminates vesting; termination proceeds to `WithdrawingFromStakingPoolInProgress`.
3. `ext_staking_pool::withdraw` is delivered to attacker's pool. The malicious `withdraw` implementation transfers the unstaked NEAR to an attacker-controlled account and returns a failing promise as its own result.
4. `on_staking_pool_withdraw_for_termination` sees `is_promise_success() == false`, sets status back to `EverythingUnstaked`, and does not touch `deposit_amount`.
5. `deposit_amount`/`get_known_deposited_balance()` still counts the NEAR that has already left the pool, so `get_terminated_unvested_balance_deficit()` and any subsequent `on_withdraw_unvested_amount` accounting is computed against funds no longer recoverable from the pool.

### Impact Explanation
NEAR that the pool actually owed to the lockup account has left the ecosystem for the attacker's benefit, while the lockup contract's internal ledger (`deposit_amount`, and derived values such as `get_terminated_unvested_balance_deficit()`) still counts it as recoverable. This is a claim-vs-reality mismatch: the foundation's termination process will believe more NEAR is available/owed from the pool than actually exists there, i.e. a solvency-overstatement for the unvested balance the foundation is entitled to recover. This matches the Critical category: "claims exceeding the NEAR actually held." The blast radius is scoped to lockup accounts that selected the attacker's pool and later undergo vesting termination; it is repeatable per victim lockup contract that uses the malicious pool.

### Likelihood Explanation
Requires: (1) attacker's contract is whitelisted and selected as the staking pool for a lockup with an active vesting schedule, (2) the vesting must later be terminated by the foundation and reach `EverythingUnstaked` -> `WithdrawingFromStakingPoolInProgress`. The attacker fully controls the pool contract's logic and can trivially implement the "transfer + independently fail" pattern; no special balances or races are needed on the attacker's side. The main precondition — foundation-initiated termination against a victim who staked with an attacker-controlled pool — is a plausible operational sequence for lockups with malicious/compromised pool selection, matching the scenario explicitly described in the question. Attacker cost is minimal (deploy a pool contract); feasibility depends on getting the pool selected before termination, which is outside the attacker's direct control but within the stated threat model.

### Recommendation
Do not treat `is_promise_success()` of the `withdraw` call as authoritative proof that no funds moved. On failure, re-query the pool's actual unstaked/total balance for this account (as done elsewhere via `get_account_unstaked_balance`/`get_account_total_balance`) and reconcile `deposit_amount` against that ground truth before reverting the termination status, rather than assuming a failed promise implies no state change on the counterparty contract.

### Proof of Concept
`near-workspaces` test plan:
1. Deploy a malicious staking-pool contract whose `withdraw(amount)` method: (a) issues `Promise::new(attacker_account).transfer(amount)` as an independent action, and (b) returns a distinct promise/action guaranteed to fail (e.g., a cross-contract call to a nonexistent method) so the top-level receipt reports failure.
2. Deploy the lockup contract with vesting; whitelist and select the malicious pool as `staking_pool_account_id`; deposit and stake funds.
3. Foundation terminates vesting and drives `termination_prepare_to_withdraw` through `UnstakingInProgress` -> `EverythingUnstaked` -> `WithdrawingFromStakingPoolInProgress`, triggering the `withdraw` cross-call to the malicious pool.
4. Assert: `attacker_account`'s balance increased by `amount` (funds left the pool) — binding LHS.
5. Assert: `lockup.get_known_deposited_balance()` (and `get_terminated_unvested_balance_deficit()`) still counts `amount` as recoverable, i.e. unchanged from before the failed withdraw — binding RHS.
6. Show the two sides diverge: recoverable-per-ledger `deposit_amount` > actual NEAR remaining in/owed by the pool, confirming the solvency-claim overstatement.

### Citations

**File:** lockup/src/foundation_callbacks.rs (L114-132)
```rust
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
```

**File:** lockup/src/foundation_callbacks.rs (L143-185)
```rust
    pub fn on_staking_pool_withdraw_for_termination(&mut self, amount: WrappedBalance) -> bool {
        assert_self();

        let withdraw_succeeded = is_promise_success();
        self.set_staking_pool_status(TransactionStatus::Idle);

        if withdraw_succeeded {
            self.set_termination_status(TerminationStatus::ReadyToWithdraw);
            {
                let staking_information = self.staking_information.as_mut().unwrap();
                // Due to staking rewards the deposit amount can become negative.
                staking_information.deposit_amount.0 = staking_information
                    .deposit_amount
                    .0
                    .saturating_sub(amount.0);
            }
            env::log(
                format!(
                    "Termination Step: The withdrawal of {} from @{} succeeded",
                    amount.0,
                    self.staking_information
                        .as_ref()
                        .unwrap()
                        .staking_pool_account_id
                )
                .as_bytes(),
            );
        } else {
            self.set_termination_status(TerminationStatus::EverythingUnstaked);
            env::log(
                format!(
                    "Termination Step: The withdrawal of {} from @{} failed",
                    amount.0,
                    self.staking_information
                        .as_ref()
                        .unwrap()
                        .staking_pool_account_id
                )
                .as_bytes(),
            );
        }
        withdraw_succeeded
    }
```
