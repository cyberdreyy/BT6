This confirms the design: `create` is explicitly intended to be permissionless — "This contract deploys lockup contracts. It allows any user to create and fund the lockup contract... No need to have access to the foundation keys to create lockup" [1](#0-0) .

### Title
Front-running/squatting `LockupFactory::create` permanently strips `foundation_account_id` from a victim's intended vesting lockup address - ([File: lockup-factory/src/lib.rs])

### Summary
`LockupFactory::create` derives the lockup account deterministically as `sha256(owner_account_id)[..20].<factory>` and is callable by anyone with `MIN_ATTACHED_BALANCE`, with no restriction tying the call to a particular predecessor or to whether a vesting schedule is intended. An unprivileged attacker can call `create(owner_account_id=victim, vesting_schedule=None, ...)` before the legitimate grant-issuing call, permanently occupying the deterministic address with `foundation_account_id=None`, so the intended `Some(foundation_account_id)` grant can never be deployed there.

### Finding Description
The binding claimed to hold is: `LockupArgs.foundation_account_id == Some(foundation_account_id)` whenever the account at `sha256(owner_account_id).<factory>` is meant to be a foundation-issued vesting grant for `owner_account_id`. In `LockupFactory::create`, this is only set when the caller happens to pass `vesting_schedule.is_some()`: [2](#0-1) 

The lockup account id is a pure function of `owner_account_id` and the factory's own account id, with no attacker-independent nonce, predecessor binding, or reservation mechanism: [3](#0-2) 

`create` has no `assert_owner`/foundation check — it is deliberately public, as documented ("It allows any user to create and fund the lockup contract... No need to have access to the foundation keys to create lockup"): [1](#0-0) 

Because NEAR's `create_account` action fails if the target account already exists, whichever `create` call lands first wins the address permanently. If an attacker calls `create(owner_account_id=victim, vesting_schedule=None, ...)` with `MIN_ATTACHED_BALANCE` before the foundation's real grant transaction, the attacker's promise batch (`create_account`+`deploy_contract`+`transfer`+`function_call("new", ...)`) succeeds first, deploying a lockup at that address with `foundation_account_id=None` [4](#0-3) . A subsequent legitimate `create` call for the same `owner_account_id` with `vesting_schedule=Some(...)` will fail at the `create_account` action (account already exists), causing `on_lockup_create` to see `is_promise_success() == false` and refund the caller's deposit, leaving the squatted, foundation-clawback-free lockup permanently in place: [5](#0-4) 

None of the existing guards (`assert_self`, `is_promise_success`, the `MIN_ATTACHED_BALANCE` check) address this — they only guard against a self-call spoof and refund-on-failure; none check that the caller is authorized to decide on `foundation_account_id`, nor do they prevent a rival claim on the same deterministic address before the intended grant is issued.

### Impact Explanation
This matches the Critical category "a lockup deployed with parameters its rightful creator never chose." The foundation's unvested-token clawback path (`foundation_account_id`) is permanently unavailable for that `owner_account_id`, since the address is deterministic and can never be redeployed with different terms once occupied. This is repeatable against any `owner_account_id` known in advance (e.g., publicly known grant recipients) at attacker cost of only `MIN_ATTACHED_BALANCE` (3.5 NEAR) per victim address, and each occupied address is a permanent, irreversible loss of the intended vesting/clawback configuration.

### Likelihood Explanation
The attacker needs no privileges beyond sending a transaction with 3.5+ NEAR attached, and needs only to know or predict the `owner_account_id` intended for a future foundation grant (e.g., publicly announced token-grant recipients, team/investor addresses). The address derivation is fully deterministic and public (`sha256(owner_account_id)[..20].<factory>`), so front-running requires no special information beyond the target account name and general network monitoring/timing to submit before the legitimate call.

### Recommendation
Restrict the ability to set `vesting_schedule`/`foundation_account_id` (or restrict `create` itself for grant-bearing lockups) to a privileged caller (e.g., `assert_eq!(env::predecessor_account_id(), self.foundation_account_id)` when `vesting_schedule.is_some()`), or introduce a reservation/allow-list mechanism keyed by `owner_account_id` so that only the intended grant issuer can create the lockup at that deterministic address, preventing an unprivileged party from squatting it with `vesting_schedule=None` first.

### Proof of Concept
`near-sdk-sim`/unit test plan (extending `lockup-factory/src/lib.rs` test module):
1. Initialize `LockupFactory` with `foundation_account_id`.
2. As attacker predecessor, call `create(owner_account_id=victim, vesting_schedule=None, lockup_duration, ...)` with `attached_deposit = MIN_ATTACHED_BALANCE`; simulate `PromiseResult::Successful` for the create-account batch and call `on_lockup_create` to confirm it returns `true` (lockup deployed with `foundation_account_id=None`).
3. As a different predecessor (simulating the foundation), call `create(owner_account_id=victim, vesting_schedule=Some(...), ...)` for the same `owner_account_id`; because `lockup_account_id` (computed via `sha256(victim)[..20].<factory>`) is identical and already occupied, simulate `PromiseResult::Failed` for the batch and call `on_lockup_create`, asserting it returns `false` and the deposit is refunded to the second predecessor via `Promise::new(predecessor_account_id).transfer(attached_deposit.0)`.
4. Assert: (a) `lockup_account_id` from step 2 and step 3 are equal (same address), (b) the on-chain `LockupArgs.foundation_account_id` at that address remains `None` from step 2 forever, (c) the foundation's grant with `Some(foundation_account_id)` never gets deployed at that address — demonstrating the binding `foundation_account_id == Some(...)` (intended) vs `None` (actual, permanent) diverges.

### Citations

**File:** lockup-factory/README.md (L3-16)
```markdown
This contract deploys lockup contracts. 
It allows any user to create and fund the lockup contract.
The lockup factory contract packages the binary of the 
<a href="https://github.com/near/core-contracts/tree/master/lockup">lockup 
contract</a> within its own binary.

To create a new lockup contract a user should issue a transaction and 
attach the required minimum deposit. The entire deposit will be transferred to 
the newly created lockup contract including to cover the storage.

The benefits: 
1. Lockups can be funded from any account.
2. No need to have access to the foundation keys to create lockup.
3. Auto-generates the lockup from the owner account.
```

**File:** lockup-factory/src/lib.rs (L119-126)
```rust
        let byte_slice = env::sha256(owner_account_id.as_ref().as_bytes());
        let lockup_account_id =
            format!("{}.{}", hex::encode(&byte_slice[..20]), env::current_account_id());

        let mut foundation_account: Option<AccountId> = None;
        if vesting_schedule.is_some() {
            foundation_account = Some(self.foundation_account_id.clone());
        };
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
