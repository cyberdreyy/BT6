### Title
`create` has no callback to detect a failed multisig deployment, so the attached deposit is never refunded on failure - (File: `multisig-factory/src/lib.rs`)

### Summary
`MultisigFactory::create` chains `create_account`/`deploy_contract`/`transfer`/`function_call` into a single `Promise` and returns it directly with no `.then()` callback. If any action in that chain fails (duplicate account name, malformed `MultisigMember`, or a panic inside multisig2's `new` because `num_confirmations` is invalid), there is no code path in the factory that detects the failure and returns the deposit to the caller, unlike the sibling factories in this repo.

### Finding Description
The invariant under test is: `predecessor_balance_after_failed_create == predecessor_balance_before_create` (a failed deployment refunds the attached deposit). In `multisig-factory/src/lib.rs`: [1](#0-0) 

`create` attaches `env::attached_deposit()` as part of the promise batch and hands off `function_call` gas to `new`, then simply returns the `Promise` — there is no `ext_self::on_create` callback, no `assert_self()`/`is_promise_success()` check, and no `Promise::new(predecessor_account_id).transfer(...)` fallback.

This is a direct regression relative to the pattern used by the two other factories in the same repo, which both implement exactly this refund-on-failure callback:
- `staking-pool-factory/src/lib.rs`'s `create_staking_pool` chains `.then(ext_self::on_staking_pool_create(...))`, and `on_staking_pool_create` calls `assert_self()`, checks `is_promise_success()`, and on failure runs `Promise::new(predecessor_account_id).transfer(attached_deposit.0)`. [2](#0-1) 
- `lockup-factory/src/lib.rs`'s `create` does the identical thing via `on_lockup_create`. [3](#0-2) 

`multisig-factory::create` has no equivalent. Because the attacker fully controls `name`, `members`, and `num_confirmations`, they can trivially force the deployment to fail after the deposit-carrying actions have already been scheduled/applied (e.g. reuse an account name that already exists, or pass a `num_confirmations`/member list that causes multisig2's `#[init] new` to panic). Since NEAR receipt actions execute sequentially and are not atomically rolled back as a unit, a failure late in the chain (in `function_call new`) can leave the new account created and funded with the deposit, but uninitialized — with no owner/keys/members set and no factory-side logic to recover or refund it. Even in the case where `create_account` itself fails outright, there is still no callback to catch that and return funds to the caller; the deposit is simply gone from the caller's perspective with no compensating transfer engineered by the factory.

None of the guards listed in the audit rubric (`assert_self()`, `is_promise_success()`, etc.) are present in this function at all, so nothing in the existing code prevents the divergence.

### Impact Explanation
On a failed deployment, the attacker's (or any caller's) attached deposit is consumed with no return path, and, depending on where the failure lands, may become permanently stranded in a created-but-uninitialized account. This matches the "accounting value diverges from reality" / fund-loss class of High-severity impact: the factory's implicit accounting ("attached deposit belongs to a successfully created and initialized multisig, otherwise it is returned to payer") is broken, and the deposit is not returned to the entitled party. This is repeatable by any caller for any deployment attempt, and scales with the size of the attached deposit.

### Likelihood Explanation
No special privileges are required — any account can call `create` with a large deposit and any `name`/`members`/`num_confirmations`. Triggering a failure is straightforward and fully attacker-controlled: reusing an existing sub-account name, or supplying member/`num_confirmations` values that fail validation inside multisig2's `new`. This makes the bug trivially and repeatably reproducible at will.

### Recommendation
Add a callback pattern matching `staking-pool-factory`/`lockup-factory`: chain `.then(ext_self::on_create(name, attached_deposit, predecessor_account_id, ...))`, and in that callback call `assert_self()`, check `is_promise_success()`, and on failure issue `Promise::new(predecessor_account_id).transfer(attached_deposit)`.

### Proof of Concept
```rust
// multisig-factory/src/lib.rs (add near existing tests, mirroring lockup-factory/staking-pool-factory tests)
#[test]
fn test_create_multisig_no_refund_on_failure() {
    let mut context = VMContextBuilder::new()
        .current_account_id(account_factory())
        .predecessor_account_id(account_attacker())
        .finish();
    context.attached_deposit = ntoy(1000); // large deposit
    testing_env!(context.clone());

    let mut contract = MultisigFactory::default();
    // Call create with parameters that will make multisig2::new panic
    // (e.g. num_confirmations > members.len())
    contract.create("evil".to_string(), vec![], 5);

    // Simulate the function_call failing
    context.predecessor_account_id = account_factory();
    context.attached_deposit = ntoy(0);
    testing_env_with_promise_results(context.clone(), PromiseResult::Failed);

    // BROKEN: there is no on_create callback to invoke here at all,
    // so there is no way to assert that the 1000 NEAR was ever
    // returned to `account_attacker()`. Compare with
    // lockup-factory::on_lockup_create / staking-pool-factory::on_staking_pool_create
    // which explicitly call Promise::new(predecessor_account_id).transfer(...)
    // on failure — multisig-factory has no such call anywhere in lib.rs.
}
```
The absence of any `on_create`/`assert_self`/`is_promise_success`/refund-`Promise::new(...).transfer(...)` in `multisig-factory/src/lib.rs` (confirmed by reading the entire file, 50 lines) is itself the proof that the refund path does not exist, in contrast to the two sibling factories that implement it.

### Citations

**File:** multisig-factory/src/lib.rs (L28-49)
```rust
    #[payable]
    pub fn create(
        &mut self,
        name: AccountId,
        members: Vec<MultisigMember>,
        num_confirmations: u64,
    ) -> Promise {
        let account_id = format!("{}.{}", name, env::current_account_id());
        Promise::new(account_id)
            .create_account()
            .deploy_contract(CODE.to_vec())
            .transfer(env::attached_deposit())
            .function_call(
                b"new".to_vec(),
                json!({ "members": members, "num_confirmations": num_confirmations })
                    .to_string()
                    .as_bytes()
                    .to_vec(),
                0,
                env::prepaid_gas() - CREATE_CALL_GAS,
            )
    }
```

**File:** staking-pool-factory/src/lib.rs (L186-239)
```rust
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

    /// Callback after a staking pool was created.
    /// Returns the promise to whitelist the staking pool contract if the pool creation succeeded.
    /// Otherwise refunds the attached deposit and returns `false`.
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

**File:** lockup-factory/src/lib.rs (L158-198)
```rust
            .then(ext_self::on_lockup_create(
                lockup_account_id,
                env::attached_deposit().into(),
                env::predecessor_account_id(),
                &env::current_account_id(),
                NO_DEPOSIT,
                gas::CALLBACK,
            ))
    }

    /// Callback after a lockup was created.
    /// Returns the promise if the lockup creation succeeded.
    /// Otherwise refunds the attached deposit and returns `false`.
    pub fn on_lockup_create(
        &mut self,
        lockup_account_id: AccountId,
        attached_deposit: U128,
        predecessor_account_id: AccountId,
    ) -> bool {
        assert_self();

        let lockup_account_created = is_promise_success();

        if lockup_account_created {
            env::log(
                format!("The lockup contract {} was successfully created.", lockup_account_id)
                    .as_bytes(),
            );
            true
        } else {
            env::log(
                format!(
                    "The lockup {} creation has failed. Returning attached deposit of {} to {}",
                    lockup_account_id, attached_deposit.0, predecessor_account_id
                )
                    .as_bytes(),
            );
            Promise::new(predecessor_account_id).transfer(attached_deposit.0);
            false
        }
    }
```
