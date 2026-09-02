### Title
`MultisigFactory::create` has no refund/rollback path, permanently freezing attached NEAR on creation failure - (File: `multisig-factory/src/lib.rs`)

### Summary
`MultisigFactory::create` is `#[payable]` and forwards `env::attached_deposit()` to a newly created `multisig2` sub-account in a single batched `Promise`, but unlike the sibling factory contracts (`lockup-factory`, `staking-pool-factory`) it has no `.then()` callback to detect failure and return the deposit to the caller. [1](#0-0) 

### Finding Description
`create` builds a single promise batch: `create_account()` → `deploy_contract(CODE)` → `transfer(env::attached_deposit())` → `function_call("new", {members, num_confirmations})`. [2](#0-1) 

If any action in that batch fails — e.g. the target account name already exists, `num_confirmations` is invalid, or `members` fails validation inside `multisig2::new` — the whole receipt fails and the NEAR balance transferred in that receipt is refunded by the NEAR protocol to the **predecessor of that receipt**, which is `multisig-factory` itself (the contract that issued the promise), not the original caller who attached the deposit.

Compare this to `LockupFactory::create` and `StakingPoolFactory::create_staking_pool`, both of which explicitly chain a `.then(ext_self::on_*_create(...))` callback that checks `is_promise_success()` and, on failure, issues `Promise::new(predecessor_account_id).transfer(attached_deposit.0)` to return the funds to the original caller: [3](#0-2) [4](#0-3) 

`MultisigFactory::create` has no equivalent callback at all, so on failure the deposit lands in the factory contract's own balance, with no accounting of whose funds they were and no method exposed to return them to the original depositor.

This mirrors the report's bug class: a payable/value-carrying call whose failure path does not correctly account for or return the attached value, resulting in funds being effectively frozen — here, frozen in the wrong account (the factory) with no bookkeeping and no user-facing withdrawal path, rather than lost to msg.value misuse, but breaking the same "value debited versus value delivered" custody binding.

### Impact Explanation
Any user who calls `create` on `multisig-factory` and whose sub-account creation fails (name collision, invalid `num_confirmations`, malformed `members`, insufficient gas for the `new` call, etc.) permanently loses their attached NEAR deposit to the factory contract, which itself has no method to return or reallocate it. This is a Critical-severity "funds permanently frozen" outcome per the same equality the ERC20 report identifies: value sent in versus value actually delivered/returned diverges, and here there is no recovery mechanism at all (worse than the ERC20 case where funds are at least frozen on the correct contract for the correct depositor).

### Likelihood Explanation
Likelihood is moderate to high: name collisions on `AccountId` prefixes are plausible for a public factory, and `multisig2::new` validation failures (e.g. malformed member lists, invalid `num_confirmations` relative to member count) are easy for a user to trigger accidentally, especially since no client-side pre-validation exists in this contract before it dispatches the promise batch.

### Recommendation
Add a callback analogous to `on_lockup_create` / `on_staking_pool_create`: chain a `.then(ext_self::on_multisig_create(...))` that checks `is_promise_success()` and, on failure, returns `env::attached_deposit()` to `env::predecessor_account_id()`.

### Proof of Concept
1. Call `MultisigFactory::create(name, members, num_confirmations)` attaching `N` NEAR, where `name` collides with an already-existing sub-account (or `num_confirmations`/`members` are invalid such that `multisig2::new` panics).
2. The batched promise (`create_account`/`deploy_contract`/`transfer`/`function_call`) fails.
3. The NEAR protocol refunds the transferred deposit to the receipt's predecessor, `multisig-factory` itself — not to the calling user.
4. `multisig-factory` has no method to detect this refund or return it to the original caller; the `N` NEAR is now permanently stuck in the factory's balance.

### Citations

**File:** multisig-factory/src/lib.rs (L26-49)
```rust
#[near_bindgen]
impl MultisigFactory {
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
