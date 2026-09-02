## Finding [1](#0-0) 

### Title
Multisig Factory `create()` permanently strands attached NEAR deposit on account-creation failure, with no refund path or withdraw function - (File: `multisig-factory/src/lib.rs`)

### Summary
`MultisigFactory::create` attaches the caller's deposit to a batched `create_account` / `deploy_contract` / `transfer` / `function_call` promise but, unlike its sibling factories in the same repository, never registers a callback to detect failure of that promise chain and never exposes any method to recover the deposit. If the underlying account-creation receipt fails for any reason, the NEAR debited from the caller is not returned to them and there is no way, ever, to withdraw it from the factory contract.

### Finding Description
`create()` is the entire implementation surface of the factory - it issues one promise and returns it directly, without a `.then()` callback: [2](#0-1) 

Compare this to the two other factories in the repository that perform the identical "create account + deploy + fund" pattern. Both `lockup-factory` and `staking-pool-factory` explicitly attach a `.then(ext_self::on_..._create(...))` callback whose sole purpose is to detect a failed creation and return the attached deposit to `predecessor_account_id`: [3](#0-2) [4](#0-3) 

`MultisigFactory::create` has no equivalent. If the batched receipt (`create_account`, `deploy_contract`, `transfer`, `function_call`) fails - e.g. because the target `account_id` (`{name}.{multisig-factory}`) already exists, `name` produces an invalid account ID, or the `new` function-call panics - the NEAR runtime refunds the failed action's balance to the *predecessor of that receipt*, which is the `multisig-factory` contract account itself (since the `Promise::new(account_id)` batch was issued by the factory, not by the original caller). The refunded NEAR is absorbed into the factory contract's own balance. Since `MultisigFactory` has no owner, no admin key, and no withdraw/refund method whatsoever, that NEAR is permanently unreachable by the original depositor.

This is the exact bug class from the external report generalized to NEAR: a payable entry point moves value out of the caller's control, an ordinary and easily-triggered failure path leaves value in the contract's custody, and the contract provides no mechanism to reclaim it. The binding broken is: `attached_deposit debited from caller == value delivered to the newly created multisig account`. On failure this equality breaks and the difference is permanently trapped, whereas the sibling factories preserve the equality by refunding on failure.

### Impact Explanation
This matches the Critical impact category "funds permanently frozen": any user's attached deposit (which must cover contract storage plus initial funding, typically several NEAR) can become permanently stuck in the `multisig-factory` contract with zero possibility of recovery, since the contract exposes no owner, no withdraw function, and no state tracking of who is owed what.

### Likelihood Explanation
No privileged access is required. The failure condition (`create_account` failing because the target account id already exists, or is otherwise invalid) is trivial to trigger — including by an unprivileged third party front-running/pre-creating the `{name}.{multisig-factory}` account before the victim's `create()` transaction executes, or simply by two users independently choosing the same `name`. No malicious validator, redeploy, or owner privilege is needed.

### Recommendation
Add a `.then()` callback to `create()` analogous to `on_lockup_create` / `on_staking_pool_create` that checks `is_promise_success()` and, on failure, transfers the attached deposit back to `env::predecessor_account_id()`.

### Proof of Concept
1. Attacker observes/anticipates a pending `multisig-factory` `create` call for `name = "alice"` (or simply races to claim common names).
2. Attacker directly creates the account `alice.<multisig-factory-account>` via a plain `create_account` transaction (no special privilege required), or otherwise causes the account id to already exist.
3. Victim calls `create(name: "alice", members, num_confirmations)` with attached deposit (e.g. 5 NEAR).
4. The batched promise (`create_account`, `deploy_contract`, `transfer`, `function_call`) fails because `create_account` fails on collision.
5. Per NEAR runtime semantics, the attached deposit is refunded to the predecessor of that failed receipt, i.e., the `multisig-factory` contract account — not the victim.
6. `MultisigFactory` contains no callback, no owner, and no withdraw method (confirmed: the entire contract is the 24-line `lib.rs` shown above), so the victim's NEAR is irrecoverable. [5](#0-4)

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

**File:** lockup-factory/src/lib.rs (L158-198)
```rust
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

**File:** staking-pool-factory/src/lib.rs (L186-239)
```rust
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
