### Title
Missing refund path in `MultisigFactory::create()` permanently locks attached NEAR when multisig deployment fails - (File: multisig-factory/src/lib.rs)

### Summary
`MultisigFactory::create()` fires an unsupervised cross-account promise batch (`create_account` → `deploy_contract` → `transfer` → `function_call`) to a `name.<factory>` subaccount and returns the `Promise` directly, with no `.then()` callback to verify success. If that batch fails for any reason — most notably because another caller (an attacker racing the transaction, or simply another legitimate user) already claimed the same `name` — the attached NEAR deposit is never returned to the caller. This is a real accounting break, not just a reverted transaction: the deposit was already deducted from the factory account's balance when the promise was created, and on failure it is refunded to the factory account itself, not to the original caller, since no refund logic exists.

### Finding Description
Compare `MultisigFactory::create()`: [1](#0-0) 

with the sibling factories, both of which explicitly check the outcome and refund the caller on failure: [2](#0-1) [3](#0-2) 

`LockupFactory::create()` and `StakingPoolFactory::create_staking_pool()` both `.then()` a callback (`on_lockup_create` / `on_staking_pool_create`) that calls `is_promise_success()` and, on failure, issues `Promise::new(predecessor_account_id).transfer(attached_deposit.0)` to return the deposit to the original caller. `MultisigFactory::create()` has no such callback at all — the promise chain is returned as-is with no accounting of success/failure.

Because a NEAR subaccount name can only be claimed once, two callers who both invoke `create()` with the same `name` (e.g., an attacker observing a pending transaction and racing it, exactly the front-running pattern from the external report) will have one succeed and one fail. The losing caller's batched receipt (`create_account`/`deploy_contract`/`transfer`/`function_call` to `name.<factory>`) fails atomically because `create_account` errors out (account already exists). The `transfer` action inside that failed receipt never executes, so the attached deposit — already withdrawn from the factory contract's balance at promise-creation time — is refunded by the protocol back to the *predecessor of the failed receipt*, which is the `MultisigFactory` contract account itself, not the original caller. The caller has no recourse: `MultisigFactory` exposes no method to reclaim or redistribute these stranded funds.

This breaks the custody binding: value debited from the caller (attached deposit) ≠ value delivered (a new multisig contract) or refunded (as the sibling factories guarantee). The caller's deposit is permanently locked inside the factory's balance.

### Impact Explanation
Any legitimate user calling `create()` on `multisig-factory` whose `name` collides with another concurrent `create()` call (whether by accident or via deliberate front-running as described in the report) permanently loses their attached NEAR deposit — it becomes stuck in the factory contract's balance with no recovery path. This matches the "funds permanently frozen" Critical impact category, since the deposited NEAR is neither delivered to a working multisig nor returned to its owner.

### Likelihood Explanation
Exploitation only requires observing a pending `create()` transaction (public in the mempool) and submitting a competing `create()` call with the identical `name` parameter before it executes — an unprivileged, cheap, and repeatable action requiring no special access. Non-adversarial collisions (two independent users choosing the same `name`) trigger the same loss.

### Recommendation
Add a callback to `MultisigFactory::create()` analogous to `on_lockup_create` / `on_staking_pool_create`: chain a `.then()` call that checks `is_promise_success()` and, on failure, refunds `env::attached_deposit()` to `env::predecessor_account_id()`. Additionally, consider deriving the subaccount name deterministically from the caller's identity (as flagged in the external report) to reduce the likelihood of name collisions in the first place.

### Proof of Concept
1. User A submits `create({ name: "myms", members: [...], num_confirmations: 1 })` to `multisig-factory`, attaching e.g. 50 NEAR.
2. Attacker observes this pending transaction and submits `create({ name: "myms", members: [attacker_key], num_confirmations: 1 })` with minimal deposit, and it lands first.
3. `myms.multisig-factory` is created by the attacker's call.
4. User A's receipt batch (`create_account`, `deploy_contract`, `transfer`, `function_call` to `myms.multisig-factory`) fails at the `create_account` step since the account already exists.
5. Because `MultisigFactory::create()` has no callback (`multisig-factory/src/lib.rs` lines 34-49), User A's attached 50 NEAR is refunded to the `multisig-factory` contract account itself rather than to User A, and is permanently unrecoverable by User A.

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

**File:** lockup-factory/src/lib.rs (L136-198)
```rust
        Promise::new(lockup_account_id.clone())
            .create_account()
            .deploy_contract(CODE.to_vec())
            .transfer(env::attached_deposit())
            .function_call(
                b"new".to_vec(),
                near_sdk::serde_json::to_vec(&LockupArgs {
                    owner_account_id,
                    lockup_duration,
                    lockup_timestamp,
                    transfers_information: TransfersInformation::TransfersEnabled {
                        transfers_timestamp: transfers_enabled,
                    },
                    vesting_schedule,
                    release_duration,
                    staking_pool_whitelist_account_id,
                    foundation_account_id: foundation_account,
                })
                    .unwrap(),
                NO_DEPOSIT,
                gas::LOCKUP_NEW,
            )
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

**File:** staking-pool-factory/src/lib.rs (L172-239)
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
