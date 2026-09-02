This confirms the vulnerability. `lockup-factory/src/lib.rs::create` uses a `.then(ext_self::on_lockup_create(...))` callback that checks `is_promise_success()` and explicitly refunds the deposit to `predecessor_account_id` if the batch (create_account/deploy/transfer/function_call) fails. [1](#0-0) 

`multisig-factory/src/lib.rs::create` has **no such callback**. It builds an identical action batch — `create_account().deploy_contract(CODE).transfer(env::attached_deposit()).function_call(...)` — directly on the target account and returns the `Promise` with no `.then()` handler and no refund logic. [2](#0-1) 

### Title
Multisig creation onto an existing/funded account strands the attached NEAR deposit with no refund - (File: `multisig-factory/src/lib.rs`)

### Summary
`MultisigFactory::create` schedules a single batched promise (`create_account`, `deploy_contract`, `transfer`, `function_call`) against `{name}.{factory}` without any success/failure callback. If the derived account already exists (e.g., because a prior call already created it, or because someone pre-funded/pre-created that account id), `create_account` fails and the whole receipt fails, but the attached deposit that was included in the same batch's `transfer` action is never returned to the caller, unlike the sibling `lockup-factory`, which explicitly refunds on failure via `on_lockup_create`.

### Finding Description
The binding that should hold is: `attacker_balance_before - attached_deposit == attacker_balance_after` when the creation batch fails, i.e. a failed creation should strand no NEAR (funds return to the caller). In `multisig-factory/src/lib.rs::create` (lines 29-49), the deposit is attached directly to a `transfer` action inside the same batched `Promise` as `create_account`/`deploy_contract`/`function_call`, and the function returns that `Promise` with no `.then(ext_self::...)` callback and no `is_promise_success()`/refund logic at all — contrast with `lockup-factory/src/lib.rs::create`/`on_lockup_create` (lines 136-198), which attaches an explicit callback that inspects `is_promise_success()` and calls `Promise::new(predecessor_account_id).transfer(attached_deposit.0)` when the batch fails.

Exploit flow: an attacker calls `create(name, members, num_confirmations)` with attached deposit `D` twice with identical `name` in the same block (or sequentially). The first call succeeds and creates `{name}.{factory}` funded with `D`. The second call (or any call from a different actor targeting the same `name`, or targeting a `name` whose derived account happens to already exist/be funded for any reason) causes `create_account` to fail because the account already exists; the batched receipt fails, and because there is no callback path to refund, the deposit `D` attached to the failed call is not returned to the caller — it is effectively lost by the caller (stranded), with no state anywhere crediting it back to them. Because `MultisigFactory` itself never held onto or tracked the deposit (it was placed directly in the outgoing action batch), there is no `assert_self()`/`is_promise_success()` guard in this contract that could catch and revert the loss.

### Impact Explanation
The attacker's own deposit `D` (NEAR they attached) is stranded/lost on any failed `create` call, since there is no refund path back to the caller when the underlying batch fails due to `create_account` failing on an existing account. This is fund loss for whoever's transaction fails (which could be the attacker's own funds, or, more importantly, could be weaponized by a griefer: an attacker can front-run/pre-create `{name}.{factory}` cheaply and cause a legitimate user's subsequent `create(name, ...)` call to fail and strand that legitimate user's deposit). This matches the "accounting divergence" framing loosely, but the concrete, demonstrable impact here is deposit loss for the caller of a failed `create`, not a redirection of funds to an unentitled third party, and not a case where "another party settles on" an inflated balance recorded in this contract's own state — `MultisigFactory` holds no persistent balance/accounting state (`MultisigFactory` is a unit struct, see line 24), so there is no on-chain ledger value here that "diverges from reality." The impact is best characterized as fund loss/stranding for the caller of the failed transaction, repeatable by any attacker who front-runs a target account name.

### Likelihood Explanation
Very low cost, fully attacker-controlled: the attacker just needs to call `create` with a `name` that they know (or can force) to already resolve to an existing account, then call it again (or race a victim's call) in the same or later block. No privileged role, keys, or special timing beyond normal transaction submission is required. This is trivially repeatable across any number of account names.

### Recommendation
Add a callback to `multisig-factory/src/lib.rs::create`, mirroring `lockup-factory`'s `on_lockup_create` pattern: after the account-creation batch, chain a `.then(ext_self::on_multisig_create(...))` call that uses `assert_self()` and `is_promise_success()` to detect failure and, on failure, `Promise::new(predecessor_account_id).transfer(attached_deposit)` to return the deposit to the caller.

### Proof of Concept
```rust
// multisig-factory/src/tests.rs (new)
use near_sdk::{testing_env, MockedBlockchain, PromiseResult, VMContext};
// 1. testing_env! with predecessor "alice", attached_deposit = ntoy(5)
// 2. call contract.create("multisig".to_string(), members, 2)
//    -> record alice's balance_before = ntoy(5) deposit spent
// 3. Simulate the created account already existing (e.g. call create twice
//    with the same `name` in the same VMContext block, or directly assert
//    that no `.then()`/callback exists on the returned Promise by inspecting
//    the schema/receipts via near-sdk-sim / near-workspaces sandbox:
//      - deploy multisig-factory
//      - pre-create "multisig.factory" account out of band (simulating a
//        pre-existing funded account)
//      - call `create("multisig", members, 2)` with deposit D from alice
//      - assert final receipt status is Failure (create_account failed)
//      - assert alice's on-chain balance decreased by D and was never
//        refunded (no refund receipt observed), proving D is stranded
// 4. Contrast: run the equivalent scenario against lockup-factory's create/
//    on_lockup_create and observe the explicit refund transfer back to
//    predecessor_account_id, which multisig-factory lacks entirely.
``` [3](#0-2) [4](#0-3)

### Citations

**File:** lockup-factory/src/lib.rs (L107-198)
```rust
    #[payable]
    pub fn create(
        &mut self,
        owner_account_id: ValidAccountId,
        lockup_duration: WrappedDuration,
        lockup_timestamp: Option<WrappedTimestamp>,
        vesting_schedule: Option<VestingScheduleOrHash>,
        release_duration: Option<WrappedDuration>,
        whitelist_account_id: Option<ValidAccountId>,
    ) -> Promise {
        assert!(env::attached_deposit() >= MIN_ATTACHED_BALANCE, "Not enough attached deposit");

        let byte_slice = env::sha256(owner_account_id.as_ref().as_bytes());
        let lockup_account_id =
            format!("{}.{}", hex::encode(&byte_slice[..20]), env::current_account_id());

        let mut foundation_account: Option<AccountId> = None;
        if vesting_schedule.is_some() {
            foundation_account = Some(self.foundation_account_id.clone());
        };

        // Defaults to the whitelist account ID given on init call.
        let staking_pool_whitelist_account_id = if let Some(account_id) = whitelist_account_id {
            account_id.into()
        } else {
            self.whitelist_account_id.clone()
        };

        let transfers_enabled: WrappedTimestamp = TRANSFERS_STARTED.into();
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

**File:** multisig-factory/src/lib.rs (L1-50)
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
}
```
