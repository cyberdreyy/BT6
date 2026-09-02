### Title
Unauthenticated `owner_account_id` in `LockupFactory::create` lets any attacker squat a victim's deterministic lockup address and force an unauthorized `VestingInformation::VestingHash` onto it - ([File: lockup-factory/src/lib.rs])

### Summary
`LockupFactory::create` derives the lockup contract's account ID solely from `sha256(owner_account_id)` [1](#0-0) , with no check that the caller is authorized to act on behalf of `owner_account_id`, and no check that the account doesn't yet exist. Any unprivileged caller can pre-create `victim.near`'s deterministic lockup address with `vesting_schedule=Some(VestingScheduleOrHash::VestingHash(attacker_salted_hash))`, which unconditionally sets `foundation_account_id = Some(self.foundation_account_id)` [2](#0-1) , before the real grantor/sponsor ever calls `create` for that owner.

### Finding Description
The binding that should hold is: `VestingInformation` persisted at `lockup_account_id = hex(sha256(owner_account_id))[..20].factory` == the vesting terms the legitimate grantor of `victim.near`'s lockup actually authorized (which, per the premise, is `VestingInformation::None`).

The code path:
1. `create()` only asserts `attached_deposit >= MIN_ATTACHED_BALANCE` [3](#0-2) . There is no check on `env::predecessor_account_id()` relative to `owner_account_id`, and no check that the derived `lockup_account_id` doesn't already exist.
2. `lockup_account_id` is fully deterministic from `owner_account_id` alone [1](#0-0) , so any attacker can precompute the exact address the real grantor will later try to use for `victim.near`.
3. Because `vesting_schedule.is_some()`, `foundation_account` is set to `self.foundation_account_id` automatically [2](#0-1) , regardless of whether the real grant for `victim.near` was ever supposed to include vesting or foundation termination rights.
4. The `Promise::new(lockup_account_id).create_account()...` chain deploys the lockup contract with the attacker-chosen `VestingScheduleOrHash::VestingHash(salted_hash)` embedded in `LockupArgs` [4](#0-3) , which is passed through to `LockupContract::new` and stored as `VestingInformation::VestingHash(hash)`. Since the salt/schedule is never revealed, this vesting term is effectively opaque and controlled only by the attacker.
5. `create_account()` on an already-existing NEAR account fails at the protocol level, so once the attacker has squatted the address, the real grantor's later legitimate `create()` call for the same `owner_account_id` will fail (its `Promise` batch fails), and `on_lockup_create` will simply refund the real grantor's deposit to themselves rather than deploying the intended lockup [5](#0-4) . The squatted account, with attacker-chosen vesting terms and NEAR Foundation termination rights, remains at the canonical address `victim.near` was supposed to use.
6. Ownership control of the squatted lockup (`assert_owner`) still requires `predecessor_account_id == owner_account_id` [6](#0-5) , so the attacker cannot directly steal funds sent there by the real sponsor — but any NEAR later sent to that address (e.g., by a sponsor who mistakenly assumes the canonical/deterministic address is uncontaminated) is governed by the attacker's opaque `VestingInformation::VestingHash` and by `assert_called_by_foundation`/termination flow tied to `self.foundation_account_id` [7](#0-6) , none of which the real grantor agreed to.

No existing guard (`assert_owner`, `assert_called_by_foundation`, `assert_self`, `is_promise_success`) prevents the initial squatting call itself, since `create()` has no authorization check tying `owner_account_id` to `env::predecessor_account_id()`.

### Impact Explanation
This matches the "an account... deployed with parameters its rightful creator never chose" and "funds permanently frozen" categories. The immediate effect is denial-of-service against the legitimate lockup for `victim.near` (the real grantor's `create()` call fails and their deposit gets refunded, not lost), and any NEAR later sent directly to the squatted deterministic address becomes locked behind an attacker-chosen, undisclosed vesting hash and NEAR Foundation termination authority the real grantor never intended (`VestingInformation::VestingHash` with `Terminating`/`assert_called_by_foundation` flow). This is repeatable for any `owner_account_id` the attacker chooses to front-run, and requires only the `MIN_ATTACHED_BALANCE` (3.5 NEAR) cost per squat attempt, which is refundable/reusable if the account doesn't already exist (attacker just needs to win the race before the real grantor).

### Likelihood Explanation
Feasible for any unprivileged actor: it only requires knowing (or guessing) the intended `owner_account_id` in advance and attaching `MIN_ATTACHED_BALANCE` before the legitimate transaction lands — a straightforward front-running/griefing scenario against publicly known or predictable grant recipients (e.g., published team member accounts). Cost is bounded to the attached deposit, most of which stays locked in the squatted contract (not lost, but tied up in an unauthorized vesting scheme).

### Recommendation
Require the transaction to be authorized by the intended owner, e.g., assert `env::predecessor_account_id() == owner_account_id.as_ref()` (or otherwise require a signed authorization from the owner) before deploying a lockup with vesting/foundation rights, and/or check `env::account_locked_balance`/existing account state before attempting the deterministic `create_account()` so squatting attempts fail loudly rather than silently pre-empting legitimate deployments.

### Proof of Concept
```rust
// lockup-factory/src/lib.rs tests module
#[test]
fn test_squatted_account_gets_unauthorized_vesting_hash() {
    let mut context = VMContextBuilder::new()
        .current_account_id(account_factory())
        .predecessor_account_id(account_near())
        .finish();
    testing_env!(context.clone());

    let mut contract = LockupFactory::new(whitelist_account_id(), foundation_account_id());

    const LOCKUP_DURATION: u64 = 63036000000000000;
    let lockup_duration: WrappedTimestamp = LOCKUP_DURATION.into();

    // Attacker crafts an arbitrary vesting schedule/salt unknown to the real grantor.
    let attacker_schedule = VestingSchedule {
        start_timestamp: to_ts(GENESIS_TIME_IN_DAYS).into(),
        cliff_timestamp: to_ts(GENESIS_TIME_IN_DAYS + 30).into(),
        end_timestamp: to_ts(GENESIS_TIME_IN_DAYS + 365).into(),
    };
    let attacker_hash = VestingScheduleWithSalt {
        vesting_schedule: attacker_schedule,
        salt: b"attacker-secret-salt".to_vec().into(),
    }.hash();
    let vesting_schedule = Some(VestingScheduleOrHash::VestingHash(attacker_hash.into()));

    // Attacker (not `victim.near`, not the foundation) calls create() naming victim as owner,
    // with a real grant intended to be vesting-free (vesting_schedule=None in the legit call).
    context.is_view = false;
    context.predecessor_account_id = String::from("attacker.testnet");
    context.attached_deposit = MIN_ATTACHED_BALANCE; // ntoy(3.5)
    testing_env!(context.clone());
    contract.create(
        account_tokens_owner(), // stands in for "victim.near"
        lockup_duration,
        Some(far_future_ts()),
        vesting_schedule,
        None,
        None,
    );

    context.predecessor_account_id = account_factory();
    context.attached_deposit = ntoy(0);
    testing_env_with_promise_results(context.clone(), PromiseResult::Successful(vec![]));
    contract.on_lockup_create(
        lockup_account(), // == hex(sha256("tokenowner.testnet"))[..20].factory
        MIN_ATTACHED_BALANCE.into(),
        String::from("attacker.testnet"),
    );

    // ASSERT: the squatted account at the canonical address for `victim.near` now holds
    // VestingInformation::VestingHash(attacker_hash) and foundation_account_id = Some(foundation),
    // even though the real intended LockupArgs for victim.near had vesting_schedule = None.
    // (Requires deploying the produced LockupArgs into a LockupContract::new() call and
    // inspecting self.vesting_information / self.foundation_account_id, or asserting via
    // near-sdk-sim/near-workspaces that the account created at `lockup_account()` is not the
    // one the real grantor's subsequent `create(..., vesting_schedule=None, ...)` call produces
    // (which now fails because the account already exists).)
}
```

Note: full end-to-end confirmation that the deployed `LockupContract` state actually stores `VestingInformation::VestingHash(attacker_hash)` and `foundation_account_id = Some(...)` requires either inspecting `lockup/src/lib.rs::new` (not fully read in this session) or running this scenario through `near-workspaces`/`near-sdk-sim` against the compiled `lockup_contract.wasm`, since the factory only serializes `LockupArgs` and dispatches a cross-contract `Promise`; I did not execute the WASM cross-contract call in this session to observe the persisted state directly.

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

**File:** lockup-factory/src/lib.rs (L123-126)
```rust
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

**File:** lockup/src/internal.rs (L110-120)
```rust
    pub fn assert_called_by_foundation(&self) {
        if let Some(foundation_account_id) = &self.foundation_account_id {
            assert_eq!(
                &env::predecessor_account_id(),
                foundation_account_id,
                "Can only be called by NEAR Foundation"
            )
        } else {
            env::panic(b"No NEAR Foundation account is specified in the contract");
        }
    }
```

**File:** lockup/src/internal.rs (L122-128)
```rust
    pub fn assert_owner(&self) {
        assert_eq!(
            &env::predecessor_account_id(),
            &self.owner_account_id,
            "Can only be called by the owner"
        )
    }
```
