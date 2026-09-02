Confirmed: `multisig-factory/src/lib.rs` contains the entire contract — this is the whole `MultisigFactory` implementation, with no callback method and no way to reclaim stuck deposits. [1](#0-0) 

This is a valid analog: unlike `lockup-factory` and `staking-pool-factory`, which both attach an `on_*_create` callback that checks `is_promise_success()` and explicitly refunds the attached deposit to the original caller on failure, `multisig-factory::create` fires a bare `Promise` chain with no callback at all.

### Title
Multisig Factory `create()` permanently locks attached NEAR deposit when target account name is taken - (File: multisig-factory/src/lib.rs)

### Summary
`MultisigFactory::create` builds the destination account ID by simple string concatenation (`{name}.{factory}`) and issues a single batched promise (`create_account` → `deploy_contract` → `transfer` → `function_call("new", ...)`) with the caller's `attached_deposit`. It does not attach any callback to check whether the batch succeeded, unlike the sibling factories `lockup-factory` and `staking-pool-factory`, which both explicitly guard against this failure mode.

### Finding Description
`create()` [2](#0-1)  attaches the caller's deposit (`env::attached_deposit()`, forwarded via `#[payable]`) to a promise batch targeting `account_id`. If `account_id` already exists — whether due to name collision or an attacker front-running/pre-creating that account (e.g., `<name>.<factory>` is guessable and can be created directly by anyone before the victim's transaction lands) — the `create_account` action fails and, per NEAR's atomic per-receipt semantics, the whole batch (including the `transfer` of the attached deposit) fails and the deposit balance is returned to the *predecessor* of that failed receipt, which is the `multisig-factory` contract account itself — not the original caller who attached the NEAR.

Contrast with `staking-pool-factory`, which pre-validates uniqueness on-chain (`self.staking_pool_account_ids.insert(&staking_pool_account_id)` asserting it wasn't already tracked) and always attaches `on_staking_pool_create` to explicitly `Promise::new(predecessor_account_id).transfer(attached_deposit.0)` on failure: [3](#0-2) 

And `lockup-factory`, which similarly attaches `on_lockup_create` with the same refund-on-failure pattern: [4](#0-3) 

`multisig-factory` has no such callback, no uniqueness check, and — since the file shown is the entirety of the contract's source — no other method (owner-only or otherwise) to recover funds stuck on the factory's own balance.

### Impact Explanation
Any user calling `create()` on the multisig factory and attaching NEAR risks having that NEAR become permanently unrecoverable from the factory contract if the target account name is already taken (accidentally or via a trivial front-run of the deterministic `<name>.<factory>` account ID by any unprivileged attacker who simply creates that subaccount first). This is a funds-permanently-frozen condition with no privileged party, redeploy, or victim key required — it is triggerable by any ordinary user's transaction ordering.

### Likelihood Explanation
High. Anyone can pre-create `<name>.<factory>` as a plain account with a full-access key before a victim's `create()` transaction is included, since `name` is fully attacker-observable (mempool/predictable naming) and account creation under an existing top-level/factory namespace requires no permission. No collusion with the foundation, multisig members, or validators is needed.

### Recommendation
Add a callback (`on_create`, mirroring `lockup-factory::on_lockup_create` / `staking-pool-factory::on_staking_pool_create`) that checks `is_promise_success()` and explicitly `Promise::new(env::predecessor_account_id()).transfer(attached_deposit)` back to the caller on failure. Additionally consider deriving a collision-resistant account ID (e.g., via hashing like `lockup-factory` does with `env::sha256`) or asserting uniqueness before issuing the batch.

### Proof of Concept
1. Attacker observes a pending/likely `create()` call to `multisig-factory` for name `alice`.
2. Attacker sends `CreateAccount` for `alice.<factory>` directly (any account can create a subaccount of an account it doesn't control, as long as the name isn't taken), adding a full-access key they control.
3. Victim's `create()` transaction attaching e.g. 50 NEAR executes: `Promise::new("alice.<factory>").create_account()...` — `create_account` fails because the account already exists; per NEAR's atomic receipt semantics, the whole action batch (including the `transfer` of 50 NEAR) fails, and the 50 NEAR is credited back to the deposit-return path terminating at the factory contract's own account balance (the predecessor of the failed receipt), not the victim.
4. Because `multisig-factory` (per [1](#0-0) ) has no `on_create` callback and no other method, the 50 NEAR sits in the factory's balance permanently inaccessible to the victim.

### Citations

**File:** multisig-factory/src/lib.rs (L22-49)
```rust
#[near_bindgen]
#[derive(BorshSerialize, BorshDeserialize, Default)]
pub struct MultisigFactory {}

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
