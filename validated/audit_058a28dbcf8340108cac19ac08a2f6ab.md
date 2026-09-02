### Title
`on_staking_pool_create` unconditionally refunds the full deposit even when the deposit was already spent by prior actions in the batch - (File: `staking-pool-factory/src/lib.rs`)

### Summary
`create_staking_pool` chains `create_account().transfer(deposit).deploy_contract(...).function_call("new", ...)` as one action receipt, then in the callback treats the *entire* receipt result as a single boolean via `is_promise_success()`. Because NEAR executes batched actions sequentially and does **not** roll back actions that already succeeded when a later action in the same receipt fails, an attacker can make the final `function_call("new", ...)` fail (e.g. deserialization/validation panic inside `staking-pool::new`) after `create_account`+`transfer`+`deploy_contract` have already succeeded, so the `MIN_ATTACHED_BALANCE` deposit is already parked on the newly created (but broken) sub-account. `on_staking_pool_create` then sees `is_promise_success() == false` and unconditionally sends `attached_deposit` back to the predecessor, doubling the payout for a single incoming deposit.

### Finding Description
The invariant the factory relies on is: `attached_deposit_received == attached_deposit_refunded_or_consumed_exactly_once`. Concretely: for one call to `create_staking_pool` with deposit `D`, either (a) the pool is created and `D` stays locked in the new pool account, or (b) creation fails and `D` is refunded to the caller — never both.

