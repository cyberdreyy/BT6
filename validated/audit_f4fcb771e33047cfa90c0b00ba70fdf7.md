Based on tracing the exact code path in `lockup-factory/src/lib.rs`, the described attack does not hold.

## Analysis

The `create` function builds a **single batched Promise** (one receipt) with actions in this exact order: `create_account()` → `deploy_contract()` → `transfer()` → `function_call()`, followed by a **separate** `.then()` callback (`on_lockup_create`) in a second receipt. [1](#0-0) 

Per NEAR protocol semantics, actions within a single receipt execute sequentially, and if an action fails, subsequent actions in that same receipt do not execute — the receipt is marked failed as a whole. Since `create_account()` is the **first** action in the batch, if the derived `lockup_account_id` (deterministic: `sha256(owner_account_id)[..20] + "." + factory`) already exists (attacker front-ran with the same `owner_account_id`), `create_account` fails immediately with `AccountAlreadyExists` **before** `transfer()` or `deploy_contract()` ever run. No NEAR leaves the factory toward the occupied account in that failed receipt. [2](#0-1) [3](#0-2) 

The callback then observes `is_promise_success()` returns `false` and explicitly refunds the entire `attached_deposit` back to `predecessor_account_id` (the legitimate caller who issued `create`), not to the attacker: [4](#0-3) 

This exact rollback path is already covered by `test_create_lockup_rollback`, which simulates a failed promise (`PromiseResult::Failed`) and asserts the callback returns `false` (i.e., refund branch taken): [5](#0-4) 

Additionally, even in the "successful front-run" scenario (attacker submits `create` first with the same `owner_account_id`), the resulting lockup contract is initialized with `owner_account_id` = the victim's account — the attacker cannot redirect the NEAR to themselves. The attacker would be spending their **own** `MIN_ATTACHED_BALANCE` (≥3.5 NEAR) to fund a lockup contract owned by the victim, with no path to extract or redirect the victim's or protocol's funds to themselves. The account-existence collision is a legitimate first-come-first-served race inherent to the deterministic, permissionless `create()` design — not a fund-draining bug.

## Conclusion

No vulnerability found for this question.

### Citations

**File:** lockup-factory/src/lib.rs (L117-117)
```rust
        assert!(env::attached_deposit() >= MIN_ATTACHED_BALANCE, "Not enough attached deposit");
```

**File:** lockup-factory/src/lib.rs (L119-121)
```rust
        let byte_slice = env::sha256(owner_account_id.as_ref().as_bytes());
        let lockup_account_id =
            format!("{}.{}", hex::encode(&byte_slice[..20]), env::current_account_id());
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
