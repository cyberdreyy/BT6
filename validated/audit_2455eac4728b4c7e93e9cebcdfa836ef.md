### Title
`LockupContract::new` lacks a `state_exists` guard, allowing anyone to re-initialise an existing lockup and drain it with `release_duration = Some(0)` - (File: `lockup/src/lib.rs`)

### Summary
`LockupContract::new` (the `#[init]` entrypoint) never checks `env::state_exists()` before writing state, unlike every other contract in this repo (`LockupFactory::new`, `whitelist`, `voting`, `multisig`, `staking-pool`). Because `new` is a plain public function with no self-check, any unprivileged caller can invoke `new` a second time against an already-deployed lockup account, overwrite `owner_account_id`, `vesting_information` and `lockup_information` with attacker-chosen values (including `release_duration = Some(0)`), and immediately unlock all NEAR currently held by the account.

### Finding Description
The broken binding: `LockupContract` state should be written exactly once per account, i.e. `state_write_count(account) == 1` for the account's lifetime. `new` violates this because it performs no equivalent of:
```rust
assert!(!env::state_exists(), "The contract is already initialized");
```
which is present in every sibling contract's `new`, e.g.: [1](#0-0) 

but is absent from the lockup contract's own `#[init]` method: [2](#0-1) 

`near-sdk` 3.1.0's `#[init]` macro (used by this crate, per `lockup/Cargo.toml`) does not insert a `state_exists` check automatically — that is why every other contract in the repo (`lockup-factory`, `staking-pool-factory`, `whitelist`, `voting`, `multisig`) has to add it manually, and the lockup contract's omission is an outlier, not standard macro behavior.

Because `new` has no `assert_self()` or owner check, calling it does not require any key on the target account — a bare `FunctionCall` action addressed to the existing lockup account is sufficient. The attacker (unprivileged, no key on the victim account) sends a transaction:
```
FunctionCall(lockup_account_id, "new", {
  owner_account_id: <attacker_account>,
  lockup_duration: 0,
  lockup_timestamp: None,
  transfers_information: TransfersEnabled { transfers_timestamp: <past> },
  vesting_schedule: None,
  release_duration: Some(0),
  staking_pool_whitelist_account_id: <whitelist>,
  foundation_account_id: None
})
```
Since `lockup_amount = env::account_balance()` is read fresh at call time: [3](#0-2) 
the newly written `LockupInformation` captures the account's *entire current balance* (previously-locked + unvested funds) as the "lockup_amount" of a lockup that is already fully released (`release_duration = Some(0)`, transfers already enabled, no vesting). `owner_account_id` is now the attacker's account, and `staking_information` is reset to `None`, discarding any prior staking bookkeeping. With `get_locked_amount`/`get_liquid_owners_balance` now returning the full balance as liquid (because release completes instantly), the attacker — now recorded as `owner_account_id` — can call the owner's `transfer` method to move out the funds. This is not prevented by any of the standard guards: there is no `assert_self()`, no `assert_owner()` (the caller isn't yet the owner, but `new` lets them *become* the owner), no `assert_one_yocto`, and `VestingSchedule::assert_valid` is irrelevant since `vesting_schedule: None` bypasses it entirely.

### Impact Explanation
This lets an unprivileged attacker seize ownership of any existing lockup contract and release/withdraw NEAR that was never entitled to them — a direct instance of "locked/unvested lockup tokens released to a party not entitled to them," matching the Critical impact category (NEAR moved out of a lockup by a party not entitled to it). The blast radius is every deployed lockup contract account on the network, and the attack is repeatable against any lockup as long as its current balance is nonzero at the moment of the second `new` call.

### Likelihood Explanation
No special preconditions are required beyond knowing the target lockup account ID (public information) and being able to send a NEAR function call — well within the defined "unprivileged attacker" capability set. Attacker cost is a single low-gas transaction (`new` only requires ~25 TGas per its doc comment). This makes the exploit trivially and repeatably executable against any live lockup account.

### Recommendation
Add `assert!(!env::state_exists(), "The contract is already initialized");` as the first line of `LockupContract::new`, mirroring `LockupFactory::new` and the other contracts in this repo.

### Proof of Concept
```rust
#[test]
#[should_panic(expected = "The contract is already initialized")]
fn test_cannot_reinitialize_lockup() {
    let mut context = basic_context();
    testing_env!(context.clone());
    let _contract = LockupContract::new(
        account_owner(),
        0.into(),
        None,
        TransfersInformation::TransfersEnabled {
            transfers_timestamp: to_ts(GENESIS_TIME_IN_DAYS).into(),
        },
        None,
        None,
        AccountId::from("whitelist"),
        None,
    );

    // Simulate a second `new` call from an unprivileged attacker account
    // against the SAME lockup contract account (state already exists).
    let attacker_owner_id = AccountId::from("attacker");
    let _attacker_contract = LockupContract::new(
        attacker_owner_id.clone(),
        0.into(),
        None,
        TransfersInformation::TransfersEnabled {
            transfers_timestamp: to_ts(GENESIS_TIME_IN_DAYS).into(),
        },
        None,
        Some(0.into()), // release_duration = Some(0): instantly fully released
        AccountId::from("whitelist"),
        None,
    );
    // Assert-before: owner_account_id == account_owner()
    // Assert-after (bug): owner_account_id == attacker_owner_id, get_locked_amount() == 0
    // Expected (fixed): panics with "The contract is already initialized" before reaching here.
}
```
The equality to check on both sides: `contract.owner_account_id == account_owner()` before the second `new`, versus `contract.owner_account_id == attacker_owner_id` and `contract.get_locked_amount().0 == 0` after — demonstrating the invariant break. A `near-sdk-sim`/`near-workspaces` variant should additionally show the attacker calling `transfer` post re-init and successfully withdrawing the balance.

### Citations

**File:** lockup-factory/src/lib.rs (L75-90)
```rust
    #[init]
    pub fn new(
        whitelist_account_id: ValidAccountId,
        foundation_account_id: ValidAccountId,
    ) -> Self {
        assert!(!env::state_exists(), "The contract is already initialized");
        assert!(
            env::current_account_id().len() <= 23,
            "The account ID of this contract can't be more than 23 characters"
        );

        Self {
            whitelist_account_id: whitelist_account_id.into(),
            foundation_account_id: foundation_account_id.into(),
        }
    }
```

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