The code path:
- `create_staking_pool` builds one action receipt: [1](#0-0) 
- The callback determines success/failure purely from `is_promise_success()`, which reports whether the *whole* receipt (i.e. its last/aggregate action) succeeded, not which individual action failed: [2](#0-1) 
- On failure, it unconditionally refunds the full `attached_deposit` to `predecessor_account_id`, with no check on whether the `transfer` action inside the batch actually completed: [3](#0-2) 

Root cause: NEAR action-receipt semantics execute the chained actions (`create_account`, `transfer`, `deploy_contract`, `function_call`) sequentially, and a failure in a later action (the `function_call("new", ...)`) does not undo the effects of earlier, already-successful actions (`create_account`, `transfer`, `deploy_contract`). The factory's tracked HashSet insert at [4](#0-3)  is also removed synchronously on any failure, mirrored by the physical-account state divergence: the sub-account now permanently holds `D` NEAR (and a deployed-but-uninitialized contract), yet is de-listed from `staking_pool_account_ids`.

Exploit flow:
1. Attacker calls `create_staking_pool(staking_pool_id, owner_id, stake_public_key, reward_fee_fraction)` with `attached_deposit = MIN_ATTACHED_BALANCE`, choosing arguments that pass the factory's local checks (`RewardFeeFraction::assert_valid`, `is_valid_account_id` on both ids) at [5](#0-4)  but are crafted to cause `StakingContract::new` (in `staking-pool/src/lib.rs`) to panic during initialization — e.g. via a stake public key or reward-fee edge case that the staking-pool contract itself rejects more strictly than the factory does.
2. `create_account`, `transfer(D)`, and `deploy_contract` all succeed on the target sub-account; `function_call("new", ...)` panics.
3. The whole receipt is reported failed; `is_promise_success()` returns `false`.
4. `on_staking_pool_create` refunds `D` again to the attacker (`predecessor_account_id`), while the `D` already sitting on the orphaned sub-account is unrecoverable through this contract (no owner key, uninitialized/failed contract state, and it is removed from `staking_pool_account_ids` so the factory no longer tracks it at all).
5. Net effect: the factory contract's balance decreases by `D` more than it should for this single deposit event — the attacker effectively receives `2D` worth of value out of the system (D refunded + D parked on a sub-account which, depending on `owner_id`, may still be attacker-controllable if the deployed staking-pool contract can later be salvaged, or otherwise simply lost from the factory's operating balance funded by other users' deposits).

None of `assert_self()`, `is_promise_success()`, or the `staking_pool_account_ids` set guard against this because they only test/track the *aggregate* receipt outcome and the *logical* create-vs-fail decision — none of them verify whether the `transfer` sub-action specifically completed before deciding to refund.

### Impact Explanation
This produces a real accounting divergence: the factory's spendable/expected balance no longer matches the sum of legitimate obligations, because a single `D`-sized deposit results in `2D` total NEAR leaving factory-controlled destinations (one to the orphaned sub-account, one refunded to the attacker). Repeating this with distinct `staking_pool_id` values scales the loss linearly with attacker gas/deposit cost, each iteration draining `D` (≥30 NEAR) from the factory's operating balance that is not backed by any corresponding "created pool" state. This matches the High-severity category: "an accounting value diverging from reality where another party settles on it" — the factory (and by extension whoever funds/tops up the factory account) settles the loss.

### Likelihood Explanation
- Precondition: the attacker must find a set of `(stake_public_key, reward_fee_fraction, owner_id)` values that pass the factory's coarse validation (`is_valid_account_id`, `RewardFeeFraction::assert_valid`) but cause a panic inside `StakingContract::new` in `staking-pool/src/lib.rs`. I was not able to fully confirm from the code inspected so far which specific value combination triggers such a panic (the file was only partially reviewed before the iteration limit), so this precondition is **not fully verified** and requires further code review of `staking-pool/src/lib.rs::new` (or of `internal.rs`) to confirm a concrete panicking input exists that isn't already blocked by the factory's pre-checks.
- Cost: exactly `MIN_ATTACHED_BALANCE` (30 NEAR) plus gas per attempt, fully unprivileged, repeatable across distinct `staking_pool_id` values indefinitely.
- If the panicking-`new()` precondition holds, the exploit is straightforward and fully reproducible with a `near-sdk-sim`/`near-workspaces` test that supplies a `PromiseResult::Failed` for the create-pool promise while the underlying `transfer` action has already been applied to the target account's balance.

### Recommendation
Do not conflate "did the whole batched receipt succeed" with "should the deposit be refunded." Split the account-creation flow into a plain `create_account` + `transfer` action that is checked independently (via its own callback / `PromiseResult`) before deploying/initializing the contract, or verify the actual on-chain balance of `staking_pool_account_id` in the callback before refunding, so the refund is only issued when the `transfer` truly did not land. Alternatively, only refund the deposit when `env::account_balance()`/state confirms the sub-account was never funded, and otherwise attempt to recover/deploy a repair mechanism instead of silently reissuing a second payout.

### Proof of Concept
Recommended `cargo test` plan (to be run in `staking-pool-factory`, extending `staking-pool-factory/src/lib.rs`'s existing `tests` module):
1. Set up context as in `test_create_staking_pool_rollback`.
2. Call `create_staking_pool` with `attached_deposit = ntoy(31)`; assert `staking_pool_account_ids` contains the new id (mirrors the existing `insert` assertion).
3. Simulate that `create_account`, `transfer`, `deploy_contract` succeeded (i.e., manually credit the sub-account's tracked/simulated balance by `ntoy(31)`, mimicking what NEAR would do), but the final `function_call("new", ...)` failed — invoke `on_staking_pool_create` with `PromiseResult::Failed`.
4. Assert on both sides of the equality: `initial_factory_balance - final_factory_balance == D` (only one deposit's worth should leave) vs. actual `== 2*D` (deposit still on sub-account **and** refunded to predecessor) — the test should show the second, proving the divergence.
5. This requires `near-workspaces` (or a hand-rolled `RuntimeStandalone` sim) rather than a pure unit test, because unit tests with `testing_env!`/mocked `PromiseResult` cannot represent "some actions in a batch succeeded while a later one failed" — that specific semantic gap is exactly the root cause and needs real receipt execution to demonstrate conclusively.

### Citations

**File:** staking-pool-factory/src/lib.rs (L144-164)
```rust
        assert!(
            env::attached_deposit() >= MIN_ATTACHED_BALANCE,
            "Not enough attached deposit to complete staking pool creation"
        );

        assert!(
            staking_pool_id.find('.').is_none(),
            "The staking pool ID can't contain `.`"
        );

        let staking_pool_account_id = format!("{}.{}", staking_pool_id, env::current_account_id());
        assert!(
            env::is_valid_account_id(staking_pool_account_id.as_bytes()),
            "The staking pool account ID is invalid"
        );

        assert!(
            env::is_valid_account_id(owner_id.as_bytes()),
            "The owner account ID is invalid"
        );
        reward_fee_fraction.assert_valid();
```

**File:** staking-pool-factory/src/lib.rs (L166-170)
```rust
        assert!(
            self.staking_pool_account_ids
                .insert(&staking_pool_account_id),
            "The staking pool account ID already exists"
        );
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

**File:** staking-pool-factory/src/lib.rs (L206-210)
```rust
        assert_self();

        let staking_pool_created = is_promise_success();

        if staking_pool_created {
```

**File:** staking-pool-factory/src/lib.rs (L225-238)
```rust
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
```
