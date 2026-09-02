### Title
Missing re-initialisation guard in `LockupContract::new` allows full state takeover via cross-contract `new` call - ([File: lockup/src/lib.rs])

### Summary
`LockupContract::new` (`lockup/src/lib.rs:181-243`) is annotated with `#[init]` but, unlike every other contract in this repo (`whitelist`, `voting`, `multisig`, `staking-pool`, `staking-pool-factory`), it never asserts `!env::state_exists()`. An attacker who deploys their own contract can issue a `Promise::function_call` targeting `new` on an already-initialized victim lockup account (no access key required for cross-contract calls), overwriting `owner_account_id`, `lockup_information`, `vesting_information`, and `staking_information` in place.

### Finding Description
The invariant that should hold is: `state_exists(lockup_account) == false` must be true immediately before `LockupContract::new` executes; i.e. initialization happens exactly once. In this codebase every other `#[init]` method enforces this explicitly, e.g. `whitelist/src/lib.rs:34` (`assert!(!env::state_exists(), "Already initialized")`), and similar checks exist in `multisig/src/lib.rs`, `staking-pool/src/lib.rs`, `staking-pool-factory/src/lib.rs`, `voting/src/lib.rs`, `lockup-factory/src/lib.rs`. `lockup/src/lib.rs:181-243` (`LockupContract::new`) contains no such assertion: [1](#0-0) 

The `#[init]` macro in `near-sdk = "3.1.0"` (per `lockup/Cargo.toml`) does not itself forbid calling an init method again on an account that already holds state — the guard is expected to be added by contract authors, as is done everywhere else in this repo but omitted here.

Exploit path: lockup accounts are subaccounts with no access key (they are created purely via `Promise::create_account().deploy_contract(...).function_call("new", ...)` from `lockup-factory/src/lib.rs:136-157`). A regular NEAR account therefore cannot re-call `new` directly via a signed transaction against that account without a key. However, NEAR permits any contract to invoke any *public* method on any other account via a cross-contract `Promise::function_call`, with no access-key requirement for such calls. An unprivileged attacker can therefore deploy their own contract (allowed per the attacker capability rules) that issues `Promise::new(victim_lockup_account_id).function_call(b"new".to_vec(), attacker_controlled_args, 0, gas)`, targeting a victim's already-initialized lockup account.

Since `new` has no re-init guard, this call succeeds and fully overwrites state: `owner_account_id` can be set to the attacker's own account, `lockup_duration = 0`, `lockup_timestamp = None`, `transfers_information` set to `TransfersEnabled` with an already-passed timestamp, `vesting_schedule = None` (bypassing any foundation-held vesting/termination rights), and `staking_information` reset to `None`. `lockup_information.lockup_amount` is recomputed as `env::account_balance()` at the moment of the malicious call — i.e., whatever NEAR currently sits in the account, including funds that were never intended for the attacker.

None of the existing guards prevent this: `assert_owner` is not called by `new` (it's the initializer); `assert_self()` is irrelevant here since the call is a normal external function call, not a callback; `is_valid_account_id` only validates syntax of the supplied account IDs, and the attacker supplies a syntactically valid account ID (their own). There is no check tying the caller to the original owner or to the factory.

### Impact Explanation
After the malicious re-init, the attacker becomes `owner_account_id` on the victim's lockup contract with `lockup_duration = 0`, no `lockup_timestamp`, transfers already enabled, and no vesting schedule — i.e., the entire account balance is immediately liquid and unlocked. The attacker (now the "owner") can then call owner methods (e.g. `transfer`, per `lockup/src/owner.rs`) to move the account's NEAR balance out to themselves. This is Critical impact per the rubric: locked/unvested lockup tokens are released to a party never entitled to them, and NEAR leaves the lockup account under an ownership claim the rightful owner never authorised. The attack is repeatable against any lockup contract account deployed via the factory (their account IDs are deterministic and public, being `sha256(owner_account_id)` prefixed), so the blast radius spans every lockup contract deployed by this factory that the attacker can target with such a cross-contract call.

### Likelihood Explanation
The attacker needs only to: (1) know or compute a target lockup account ID (deterministic hash of `owner_account_id`, publicly derivable per `lockup-factory/src/lib.rs:119-121`), and (2) deploy a small contract capable of issuing a `Promise::function_call` to that account's `new` method. Both are within the stated unprivileged attacker capabilities (deploy contracts they control, call any open method). No special balance, key, or privileged role is required, and gas cost is minimal (single low-gas function call). This makes the exploit highly feasible and repeatable across every deployed lockup account.

### Recommendation
Add an explicit re-initialization guard at the start of `LockupContract::new` in `lockup/src/lib.rs`, matching the pattern used elsewhere in the repo:
```rust
assert!(!env::state_exists(), "The contract is already initialized");
```
This must be the very first statement in `new`, before any other logic, so any subsequent call to `new` on an account that already holds `LockupContract` state is rejected.

### Proof of Concept
Using `near-sdk-sim` / `near-workspaces` (per `lockup/tests/spec.rs` conventions):
1. Deploy the lockup factory and call `create(...)` for a legitimate `owner1`, producing lockup account `L` with real funds and legitimate `owner_account_id = owner1`.
2. From a second, attacker-controlled contract account, issue `Promise::new(L).function_call(b"new".to_vec(), json!({ "owner_account_id": "attacker", "lockup_duration": "0", "lockup_timestamp": null, "transfers_information": {"TransfersEnabled": {"transfers_timestamp": "1"}}, "vesting_schedule": null, "release_duration": null, "staking_pool_whitelist_account_id": "whitelist", "foundation_account_id": null }), 0, gas)`.
3. Assert the call **succeeds** (no panic) — binding broken: `state_exists(L) == true` before the call, yet `new` executes anyway instead of panicking.
4. Query `L.get_owner_account_id()` and assert it now equals `"attacker"` instead of `"owner1"`.
5. Have `attacker` call `L.transfer(amount, "attacker")` and assert NEAR balance moves out of `L` to `attacker`, proving funds not entitled to `attacker` were released.

Expected (fixed) behavior: step 2's `function_call("new", ...)` should fail with `"The contract is already initialized"` (or equivalent), and `L`'s state (`owner_account_id`, `lockup_information`, `vesting_information`) must remain unchanged, matching the pre-check binding `state_exists(L) == true ⇒ new() panics`.

### Citations

**File:** lockup/src/lib.rs (L180-243)
```rust
    #[init]
    pub fn new(
        owner_account_id: AccountId,
        lockup_duration: WrappedDuration,
        lockup_timestamp: Option<WrappedTimestamp>,
        transfers_information: TransfersInformation,
        vesting_schedule: Option<VestingScheduleOrHash>,
        release_duration: Option<WrappedDuration>,
        staking_pool_whitelist_account_id: AccountId,
        foundation_account_id: Option<AccountId>,
    ) -> Self {
        assert!(
            env::is_valid_account_id(owner_account_id.as_bytes()),
            "The account ID of the owner is invalid"
        );
        assert!(
            env::is_valid_account_id(staking_pool_whitelist_account_id.as_bytes()),
            "The staking pool whitelist account ID is invalid"
        );
        if let TransfersInformation::TransfersDisabled {
            transfer_poll_account_id,
        } = &transfers_information
        {
            assert!(
                env::is_valid_account_id(transfer_poll_account_id.as_bytes()),
                "The transfer poll account ID is invalid"
            );
        }
        let lockup_information = LockupInformation {
            lockup_amount: env::account_balance(),
            termination_withdrawn_tokens: 0,
            lockup_duration: lockup_duration.0,
            release_duration: release_duration.map(|d| d.0),
            lockup_timestamp: lockup_timestamp.map(|d| d.0),
            transfers_information,
        };
        let vesting_information = match vesting_schedule {
            None => {
                assert!(
                    foundation_account_id.is_none(),
                    "Foundation account can't be added without vesting schedule"
                );
                VestingInformation::None
            }
            Some(VestingScheduleOrHash::VestingHash(hash)) => VestingInformation::VestingHash(hash),
            Some(VestingScheduleOrHash::VestingSchedule(vs)) => {
                VestingInformation::VestingSchedule(vs)
            }
        };
        assert!(
            vesting_information == VestingInformation::None ||
                env::is_valid_account_id(foundation_account_id.as_ref().unwrap().as_bytes()),
            "Foundation account should be added for vesting schedule"
        );

        Self {
            owner_account_id,
            lockup_information,
            vesting_information,
            staking_information: None,
            staking_pool_whitelist_account_id,
            foundation_account_id,
        }
    }
```
