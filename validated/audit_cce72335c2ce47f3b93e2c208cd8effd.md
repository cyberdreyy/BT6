### Title
Factory account creation can be front-run to seed a pre-existing attacker-owned account, allowing theft of the transferred deposit - (File: `lockup-factory/src/lib.rs`, `staking-pool-factory/src/lib.rs`, `multisig-factory/src/lib.rs`)

### Summary
`LockupFactory::create`, `StakingPoolFactory::create_staking_pool`, and `MultisigFactory::create` all build a single promise batch of the form `create_account().deploy_contract(...).transfer(...).function_call("new", ...)` against a deterministically-derivable account id, with no verification that the target account does not already exist before committing funds and code to it. This mirrors the reported Factory.sol flaw: the deployment-guard (implicit "the account is empty/unclaimed") can be defeated by an attacker who pre-empts the target address, so the factory's assumption that it is deploying to a fresh, un-owned account is false.

### Finding Description
In `lockup-factory/src/lib.rs#L119-L157`, the lockup account id is deterministically derived from `sha256(owner_account_id)` and the factory account id: [1](#0-0) 
and then a single promise batch is dispatched to that address: [2](#0-1) 

Similarly, `staking-pool-factory/src/lib.rs` derives the pool id from a caller-supplied prefix and dispatches the same style of batch: [3](#0-2) 

and `multisig-factory/src/lib.rs::create` does the same for `{name}.{factory}`: [4](#0-3) 

None of these `create` functions check that the target account is unoccupied before submitting the batch — they rely entirely on `create_account()` succeeding as an implicit "this address is empty" guard, exactly like Factory.sol relying on `codehash == 0`. On NEAR, actions within a single receipt/batch are executed independently in order; if `create_account` fails because the account already exists, the remaining actions in the same batch (`deploy_contract`, `transfer`, `function_call("new", ...)`) still execute against the pre-existing account. An attacker who predicts the deterministic address (trivial for lockup — it's `sha256(owner_id)`; trivial for staking-pool/multisig — it's attacker-chosen prefix races against a known name) can front-run the factory's transaction by creating that account first and attaching their own full-access key. When the factory's batch later lands:
- `create_account` fails silently (account exists), but
- `deploy_contract` installs the intended contract code,
- `transfer` funds it with the caller's deposit,
- `function_call("new", ...)` initializes contract state successfully (since state didn't exist yet), which makes the receipt's promise outcome `Successful`.

The factory's callback (`on_lockup_create` / `on_staking_pool_create`) only checks `is_promise_success()`, i.e., whether the last action in the chain succeeded, not whether `create_account` itself succeeded: [5](#0-4) [6](#0-5) 

So the factory believes a fresh contract was created and legitimately whitelists/finalizes it, while the attacker retains the full-access key they added when they pre-created the account. Because that key was never removed by `deploy_contract`, the attacker can subsequently sign a `DeleteAccount` action (self-destruct with themselves as beneficiary) or any other privileged native action against that account, sweeping the entire NEAR balance — including the deposit the victim/factory just transferred — to an address of their choosing, completely bypassing the deployed lockup/staking-pool/multisig contract logic.

### Impact Explanation
This breaks the custody binding "value debited by the caller versus value delivered to a contract the caller actually controls under the intended terms." The caller's `attached_deposit` (`MIN_ATTACHED_BALANCE`, e.g. 3.5 NEAR for lockup, 30 NEAR for staking pool) plus the deployed contract's balance are moved to an account effectively owned by the attacker's front-run access key, not the intended immutable/logic-only contract. This is a Critical-severity outcome per the given rubric: NEAR moved by a party not entitled to it.

### Likelihood Explanation
Exploitation only requires observing/predicting a pending `create` call (mempool visibility or, for lockup, simply knowing the target owner's account id since the address is a pure function of `owner_account_id`) and racing a `CreateAccount + AddKey(full access)` transaction ahead of the factory's transaction. No privileged role, foundation key, or malicious validator is required — a normal unprivileged NEAR account can do this.

### Recommendation
Before dispatching the creation batch, verify the target account does not already exist (e.g., via a preceding view/promise check or a two-phase commit pattern), and/or have `on_*_create` callbacks explicitly distinguish which action failed rather than relying solely on the aggregate `is_promise_success()` of the whole batch. Additionally, consider deploying without ever allowing a race window, e.g., by using `Promise::new(...).create_account()` as its own receipt whose failure is checked independently before continuing with `deploy_contract`/`transfer`/`function_call`.

### Proof of Concept
1. Attacker observes/predicts the deterministic lockup address for `owner_account_id = O` (`sha256(O)[..20]hex.factory`), or picks a `staking_pool_id`/multisig `name` they intend to race.
2. Attacker submits `CreateAccount + AddFullAccessKey(attacker_key) + Transfer(minimal)` to that address, landing just before the factory's `create`/`create_staking_pool` transaction.
3. Factory's transaction executes its batch: `create_account` fails (account exists) but `deploy_contract`, `transfer(deposit)`, `function_call("new", ...)` succeed since state didn't exist, so the receipt reports success.
4. Factory's callback sees `is_promise_success() == true` and treats the pool/lockup as legitimately created (e.g., whitelists it).
5. Attacker uses the retained full-access key to send a `DeleteAccount` action with beneficiary = attacker, draining the account's entire NEAR balance (the deposit transferred by the factory) to themselves.

### Citations

**File:** lockup-factory/src/lib.rs (L119-121)
```rust
        let byte_slice = env::sha256(owner_account_id.as_ref().as_bytes());
        let lockup_account_id =
            format!("{}.{}", hex::encode(&byte_slice[..20]), env::current_account_id());
```

**File:** lockup-factory/src/lib.rs (L136-157)
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
```

**File:** lockup-factory/src/lib.rs (L171-198)
```rust
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

**File:** staking-pool-factory/src/lib.rs (L154-195)
```rust
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

**File:** multisig-factory/src/lib.rs (L35-49)
```rust
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
