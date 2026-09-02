## Title
Attached deposit permanently stuck in `MultisigFactory` on failed multisig deployment - (`multisig-factory/src/lib.rs`)

### Summary
`MultisigFactory::create` forwards the caller's `attached_deposit` into a single batched cross-contract promise (`create_account` → `deploy_contract` → `transfer` → `function_call`) but never attaches a `.then()` callback to check the outcome, unlike the sibling `lockup-factory` and `staking-pool-factory` contracts, which both implement an `on_*_create` callback that refunds the deposit to the original caller if deployment fails.

### Finding Description
`MultisigFactory::create` is `#[payable]` and transfers the entire `env::attached_deposit()` as part of the same batched promise used to create and initialize the new multisig account: [1](#0-0) 

If any action in that batch fails — e.g. the target account name already exists, the `new` constructor panics because `members.len() < num_confirmations` (see `multisig2/src/lib.rs` `new`, which asserts this), or gas is insufficient — the entire batched receipt fails atomically. At the protocol level, the attached balance for that failed receipt is refunded to its immediate predecessor, which is the `MultisigFactory` contract account itself, **not** the original end user who called `create`.

Compare this to `LockupFactory::create` and `StakingPoolFactory::create_staking_pool`, both of which capture `env::predecessor_account_id()` and `env::attached_deposit()` and pass them to a callback (`on_lockup_create` / `on_staking_pool_create`) that explicitly checks `is_promise_success()` and, on failure, issues `Promise::new(predecessor_account_id).transfer(attached_deposit.0)` to return the funds to the caller: [2](#0-1) [3](#0-2) 

`MultisigFactory::create` has no equivalent callback, and the contract exposes no withdraw/owner/sweep function of any kind — the entire source file is only 52 lines with no other public methods: [4](#0-3) 

This breaks the custody binding: `attached_deposit sent by caller == value delivered to the new multisig OR refunded to caller`. On any deployment failure, the deposit is refunded into the factory's own account balance, where it has no owner claim path and is permanently unrecoverable — this is the exact "unrecoverable" factory scenario the external report describes for missing value-forwarding.

### Impact Explanation
This matches the Critical impact category "funds permanently frozen." Any user whose `create` call fails after the deposit has already been debited (e.g., due to a naming collision race, a bad `num_confirmations`/`members` combination, or insufficient gas causing the constructor to panic) loses their attached NEAR permanently, with no code path in the contract to recover it.

### Likelihood Explanation
No privileged access is required. An ordinary caller triggering `create` with a duplicate account name, or with `members.len() < num_confirmations` (which is only validated inside the deployed `multisig2::new` constructor, not by the factory before sending the deposit), is enough to reproduce fund loss. This is a straightforward, unprivileged-attacker (or even accidental-user) path.

### Recommendation
Add an `on_create` callback to `MultisigFactory::create` mirroring the pattern used in `lockup-factory`/`staking-pool-factory`: capture `predecessor_account_id` and `attached_deposit` before dispatching the batch, chain a `.then()` callback, and on `!is_promise_success()` refund the deposit via `Promise::new(predecessor_account_id).transfer(attached_deposit)`.

### Proof of Concept
1. Call `MultisigFactory::create` with `members` whose length is less than `num_confirmations` (or reuse an already-taken `name`), attaching a NEAR deposit. [1](#0-0) 
2. The batched promise (`create_account`, `deploy_contract`, `transfer`, `function_call("new", ...)`) is sent; the `new` constructor in `multisig2::new` asserts `members.len() >= num_confirmations` and panics. [5](#0-4) 
3. The whole batched receipt fails atomically; the attached deposit is refunded to the `MultisigFactory` contract account (the predecessor of the failed receipt), not to the caller, because `create` has no `.then()` callback.
4. The caller's NEAR remains inside `MultisigFactory`'s balance indefinitely, since the contract exposes no function to withdraw or return it.

### Citations

**File:** multisig-factory/src/lib.rs (L1-49)
```rust
use near_sdk::borsh::{self, BorshDeserialize, BorshSerialize};
use near_sdk::json_types::Base58PublicKey;
use near_sdk::serde::{Deserialize, Serialize};
use near_sdk::serde_json::json;
use near_sdk::{env, near_bindgen, AccountId, Promise};

#[global_allocator]
static ALLOC: near_sdk::wee_alloc::WeeAlloc<'_> = near_sdk::wee_alloc::WeeAlloc::INIT;

const CODE: &[u8] = include_bytes!("../../multisig2/res/multisig2.wasm");

/// This gas spent on the call & account creation, the rest goes to the `new` call.
const CREATE_CALL_GAS: u64 = 50_000_000_000_000;

#[derive(Serialize, Deserialize)]
#[serde(crate = "near_sdk::serde", untagged)]
pub enum MultisigMember {
    AccessKey { public_key: Base58PublicKey },
    Account { account_id: AccountId },
}

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

**File:** multisig2/src/lib.rs (L147-153)
```rust
    #[init]
    pub fn new(members: Vec<MultisigMember>, num_confirmations: u32) -> Self {
        assert(
            members.len() >= num_confirmations as usize,
            "Members list must be equal or larger than number of confirmations",
        );
        let mut multisig = Self {
```
