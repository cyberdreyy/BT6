### Title
Front-run of `MultisigFactory::create`'s deterministic sub-account name permanently strands the caller's attached NEAR deposit - (File: `multisig-factory/src/lib.rs`)

### Summary
`MultisigFactory::create` derives the new multisig's account ID deterministically from a caller-supplied `name` (`{name}.{current_account_id}`) and immediately issues a single-receipt promise batch (`create_account` → `deploy_contract` → `transfer` → `function_call`) with no completion callback. [1](#0-0)  Because the target sub-account name is public and predictable, an attacker can pre-create `name.multisig-factory` with a trivial `create()` call of their own, before the legitimate user's transaction with the same `name` lands. The legitimate call's `create_account` action then fails, which fails the whole batched receipt including the `transfer` action — but since `MultisigFactory` never checks the outcome and never forwards a refund to the original predecessor, the deposit is refunded by the NEAR runtime back to the *factory contract's own balance* instead of to the user. The factory exposes no withdrawal/owner function, so that NEAR becomes permanently unrecoverable.

### Finding Description
Compare `MultisigFactory::create` to its sibling factories in the same repo. Both `LockupFactory::create` and `StakingPoolFactory::create_staking_pool` attach a `.then(ext_self::on_..._create(...))` callback that checks `is_promise_success()` and, on failure, explicitly does `Promise::new(predecessor_account_id).transfer(attached_deposit.0)` to return the deposit to the actual caller: [2](#0-1) [3](#0-2) 

`MultisigFactory::create` has no such callback at all: [1](#0-0) 

The account ID it targets, `format!("{}.{}", name, env::current_account_id())`, is fully attacker-predictable from the `name` parameter that will appear in the legitimate user's transaction. Any account (including the attacker) can call `create()` themselves first with the same `name` (using a minimal, storage-covering deposit and an arbitrary member list they control) to reserve `name.multisig-factory` before the legitimate transaction executes. When the legitimate transaction's receipt then attempts `create_account()` on an already-existing account, the entire batched receipt (which also carries the `transfer(env::attached_deposit())` action) fails atomically. The NEAR protocol's automatic refund for a failed receipt returns the transferred balance to the account that originated the receipt — the `MultisigFactory` contract itself — not to the `predecessor_account_id` who actually paid the deposit, since the contract issued no logic to forward it onward. `MultisigFactory` defines no other public method to move funds out of its own balance, so the deposit is now permanently stuck in the factory contract.

The binding broken: `attached_deposit paid by predecessor == NEAR eventually controlled by predecessor` is violated — after a name-collision-induced failure, the predecessor's NEAR ends up custodied by the factory contract with no path back to the depositor.

### Impact Explanation
This matches the "Critical — funds permanently frozen" category: any user's deposit attached to a `create()` call whose sub-account name collides with a pre-existing (or attacker-front-run) account is irrecoverably absorbed into the `MultisigFactory` contract's balance, with no owner, no withdrawal function, and no callback logic to return or otherwise account for it.

### Likelihood Explanation
The attack requires no privilege and negligible cost: the attacker only needs to call `create()` with the desired `name` and a minimal covering deposit before the victim's transaction executes (or simply reuse a `name` a victim is known/likely to pick). No financial cost beyond minimal account-creation storage rent, and no special permissions are needed, matching the "no profit motive required, purely griefing-capable but here escalated to fund loss" pattern from the referenced report, except here it results in actual stuck NEAR rather than mere denial of service.

### Recommendation
Add a callback to `MultisigFactory::create` (mirroring `on_lockup_create` / `on_staking_pool_create`) that checks `is_promise_success()` after the batched promise, and on failure explicitly refunds `env::attached_deposit()` to `env::predecessor_account_id()`, exactly as done in `LockupFactory` and `StakingPoolFactory`. [4](#0-3) [5](#0-4) 

### Proof of Concept
1. Attacker observes/predicts a `name` the victim intends to use (or picks a common one preemptively) and calls `MultisigFactory::create(name, [attacker_member], 1)` with the minimal deposit needed to cover account storage, successfully creating `name.multisig-factory` under attacker control.
2. Victim calls `MultisigFactory::create(name, [victim_members...], threshold)` attaching real NEAR funds intended to seed their multisig.
3. The resulting receipt's `create_account()` action fails because `name.multisig-factory` already exists; the whole batched receipt (including `transfer(attached_deposit)`) fails.
4. Because `create()` has no `.then()` callback, the NEAR protocol's automatic refund credits the transferred amount back to `MultisigFactory`'s own account balance rather than to the victim.
5. `MultisigFactory` exposes no function to move funds out of its own balance, so the victim's attached deposit is permanently stuck in the contract. [6](#0-5)

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
