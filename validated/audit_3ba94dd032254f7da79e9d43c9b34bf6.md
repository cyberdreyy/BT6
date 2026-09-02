### Title
Attached NEAR is permanently locked in `MultisigFactory` when multisig account creation fails - (File: `multisig-factory/src/lib.rs`)

### Summary
`MultisigFactory::create` is a `#[payable]` function that forwards the caller's attached deposit into a single promise batch (`create_account` → `deploy_contract` → `transfer` → `function_call`) to spin up a new multisig sub-account. Unlike the two sibling factories in this repository, it has no `.then()` callback to detect failure and refund the depositor, so if the batch fails the attached NEAR is stranded on the factory contract with no method able to retrieve it.

### Finding Description
`create` builds the new account id from an unvalidated caller-supplied `name` and issues one promise batch containing the deposit transfer: [1](#0-0) 

If any action in that batch fails — e.g. `create_account` fails because `{name}.{factory}` already exists, `name` produces an invalid account id, or the deposit is insufficient to cover the new account's storage — the entire outgoing receipt is rolled back atomically. The `transfer(env::attached_deposit())` inside it never executes, so the NEAR does not reach the intended sub-account, and because it is not automatically returned to the original caller either, it remains part of the `MultisigFactory` contract's own balance. `MultisigFactory` exposes no `withdraw`, no owner, and no other method capable of moving that balance out, so it is permanently locked.

This directly contrasts with the two other factories in the repository that use exactly this creation pattern but add an explicit success/failure callback that refunds the depositor on failure:
- `lockup-factory/src/lib.rs` `create` (attaches deposit) plus `on_lockup_create`, which explicitly does `Promise::new(predecessor_account_id).transfer(attached_deposit.0)` on failure: [2](#0-1) 
- `staking-pool-factory/src/lib.rs` `create_staking_pool` plus `on_staking_pool_create`, which likewise refunds on failure: [3](#0-2) 

`MultisigFactory::create` has neither the pre-flight validation these two perform (e.g. deduplicated/reserved account-id checks, minimum deposit checks) nor the refund callback, so the "attached deposit is returned if creation fails" guarantee that exists elsewhere in this codebase is absent here.

### Impact Explanation
This breaks the custody equality: `attached_deposit == (delivered to new multisig account) + (refunded to caller)`. When account creation fails, the deposit is neither delivered nor refunded — it is silently absorbed into the `MultisigFactory` account balance with no extraction path, i.e., NEAR is permanently frozen. Per the provided impact taxonomy this is Critical (funds permanently frozen).

### Likelihood Explanation
Likelihood is meaningfully non-trivial and requires no privileged access: an unprivileged caller triggers this simply by calling `create` with a `name` that collides with an already-created multisig sub-account (a very plausible operational occurrence, especially for common names), or with a `name` that yields an invalid account id, or by attaching insufficient NEAR to cover the new account's storage cost.

### Recommendation
Add a callback analogous to `on_lockup_create` / `on_staking_pool_create`: chain `.then(...)` on the creation promise, check `is_promise_success()`, and if it failed, explicitly `Promise::new(predecessor_account_id).transfer(attached_deposit)` to return the funds to the original caller.

### Proof of Concept
1. Call `create` with `name = "alice"`, attaching `X` NEAR, to create `alice.<factory>` — succeeds.
2. Call `create` again with `name = "alice"` (or any other input causing `create_account` to fail, e.g., invalid name), attaching `Y` NEAR.
3. The batched receipt fails because the account already exists (or another action in the batch fails); the `transfer(Y)` inside the batch is never executed.
4. `Y` NEAR remains part of the `MultisigFactory` contract's balance. There is no owner, no withdraw method, and no callback to return it — the funds are permanently locked, contrasting with the refund behavior implemented in `lockup-factory` and `staking-pool-factory` for the identical failure scenario.

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

**File:** lockup-factory/src/lib.rs (L168-197)
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
