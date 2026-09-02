## No vulnerability found for this question.

**Reasoning:**

The premise that `create()` can leave an "account created but `new` call failed" orphaned state does not match actual NEAR protocol semantics, and the repo's own tests confirm this.

`LockupFactory::create` builds a single promise chain — `create_account().deploy_contract(...).transfer(...).function_call(b"new", ...)` — with no `.then()` between these actions, meaning they are all part of **one atomic action receipt**, not separate receipts. [1](#0-0) . NEAR action receipts execute all-or-nothing: if any action in the receipt fails (e.g. the `new` call panics), the entire receipt — including `CreateAccount`, `DeployContract`, and `Transfer` — is rolled back, and the attached balance is refunded via a system-generated refund receipt back to the receipt's predecessor (the factory itself).

This is exactly what the contract's own test, `test_create_lockup_rollback`, exercises: it simulates `PromiseResult::Failed` and explicitly credits the factory's `account_balance` (`context.account_balance += ntoy(35)`) to represent the automatic NEAR refund of the failed receipt's balance back to the factory, which `on_lockup_create` then forwards to `predecessor_account_id` [2](#0-1) . The callback itself treats the whole chain as a single pass/fail unit via `is_promise_success()` [3](#0-2) , with no logic branching for "partially succeeded" creation — because no such state is reachable.

Since the lockup account address is deterministic (`sha256(owner_account_id)` prefixed) [4](#0-3) , and receipt execution is atomic, there are only two possible outcomes for a given owner: either the account is fully created *and* initialized, or nothing persists on-chain and the deposit is refunded — there is no intermediate "code deployed, funded, uninitialized" state to squat on or exploit. Repeated `create` calls for the same owner in the same block are also processed sequentially by the NEAR runtime (not concurrently), so a second `CreateAccount` action against an already-existing account simply fails cleanly and triggers the refund path, without any race window. The invariant "one owner id yields exactly one lockup account" therefore holds, and no accounting divergence or fund-freezing scenario is reachable through this path.

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

**File:** lockup-factory/src/lib.rs (L352-388)
```rust
    #[test]
    fn test_create_lockup_rollback() {
        let mut context = VMContextBuilder::new()
            .current_account_id(account_factory())
            .predecessor_account_id(account_near())
            .finish();
        testing_env!(context.clone());

        let mut contract = LockupFactory::new(
            whitelist_account_id(),
            foundation_account_id(),
        );

        const LOCKUP_DURATION: u64 = 63036000000000000; /* 24 months */
        let lockup_duration: WrappedTimestamp = LOCKUP_DURATION.into();

        context.is_view = false;
        context.predecessor_account_id = String::from(account_tokens_owner());
        context.attached_deposit = ntoy(35);
        testing_env!(context.clone());
        contract.create(account_tokens_owner(), lockup_duration, None, None, None, None);

        context.predecessor_account_id = account_factory();
        context.attached_deposit = ntoy(0);
        context.account_balance += ntoy(35);
        testing_env_with_promise_results(context.clone(), PromiseResult::Failed);
        let res = contract.on_lockup_create(
            lockup_account(),
            ntoy(35).into(),
            String::from(account_tokens_owner()),
        );

        match res {
            true => panic!("Unexpected result, should return false"),
            false => assert!(true),
        };
    }
```
