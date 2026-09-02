### Title
Non-atomic batched receipt in `create_staking_pool` lets `new()` failure leave deposit at the created account while `on_staking_pool_create` refunds it again from the factory's own balance - (File: `staking-pool-factory/src/lib.rs`)

### Summary
`create_staking_pool` batches `create_account()`, `transfer(deposit)`, `deploy_contract()` and `function_call("new", ...)` onto a single `Promise`, then attaches `on_staking_pool_create` as a `.then()` callback. NEAR does not roll back earlier actions in a receipt when a later action in the same receipt fails, so if `new` fails after account creation and funding, the deposit stays on the newly created (broken) account while the callback still sees `is_promise_success() == false` and transfers a full second copy of `attached_deposit` out of the factory's own balance to the caller.

### Finding Description
The binding claimed by the invariant is:
`refund_amount + balance_left_on_created_account == attached_deposit`

The actual code path: [1](#0-0) 
batches four actions into one receipt and chains a callback. Because NEAR applies actions within a receipt sequentially and does not revert already-applied actions when a later one in the same receipt fails, a failure in the trailing `function_call(b"new", ...)` leaves the `CreateAccount`, `Transfer`, and `DeployContract` actions already committed - the new account exists, is funded with the full deposit, and has code deployed, but is left uninitialized (no state).

The callback then unconditionally treats the whole receipt outcome as one boolean: [2](#0-1) 
On `is_promise_success() == false`, it removes the account ID from the whitelist-candidate set and sends a *second, full* `attached_deposit.0` transfer to `predecessor_account_id` out of the factory contract's own account balance: [3](#0-2) 

So on any `new()` failure: the funded (but uninitialized/broken) account permanently retains `D` NEAR, and the factory transfers an additional `D` NEAR from its own reserves to the caller. Total NEAR paid out (`2D`) exceeds the NEAR the factory actually received for this call (`D`), draining the factory's own balance by `D` per occurrence. This is repeatable by any unprivileged caller who can reliably make `new()` fail after the batch's first three actions already committed (e.g., attaching exactly `MIN_ATTACHED_BALANCE` so the deployed contract's post-init storage staking requirement is not met, or any other panic path reachable inside the staking-pool's `new`).

None of the existing guards intercept this: `reward_fee_fraction.assert_valid()` and the `is_valid_account_id` checks in `create_staking_pool` run *before* the promise is dispatched and do not protect against failures inside the deployed contract's `new`; `assert_self()` in the callback only confirms the caller is the factory itself, and `is_promise_success()` only reports the aggregate receipt status - it gives no information about which of the batched actions actually succeeded, so the callback's refund logic wrongly assumes "receipt failed" implies "no state changed."

One caveat: the question's framing about "naming a whitelist contract the attacker deployed" does not apply to this specific target. In `staking-pool-factory`, `staking_pool_whitelist_account_id` is fixed at contract `#[init]` time (set once by whoever deploys the factory, e.g. via `scripts/deploy/deploy_staking_pool_factory.sh` pointing at the Foundation's own whitelist) [4](#0-3) , and the success path always calls `add_staking_pool` on that fixed `self.staking_pool_whitelist_account_id`, not any attacker-supplied value [5](#0-4) . The per-call attacker-choosable whitelist override exists in `lockup-factory::create` instead [6](#0-5) , not in `staking-pool-factory`. The real, demonstrable bug in this target is the double-payout/factory-balance-drain from the non-atomic batched receipt, not a hostile-whitelist routing issue.

### Impact Explanation
Each triggered failure drains `D` NEAR (at least `MIN_ATTACHED_BALANCE` = 30 NEAR) from the staking-pool-factory contract's own balance: the caller gets a full refund of their deposit while an equal amount remains permanently stuck at an uninitialized, unreachable staking-pool account (no owner key, no init state, no way to recover it back). This is repeatable across distinct `staking_pool_id` values by the same or different unprivileged attackers, each attempt costing only gas plus one deposit that is immediately refunded. This matches Critical severity: NEAR leaves the factory's contract account without authorization, and the loss is borne entirely by the factory deployer/protocol reserve, not the attacker.

### Likelihood Explanation
Preconditions: attacker needs no special privileges - only `MIN_ATTACHED_BALANCE` NEAR (refunded to them anyway) and the ability to make the batched `new()` call fail after the first three actions of the receipt commit. This is entirely plausible whenever the attached deposit is at or near the minimum and the deployed `staking_pool.wasm`'s storage-staking requirements after `new()` runs are not strictly guaranteed to be covered, or any other legitimate panic path inside `new` is reachable with attacker-chosen `owner_id`/`stake_public_key`/`reward_fee_fraction` combinations. The attack is cheap (near cost-free after refund) and trivially repeatable.

### Recommendation
Do not assume a failed receipt means no state changed. Verify the actual on-chain balance/state of the newly created account (e.g., via a follow-up query or by having `new()` itself perform a self-check and refund any excess back to the owner on success, and have the failure branch check the real account balance before refunding) rather than unconditionally re-transferring the full `attached_deposit` from the factory's own funds. Alternatively, structure account creation so the deposit is only transferred to the new account after `new()`'s successful callback confirmation, or use a strictly smaller amount when funding the account initially and top up only after success is confirmed.

### Proof of Concept
Using `near-sdk-sim` / `near-workspaces` (not the existing panic-only unit tests, which stub `PromiseResult::Failed` without checking real account balances):
1. Deploy `staking-pool-factory` with a whitelist account.
2. Craft a call to `create_staking_pool` with `attached_deposit == MIN_ATTACHED_BALANCE` and parameters chosen so `staking_pool::new` fails after `CreateAccount`/`Transfer`/`DeployContract` commit (e.g., force a real runtime failure in `new`, verifiable by inspecting the account's actual on-chain balance after the receipt executes with `PromiseResult::Failed`).
3. Assert, after the callback runs: `account_balance(created_pool_account_id) == D` (deposit retained) AND `predecessor_balance_delta == +D` (full refund received) - i.e., `refund + balance_left_on_created_account == 2 * D`, violating the claimed invariant of `== D`.
4. Confirm the factory's own account balance dropped by `D` relative to before the call, demonstrating the drain.

### Citations

**File:** staking-pool-factory/src/lib.rs (L104-115)
```rust
    #[init]
    pub fn new(staking_pool_whitelist_account_id: AccountId) -> Self {
        assert!(!env::state_exists(), "The contract is already initialized");
        assert!(
            env::is_valid_account_id(staking_pool_whitelist_account_id.as_bytes()),
            "The staking pool whitelist account ID is invalid"
        );
        Self {
            staking_pool_whitelist_account_id,
            staking_pool_account_ids: UnorderedSet::new(b"s".to_vec()),
        }
    }
```

**File:** staking-pool-factory/src/lib.rs (L172-195)
```rust
        Promise::new(staking_pool_account_id.clone())
            .create_account()
            .transfer(env::attached_deposit())
            .deploy_contract(include_bytes!("../../staking-pool/res/staking_pool.wasm").to_vec())
            .function_call(
                b"new".to_vec(),
                near_sdk::serde_json::to_vec(&StakingPoolArgs {
                    owner_id,
                    stake_public_key,
                    reward_fee_fraction,
                })
                .unwrap(),
                NO_DEPOSIT,
                gas::STAKING_POOL_NEW,
            )
            .then(ext_self::on_staking_pool_create(
                staking_pool_account_id,
                env::attached_deposit().into(),
                env::predecessor_account_id(),
                &env::current_account_id(),
                NO_DEPOSIT,
                gas::CALLBACK,
            ))
    }
```

**File:** staking-pool-factory/src/lib.rs (L200-239)
```rust
    pub fn on_staking_pool_create(
        &mut self,
        staking_pool_account_id: AccountId,
        attached_deposit: U128,
        predecessor_account_id: AccountId,
    ) -> PromiseOrValue<bool> {
        assert_self();

        let staking_pool_created = is_promise_success();

        if staking_pool_created {
            env::log(
                format!(
                    "The staking pool @{} was successfully created. Whitelisting...",
                    staking_pool_account_id
                )
                .as_bytes(),
            );
            ext_whitelist::add_staking_pool(
                staking_pool_account_id,
                &self.staking_pool_whitelist_account_id,
                NO_DEPOSIT,
                gas::WHITELIST_STAKING_POOL,
            )
            .into()
        } else {
            self.staking_pool_account_ids
                .remove(&staking_pool_account_id);
            env::log(
                format!(
                    "The staking pool @{} creation has failed. Returning attached deposit of {} to @{}",
                    staking_pool_account_id,
                    attached_deposit.0,
                    predecessor_account_id
                ).as_bytes()
            );
            Promise::new(predecessor_account_id).transfer(attached_deposit.0);
            PromiseOrValue::Value(false)
        }
    }
```

**File:** lockup-factory/src/lib.rs (L128-133)
```rust
        // Defaults to the whitelist account ID given on init call.
        let staking_pool_whitelist_account_id = if let Some(account_id) = whitelist_account_id {
            account_id.into()
        } else {
            self.whitelist_account_id.clone()
        };
```
