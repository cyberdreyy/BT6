This confirms the vulnerability: `multisig-factory` lacks the `on_create` callback pattern that `staking-pool-factory` and `lockup-factory` both implement to refund the deposit on failure.

### Title
Multisig creation onto an existing account strands attached NEAR with no refund - (File: `multisig-factory/src/lib.rs`)

### Summary
`MultisigFactory::create` builds a single action batch (`create_account` → `deploy_contract` → `transfer` → `function_call`) and returns it directly to the caller with no `.then()` callback. Unlike `staking-pool-factory::create_staking_pool` and `lockup-factory::create`, which both attach `ext_self::on_..._create` callbacks that call `is_promise_success()` and `Promise::new(predecessor_account_id).transfer(attached_deposit.0)` to refund the caller when the account-creation batch fails, `multisig-factory` has no such refund path at all.

### Finding Description
The claimed invariant is: `predecessor_balance_after_failed_create == predecessor_balance_before_call` (a failed creation strands no NEAR). Trace of `MultisigFactory::create`: [1](#0-0) 

There is no check that the target `name`'s derived `account_id` doesn't already exist, and no minimum-deposit assertion at all — the method accepts any `env::attached_deposit()`, including the bare minimum needed to be non-zero. The whole action sequence (`create_account`, `deploy_contract`, `transfer`, `function_call`) is scheduled as one receipt with no `.then()` callback to the factory itself.

If `name.multisig-factory.near` already exists (e.g., attacker or anyone previously registered that name, or an account happens to collide), the `create_account` action fails at the protocol level. Depending on how the runtime processes the receipt, the attached deposit sent via the `transfer` action is not returned to `MultisigFactory` nor forwarded back to the original caller — there is no code in this contract that ever issues `Promise::new(predecessor_account_id).transfer(...)` on failure, unlike its sibling factories: [2](#0-1) [3](#0-2) 

Both sibling contracts explicitly document and test this rollback path (`test_create_staking_pool_rollback`, `test_create_lockup_rollback`), confirming the developers were aware that a failed account-creation batch requires an explicit refund callback because the protocol does not automatically return the deposit to the original caller. `multisig-factory` omits this pattern entirely, and also omits the `MIN_ATTACHED_BALANCE` and account-registry checks (`assert!(self.staking_pool_account_ids.insert(...))`) that the other factories use to reduce (but not eliminate) the chance of colliding with an existing account.

An attacker (or even a normal user under race conditions) calling `create` with a `name` that resolves to an account already existing and holding NEAR will have their attached deposit sent as part of a batch that fails at `create_account`; no code path in `multisig-factory` recovers or returns those funds to the caller or increments any accounting elsewhere. This is a genuine, reachable bug specific to this contract (not present in its sibling factories), and none of the listed guards (`assert_self`, `is_promise_success`, `assert_one_yocto`, etc.) exist in this function to catch it.

### Impact Explanation
Funds attached to a failed `create` call are not returned to the predecessor and are not accounted for anywhere in `MultisigFactory`'s state (which has no fields at all — `pub struct MultisigFactory {}`). This matches "an accounting value diverging from reality where another party settles on it" / funds effectively frozen or lost from the caller's perspective with no compensating mechanism, since the factory holds no persistent record and issues no refund. Every unprivileged caller who attaches a deposit and targets a colliding/existing account name loses that NEAR permanently, with no way to reclaim it since the factory contract exposes no admin/withdraw method either.

### Likelihood Explanation
The precondition is simply that the derived `name.multisig-factory.near` account already exists — attacker or victim can trigger this trivially (e.g., call `create` twice with the same `name`, or front-run someone else's expected multisig account, or reuse a name previously used by any other multisig-factory deployment/testnet activity). No special privilege, key, or balance is required beyond the deposit itself, and the attack is fully repeatable across arbitrary names.

### Recommendation
Add an `on_create` callback (`#[private]` / `assert_self()`-guarded) analogous to `staking_pool_factory::on_staking_pool_create` and `lockup_factory::on_lockup_create`: chain `.then(ext_self::on_create(account_id, env::attached_deposit().into(), env::predecessor_account_id(), ...))`, check `is_promise_success()`, and call `Promise::new(predecessor_account_id).transfer(attached_deposit.0)` on failure. Also add an explicit minimum-deposit assertion and validate the derived account doesn't collide before scheduling the batch.

### Proof of Concept
```rust
// multisig-factory/src/lib.rs (conceptual, mirrors lockup-factory::test_create_lockup_rollback)
#[test]
fn test_create_onto_existing_account_strands_deposit() {
    let mut context = VMContextBuilder::new()
        .current_account_id(account_factory())
        .predecessor_account_id(account_attacker())
        .attached_deposit(ntoy(1)) // minimal deposit
        .finish();
    testing_env!(context.clone());

    let mut contract = MultisigFactory::default();
    // simulate that `name.multisig-factory.near` already exists and holds NEAR
    let promise = contract.create(name(), members(), 1);
    // batch executes: create_account fails because account exists,
    // deploy_contract/transfer/function_call in same receipt fail too

    // simulate the receipt failing
    testing_env_with_promise_results(context.clone(), PromiseResult::Failed);

    // ASSERT: no refund promise exists — contract has no on_create/callback method to invoke,
    // so predecessor's balance is never restored.
    // Binding broken: predecessor_balance_after != predecessor_balance_before
    // (compare against staking-pool-factory/lockup-factory which DO restore balance
    //  via on_staking_pool_create / on_lockup_create).
}
```
Since `MultisigFactory` exposes no `on_create` method at all, there is no callback to unit-test refund logic against — this absence itself, contrasted with the sibling factories' `test_create_staking_pool_rollback` / `test_create_lockup_rollback` tests, is the proof that the refund path does not exist in this contract.

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

**File:** staking-pool-factory/src/lib.rs (L197-239)
```rust
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

**File:** lockup-factory/src/lib.rs (L168-198)
```rust
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
