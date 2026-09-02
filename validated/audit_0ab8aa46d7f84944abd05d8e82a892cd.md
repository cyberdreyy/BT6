### Title
Unprivileged loss of attached NEAR deposit due to missing account-id validation and refund logic in `create` - ([File: multisig-factory/src/lib.rs])

### Summary
`MultisigFactory::create` in [1](#0-0)  builds a sub-account name by naive string concatenation of an attacker-supplied `name: AccountId` with `env::current_account_id()`, exactly the pattern flagged in the external report (`appchain-registry` concatenating `appchain_id` without validation). Unlike its sibling factories (`lockup-factory`, `staking-pool-factory`), this function performs **no validation** of `name` (no `is_valid_account_id` check, no `.` rejection) and, critically, **has no callback** to detect account-creation failure and refund the caller's attached deposit.

### Finding Description
The function is `#[payable]`, so `env::attached_deposit()` is pulled from the caller into the factory contract's balance before the sub-account creation promise is dispatched: [2](#0-1) 

Compare this to `staking-pool-factory::create_staking_pool`, which validates the derived account id and registers `on_staking_pool_create` as a callback that refunds `predecessor_account_id` if the account creation promise fails: [3](#0-2) [4](#0-3) 

And `lockup-factory::create`, which similarly attaches `.then(ext_self::on_lockup_create(...))`: [5](#0-4) 

`multisig-factory::create` has none of this: it neither validates `name` (so any string containing `.`, uppercase characters, invalid length, etc. can be supplied) nor chains a `.then(...)` callback. Any of the following unprivileged-attacker-controlled conditions cause the `create_account`/`transfer`/`deploy_contract`/`function_call` action batch to fail atomically at the protocol level:
- `name` produces an invalid NEAR account id (e.g. contains `.`, uppercase letters, disallowed characters, or invalid length) when concatenated with `env::current_account_id()`.
- The derived sub-account already exists (e.g. an attacker or anyone else previously created the same `name` under this factory).

In all such cases, the deposit that was already debited from the caller and credited to the factory contract's balance (via `#[payable]`) is committed to a receipt that fails to execute the `create_account`/`transfer` actions. Because there is no `.then()` callback and no other withdraw/refund method exists anywhere in `multisig-factory/src/lib.rs`, the deposited NEAR is retained by the factory contract balance permanently, unreachable by the original depositor or anyone else — the contract exposes no admin/owner and no refund entrypoint.

### Impact Explanation
This breaks the custody binding "value debited from the caller versus value delivered to (or returned from) the intended callee." The caller's attached NEAR is debited and never delivered to the new multisig account nor returned to the caller — it is permanently frozen inside the `multisig-factory` contract with no code path to recover it. This matches the Critical impact criterion "funds permanently frozen," achievable by any unprivileged caller supplying a malformed `name` or colliding with an existing sub-account name, with no need for a foundation, owner, multisig member, or redeploy.

### Likelihood Explanation
Likelihood is high: any external, unprivileged account can call `create` with attached deposit and pass an intentionally or accidentally invalid `name` (e.g., containing `.`, disallowed characters, or exceeding length limits) — no permission or special setup is required, and NEAR account-id validation rules are well documented, making it trivial to construct a payload guaranteed to fail sub-account creation while still debiting the deposit.

### Recommendation
Validate the derived `account_id` (e.g., `env::is_valid_account_id`) before dispatching the creation promise, and reject early with an assertion, as done in `staking-pool-factory::create_staking_pool`. Additionally, chain a `.then(ext_self::on_create(...))` callback that checks `is_promise_success()` and, on failure, refunds `env::attached_deposit()` to `env::predecessor_account_id()`, mirroring the pattern already implemented in `lockup-factory` and `staking-pool-factory`.

### Proof of Concept
1. Attacker calls `multisig-factory.create({"name": "invalid.name", "members": [...], "num_confirmations": 1})` (or any `name` producing an invalid/colliding account id) with an attached deposit of N NEAR.
2. `#[payable]` debits N NEAR from the attacker into the factory contract's balance at [6](#0-5) .
3. `Promise::new(account_id).create_account()...` fails at the protocol level because `account_id` is invalid (contains `.`) or already exists.
4. Because `create` returns the promise directly with no `.then()` callback ( [7](#0-6) ), there is no logic to detect the failure and return the N NEAR to the attacker.
5. The N NEAR remains permanently in the `multisig-factory` contract balance; no method in the contract can withdraw or refund it.

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

**File:** staking-pool-factory/src/lib.rs (L149-195)
```rust
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

**File:** lockup-factory/src/lib.rs (L136-165)
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
```
