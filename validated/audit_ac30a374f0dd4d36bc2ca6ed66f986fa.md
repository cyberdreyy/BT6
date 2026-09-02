### Title
Attached NEAR deposit permanently stuck in `MultisigFactory` when multisig deployment fails - ([File: multisig-factory/src/lib.rs])

### Summary
`MultisigFactory::create` batches account creation, contract deployment, NEAR transfer and the `new` initialization call into a single promise, but unlike the sibling factories in this repo it has no completion callback and no way to recover funds if that batch fails.

### Finding Description
`create()` receives an attached deposit and issues one chained promise: `create_account().deploy_contract(CODE).transfer(attached_deposit).function_call("new", ...)`, then returns that `Promise` directly with no `.then()` callback to itself [1](#0-0) . `MultisigFactory` is declared as a bare, field-less struct with only this `create` method - there is no owner, no admin, and no withdraw/refund entrypoint of any kind [2](#0-1) .

If the batched receipt fails atomically - e.g. the derived `account_id = "{name}.{factory}"` already exists (`create_account` fails), or the `multisig2::new` initialization call panics on invalid `members`/`num_confirmations` input - all actions in that receipt roll back together. The NEAR that was earmarked for `.transfer(env::attached_deposit())` inside the batch never leaves the factory's own account balance, because the deposit had already been credited to the factory as part of the original `create()` function-call receipt (this is exactly why `attached_deposit` needs an explicit refund transfer on failure in the analogous factories). With `MultisigFactory` lacking a callback and lacking any owner or withdrawal method, those tokens remain locked in the factory contract's balance indefinitely.

This mirrors the `FjordAuctionFactory`/`FjordAuction` root cause: an unprivileged caller's assets end up custodied by a contract account whose code provides no path to move them back out. The binding that breaks is: `deposit sent by caller == NEAR recoverable after a failed/degenerate deployment`, which should be an equality but is not, because the failure path returns `0` while the deposit stays trapped at `factory_account_balance`.

Contrast with the two other factories in the repo, which explicitly implement this recovery path:
- `LockupFactory::create` chains a callback `on_lockup_create` specifically to refund the deposit on error (documented as "Refund deposit on errors") [3](#0-2) [4](#0-3) .
- `StakingPoolFactory::create_staking_pool` chains `on_staking_pool_create`, which checks `is_promise_success()` and explicitly transfers the attached deposit back to `predecessor_account_id` on failure [5](#0-4) .

`MultisigFactory::create` has no such callback, no `is_promise_success` check, and no refund logic at all [1](#0-0) .

### Impact Explanation
Any unprivileged user calling `create` with an attached deposit risks having that NEAR permanently frozen inside the `MultisigFactory` account if the subaccount name collides with an existing account, or if the supplied `members`/`num_confirmations` cause `multisig2::new` to panic. Since the factory contract exposes no owner and no withdrawal method, there is no way — not even for the deployer of the factory — to recover the stuck balance. This matches the "funds permanently frozen" / "insolvent ledger" impact class: the deposit debited from the caller no longer equals any NEAR recoverable by anyone.

### Likelihood Explanation
Likelihood is moderate: it requires the batched deployment receipt to fail, which can happen simply by an unprivileged caller choosing a `name` that collides with an already-created multisig subaccount (a very plausible, attacker-triggerable or accidental scenario since `name` is fully caller-controlled), or by supplying `num_confirmations`/`members` values that violate `multisig2::new`'s invariants.

### Recommendation
Add a callback (mirroring `on_lockup_create` / `on_staking_pool_create`) that checks `is_promise_success()` after the batched creation promise and, on failure, transfers the attached deposit back to `env::predecessor_account_id()`.

### Proof of Concept
Conceptual reproduction (would need a Devin session against a local NEAR sandbox to execute):
1. Deploy `MultisigFactory`.
2. Call `create` with `name = "dup"`, valid `members`, and an attached deposit, allowing `dup.<factory>` to be created successfully.
3. Call `create` again with the same `name = "dup"` and a new attached deposit — `create_account` for `dup.<factory>` fails because the account already exists, causing the whole batched receipt to fail.
4. Observe that the second attached deposit is not returned to the caller and is not held by any recoverable account; `MultisigFactory`'s balance increased by the deposit amount with no method to withdraw it.

### Citations

**File:** multisig-factory/src/lib.rs (L22-27)
```rust
#[near_bindgen]
#[derive(BorshSerialize, BorshDeserialize, Default)]
pub struct MultisigFactory {}

#[near_bindgen]
impl MultisigFactory {
```

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

**File:** lockup-factory/src/lib.rs (L136-166)
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
```

**File:** lockup-factory/README.md (L13-18)
```markdown
The benefits: 
1. Lockups can be funded from any account.
2. No need to have access to the foundation keys to create lockup.
3. Auto-generates the lockup from the owner account.
4. Refund deposit on errors.

```

**File:** staking-pool-factory/src/lib.rs (L200-238)
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
```
