### Title
Unprivileged front-running of `MultisigFactory::create` account-name causes attached NEAR deposit to be permanently stranded in the factory, with no refund path - (File: `multisig-factory/src/lib.rs`)

### Summary
`MultisigFactory::create` (the NEAR analog of the reported `NFTMintSaleMultiple` id-collision bug) derives the new contract's account id deterministically from the caller-supplied `name` and fires a single promise batch (`create_account` → `deploy_contract` → `transfer` → `function_call`) with no completion callback. If any unprivileged actor front-runs by creating an account with that same name before the factory's receipt executes, the whole receipt fails atomically, exactly as `nft.mintWithId` reverts when the id is already taken in the original report. Unlike its sibling factories, this contract has no `on_create`/`is_promise_success` callback to detect the failure and refund the caller, so the attached NEAR that the legitimate user paid is not returned to them.

### Finding Description
`create` builds `account_id = format!("{}.{}", name, env::current_account_id())` and immediately issues the promise batch: [1](#0-0) 

There is no `staking_pool_account_ids`-style reservation set (as in `staking-pool-factory/src/lib.rs`) and no `.then(ext_self::on_...)` callback (as in both `staking-pool-factory/src/lib.rs` and `lockup-factory/src/lib.rs`) to detect a failed `create_account` action and refund the caller: [2](#0-1) [3](#0-2) [4](#0-3) 

Because the sub-account name is fully attacker-observable/predictable (it's just `name.<factory>`), any unprivileged party can pre-create an account with the same name (e.g. by directly calling NEAR's `CreateAccount` action for that account id) before the legitimate caller's transaction lands. When the factory's receipt then attempts `create_account` on an already-existing account, that action fails and the entire batched receipt (including the `transfer(env::attached_deposit())` action) fails atomically. Per NEAR runtime semantics, the unspent/failed-action balance from a failed receipt is refunded to the *predecessor of that receipt* — i.e. the `multisig-factory` contract account itself — not to the original `predecessor_account_id` who called `create()` and attached the NEAR. Since `MultisigFactory` stores no state (`#[derive(... Default)] pub struct MultisigFactory {}`) and has no callback to re-transfer the refunded amount back to the caller, the deposit becomes permanently stranded inside the factory's own account balance with no code path to retrieve it for that user.

This is the direct NEAR analog of the reported issue: an external, unprivileged actor causing an id/name collision that the target contract does not defensively handle, breaking the custody binding "attached deposit sent = deposit either used for pool creation or returned to sender."

### Impact Explanation
This crosses the "value debited versus value delivered" custody boundary called out in scope: the caller's `env::attached_deposit()` is debited from their account but never delivered to the intended multisig deployment nor returned to them — it is permanently frozen inside the `multisig-factory` account, unrecoverable by the caller. This matches the Critical impact category ("funds permanently frozen") and requires no privileged role — any unprivileged party can create/observe an account name and race the factory call.

### Likelihood Explanation
Account names passed to `create` are chosen by the caller and are visible in the mempool/transaction before execution, and the resulting sub-account id is deterministic (`name.multisig-factory-account`). An attacker (or even an unrelated party who happens to want the same account name) can front-run by creating that account first with a trivial transaction, at low cost, deterministically triggering the loss for any user who then calls `create` with that name. This is a straightforward, repeatable griefing/loss vector requiring only the ability to send NEAR transactions.

### Recommendation
Mirror the safety pattern already used in `staking-pool-factory` and `lockup-factory`: add an `ext_self::on_multisig_create` callback attached via `.then(...)`, check `is_promise_success()`, and if the creation failed, `Promise::new(predecessor_account_id).transfer(attached_deposit)` to refund the caller. Optionally also maintain a reservation set (as `staking-pool-factory` does with `staking_pool_account_ids`) to reject/avoid submitting the promise for names likely to collide.

### Proof of Concept
1. Attacker observes/decides on a `name` value (e.g., `"multisig1"`).
2. Attacker submits a plain `CreateAccount` transaction for `multisig1.<multisig-factory-account>` (no dependency on the factory).
3. Victim calls `MultisigFactory::create(name: "multisig1", members, num_confirmations)` with attached NEAR deposit, per [5](#0-4) .
4. The factory's batched receipt's `create_account` action fails because `multisig1.<multisig-factory-account>` already exists; the whole receipt (including the `transfer`) fails.
5. NEAR runtime refunds the failed receipt's balance to the factory contract account (the receipt's predecessor), not to the victim.
6. `MultisigFactory` has no stored state or callback to detect this and refund the victim — the deposit is permanently stuck in the factory account, confirmed by the absence of any `on_create`/`is_promise_success`/refund logic in the file (searched and found none), contrasted with the explicit refund logic present in `staking-pool-factory/src/lib.rs:225-237` and `lockup-factory/src/lib.rs:187-197`.

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

**File:** staking-pool-factory/src/lib.rs (L166-195)
```rust
        assert!(
            self.staking_pool_account_ids
                .insert(&staking_pool_account_id),
            "The staking pool account ID already exists"
        );

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
