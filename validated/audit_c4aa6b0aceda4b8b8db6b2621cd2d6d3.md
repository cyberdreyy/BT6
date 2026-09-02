Confirmed: `VestingSchedule::assert_valid()` is defined in `lockup/src/types.rs` but is never called anywhere in `lockup/src/lib.rs` or `lockup-factory/src/lib.rs`. This is a direct analog to the reported bug class: an initialization parameter (the vesting schedule, analogous to `initialPrice`) that has a defined "allowable range" check but that check is never invoked on the path an unprivileged caller controls.

### Title
Unvalidated `VestingSchedule` accepted at lockup initialization allows early release of unvested tokens - (File: `lockup/src/lib.rs`)

### Summary
`LockupContract::new()` accepts an optional, caller-supplied `VestingScheduleOrHash::VestingSchedule(vs)` and stores it directly as `VestingInformation::VestingSchedule(vs)` without ever calling `VestingSchedule::assert_valid()`, even though that validation function exists specifically to guarantee `start_timestamp <= cliff_timestamp <= end_timestamp` and `start_timestamp < end_timestamp`.

### Finding Description
`VestingSchedule::assert_valid()` [1](#0-0)  is the intended "allowable range" check for a vesting schedule — analogous to `MIN_SQRT_RATIO <= initialPrice <= MAX_SQRT_RATIO` in the referenced report. However, `LockupContract::new()` in `lockup/src/lib.rs` only validates account IDs; it never calls `vs.assert_valid()` on the supplied schedule before storing it: [2](#0-1) 

The same unchecked value is forwarded unmodified by `lockup-factory`'s `create()`, which lets any unprivileged caller supply an arbitrary `vesting_schedule` for the lockup contract it funds and deploys: [3](#0-2) 

Because the check is skipped, a caller can set, e.g., `start_timestamp == cliff_timestamp == end_timestamp` (or otherwise degenerate ranges) so that vesting math (`get_unvested_amount`, `get_locked_vested_amount` in `lockup/src/getters.rs`, and the termination accounting in `lockup/src/foundation.rs`) treats the entire vesting amount as immediately vested. This breaks the binding the NEAR Foundation relies on: `unvested_amount` (what foundation should be entitled to reclaim on termination) versus the schedule that is supposed to govern it.

### Impact Explanation
This crosses a schedule-based custody boundary: the vesting schedule is what determines how much of the locked balance the NEAR Foundation can reclaim via `terminate_vesting`/foundation withdrawal logic versus how much the owner may treat as liquid. A degenerate, unchecked schedule allows unvested/locked tokens to be treated as vested and released early to the owner, which matches the Critical impact category ("locked or unvested tokens released early").

### Likelihood Explanation
High likelihood of reachability: any account can call `lockup-factory::create()` (or directly initialize a `lockup` contract) supplying an arbitrary `VestingSchedule` as plain JSON — there is no gatekeeping, multisig, or foundation approval required to pass a malformed schedule through `new()`. No privileged role, redeploy, or social engineering is needed to trigger the missing check; it is purely a missing assertion on a caller-supplied initialization argument, exactly the same bug class as the original `initialPrice` report.

### Recommendation
Call `vesting_schedule.assert_valid()` inside `LockupContract::new()` for `VestingScheduleOrHash::VestingSchedule(vs)` before constructing `VestingInformation::VestingSchedule(vs)`, mirroring the existing (but unused) `VestingSchedule::assert_valid()` function. Optionally also validate hashed vesting schedules once revealed during termination.

### Proof of Concept
1. Call `lockup-factory::create()` (or directly `LockupContract::new()`) with `owner_account_id`, `foundation_account_id` set to the real foundation, and:
```json
"vesting_schedule": { "VestingSchedule": {
  "start_timestamp": "1000",
  "cliff_timestamp": "1000",
  "end_timestamp": "1000"
}}
```
2. `new()` accepts this without any panic since `assert_valid()` is never invoked [2](#0-1) .
3. Subsequent calls to vesting/unvested-amount getters and foundation termination logic operate on a schedule where `start == end`, causing the entire vested/unvested split to degenerate (e.g., 100% vested at any timestamp `>= start_timestamp`), letting the owner access funds that were supposed to remain locked/unvested and reducing what the foundation can reclaim on termination.

### Citations

**File:** lockup-factory/src/types.rs (L97-111)
```rust
impl VestingSchedule {
    pub fn assert_valid(&self) {
        assert!(
            self.start_timestamp.0 <= self.cliff_timestamp.0,
            "Cliff timestamp can't be earlier than vesting start timestamp"
        );
        assert!(
            self.cliff_timestamp.0 <= self.end_timestamp.0,
            "Cliff timestamp can't be later than vesting end timestamp"
        );
        assert!(
            self.start_timestamp.0 < self.end_timestamp.0,
            "The total vesting time should be positive"
        );
    }
```

**File:** lockup/src/lib.rs (L216-233)
```rust
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
```

**File:** lockup-factory/src/lib.rs (L108-153)
```rust
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
```
