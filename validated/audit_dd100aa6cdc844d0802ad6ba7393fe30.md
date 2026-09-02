### Title
`LockupContract::new` is missing the `!env::state_exists()` re-init guard, allowing state to be wiped/rewritten by anyone after initialization - (File: lockup/src/lib.rs)

### Summary
Every other privileged initializer in this repo (`multisig`, `staking-pool`, `staking-pool-factory`, `voting`, `whitelist`, and even `LockupFactory::new`) explicitly asserts `!env::state_exists()` before constructing state, but `LockupContract::new` at `lockup/src/lib.rs` does not. Because `new` is a normal public `#[init]` method with no `assert_owner`/`assert_self`/`state_exists` guard, anyone can send a `FunctionCall` action invoking `new` a second (or Nth) time against an already-initialized lockup account, completely overwriting `owner_account_id`, `lockup_information`, `vesting_information`, `staking_information` and `foundation_account_id`.

### Finding Description
The broken binding: `state_exists(lockup_account) == true` must imply `new(...)` panics ("already initialized"), the same way it does for `staking-pool` (`assert!(!env::state_exists(), "Already initialized")` at staking-pool/src/lib.rs:179), `multisig` (multisig/src/lib.rs:104), `staking-pool-factory` (staking-pool-factory/src/lib.rs:106), `voting` (voting/src/lib.rs:36), `whitelist` (whitelist/src/lib.rs:34) and `LockupFactory::new` itself (lockup-factory/src/lib.rs:80). In `LockupContract::new` this assertion is absent: [1](#0-0) 

`new` is a plain, publicly callable `#[init]` method taking every parameter from the caller (`owner_account_id`, `lockup_duration`, `lockup_timestamp`, `transfers_information`, `vesting_schedule`, `release_duration`, `staking_pool_whitelist_account_id`, `foundation_account_id`) with no caller-identity check at all — unlike owner methods which call `self.assert_owner()` (e.g. `transfer` at lockup/src/owner.rs:467-486). Since `LockupContract` derives `BorshDeserialize`/`BorshSerialize` and `#[near_bindgen] impl Default` only panics when state doesn't exist (lockup/src/lib.rs:143-147), the `#[init]` macro path used here does not itself re-check existing state — it is entirely up to the developer to add the guard, exactly as done in every sibling contract, and that guard was omitted for `LockupContract`.

Exploit flow: an attacker (or the lockup's own owner wanting to escape restrictions) sends a `FunctionCall` action to the already-deployed, already-initialized lockup account invoking `new` again with attacker-chosen arguments, e.g.:
- `vesting_schedule: None`, `foundation_account_id: None` → wipes `VestingInformation` and removes the NEAR Foundation's termination rights entirely, bypassing `assert_no_termination` forever.
- `transfers_information: TransfersEnabled { transfers_timestamp: 0 }`, `lockup_timestamp: None`, `release_duration: None` → `get_locked_amount` (lockup/src/getters.rs:65-113) then evaluates the "already past lockup" branch with `unreleased_amount = 0` and `unvested_amount = 0`, making `get_owners_balance`/`get_liquid_owners_balance` report the entire account balance as immediately transferable via the still-owner-gated `transfer` method (lockup/src/owner.rs:467-486).
- `owner_account_id` can also be reset to the attacker's own account, hijacking the lockup entirely if the original owner never re-established ownership through some other channel.

`lockup_information.lockup_amount` is recomputed as `env::account_balance()` at call time (line 209), so any staking rewards/extra balance sitting in the account also gets baked into the "fresh" lockup amount, and `termination_withdrawn_tokens` resets to `0`, erasing prior foundation clawback accounting.

None of the existing guards prevent this: `assert_owner`, `assert_self()`, `is_promise_success()`, `assert_transfers_enabled`, `assert_no_termination`, etc. are all methods on `&mut self` invoked from *other* entrypoints — they never run inside `new`, and `new` has no equivalent check of its own.

### Impact Explanation
This is Critical: it lets locked/unvested lockup tokens be released early to a party not (yet) entitled to them, and can also let a party redeploy/re-own a lockup with parameters its rightful creator never chose (resetting `owner_account_id`, deleting `foundation_account_id`/`vesting_information`). It is repeatable against any already-deployed lockup contract (one created by the NEAR Foundation via `LockupFactory::create` for any employee), and the blast radius is every lockup account created by this codebase, since the flaw is in the shared `lockup` wasm deployed by the factory.

### Likelihood Explanation
No special privilege is required — a `FunctionCall` action to any account only requires knowing the account ID and calling a public method; no access key on the target account and no attached deposit condition are enforced by `new`. The precondition is simply that a lockup contract already exists (trivially satisfiable: attacker creates their own lockup through the public `LockupFactory::create`, or targets any existing lockup account whose ID is public on-chain data). Cost is a single transaction's gas.

### Recommendation
Add the same guard used by every sibling contract at the top of `LockupContract::new`:
```rust
assert!(!env::state_exists(), "The contract has already been initialized");
```
in `lockup/src/lib.rs` before any other logic in `new` (around line 190).

### Proof of Concept
```rust
// lockup/src/lib.rs (tests module)
#[test]
#[should_panic(expected = "already been initialized")]
fn test_reinit_blocked_after_fix() {
    let context = basic_context();
    testing_env!(context.clone());
    let _contract = new_contract(true, None, None, false);
    // Second init call on the same (already-initialized) storage.
    testing_env!(context.clone());
    let _contract2 = new_contract(true, None, None, false); // must panic once fix is applied
}
```
Additional `near-sdk-sim`/`near-workspaces` level PoC:
1. Deploy `LockupFactory`, call `create` with a vesting schedule and `foundation_account_id = near_foundation` for an owner account `alice`.
2. Assert `get_vesting_information()` returns `VestingSchedule` and `get_locked_amount()` > 0 pre-exploit (binding LHS: `vesting_information != None`).
3. Submit a `FunctionCall("new", {..., vesting_schedule: null, foundation_account_id: null, transfers_information: TransfersEnabled{transfers_timestamp:0}, lockup_timestamp: null, release_duration: null, ...})` directly to the lockup account (no owner key needed).
4. Assert it succeeds (currently) and `get_vesting_information()` now returns `None`, `get_locked_amount()` returns `0` (binding RHS after: `vesting_information == None`), proving the invariant "`new` runs at most once" is violated and unvested/locked tokens are freed.
5. Call `transfer` as the (possibly reassigned) owner and observe NEAR leave the account despite the original vesting/lockup schedule never having matured — demonstrating the Critical impact.

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
