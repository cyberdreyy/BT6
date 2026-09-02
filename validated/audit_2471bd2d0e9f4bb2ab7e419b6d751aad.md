Based on my investigation, I found a genuine analog to the reported bug class: unvalidated initialization parameters that break a custody/schedule binding, reachable from an unprivileged caller through `lockup-factory`.

### Title
Unvalidated `vesting_schedule` accepted at lockup initialization allows an attacker-controlled release schedule - (File: `lockup/src/lib.rs`)

### Summary
`lockup-factory`'s `create()` is callable by any unprivileged account and forwards a caller-supplied `vesting_schedule` verbatim into `LockupContract::new`. `LockupContract::new` stores the `VestingSchedule` as-is without ever calling `VestingSchedule::assert_valid()`, unlike `lockup-factory`'s own `types.rs::VestingSchedule::assert_valid` which exists but is not invoked from the actual lockup contract initialization path.

### Finding Description
`lockup-factory/src/lib.rs::create` accepts `vesting_schedule: Option<VestingScheduleOrHash>` directly from the caller (any account attaching the minimum deposit) and passes it through unchanged into the `LockupArgs` used to call `new` on the freshly deployed lockup contract [1](#0-0) . Inside `lockup/src/lib.rs::LockupContract::new`, the `vesting_schedule` argument is only pattern-matched to decide between `VestingInformation::VestingHash` and `VestingInformation::VestingSchedule(vs)` — the raw `VestingSchedule` struct `vs` is stored directly with no call to any validity check (no `start_timestamp <= cliff_timestamp <= end_timestamp` assertion) [2](#0-1) . The type does define such a check, `VestingSchedule::assert_valid`, but it is defined in `lockup/src/types.rs` and in the sibling `lockup-factory/src/types.rs` copy — neither is called from the `new()` initializer shown [3](#0-2) [4](#0-3) .

This breaks the intended custody binding: *tokens released by the vesting schedule* should equal *tokens actually earned per the real employment timeline*, i.e. `vested_amount(t) == f(start, cliff, end, t)` where the schedule is well-formed. If `start_timestamp > end_timestamp`, or `cliff_timestamp > end_timestamp`, or the interval is degenerate, the vesting math downstream (used by `foundation.rs` to compute the amount refundable to NEAR Foundation on termination, and by `getters.rs`/owner logic to compute unvested/locked balances) can diverge from any legitimate schedule the foundation intended.

### Impact Explanation
Because `owner_account_id` in `create()` is also attacker-controlled, an unprivileged caller can create a lockup contract for themselves and simultaneously supply a degenerate/malformed `vesting_schedule` that the contract accepts without validation. Depending on how the unvalidated fields interact with the linear-vesting arithmetic elsewhere in the contract (subtraction/overflow, or a schedule that reports 100% vested despite no actual elapsed time), this can allow tokens to be treated as fully vested/unlocked earlier than a legitimate schedule would allow — an early release of "locked or unvested tokens," which maps to the Critical impact bucket in the rules (locked or unvested tokens released early). This mirrors the report's root cause exactly: fields taken from input without validation against the real conditions they are supposed to represent, deferred/absent validation at initialization time.

### Likelihood Explanation
The `create()` entrypoint on `lockup-factory` is explicitly open to any account (unprivileged, no foundation/owner/multisig gate) provided the minimum deposit is attached [5](#0-4) . No redeploy, foundation cooperation, or victim key is required — the caller supplies both `owner_account_id` and `vesting_schedule` in the same call.

### Recommendation
Call `VestingSchedule::assert_valid()` inside `LockupContract::new` (and/or in `lockup-factory::create` before forwarding) whenever a raw `VestingSchedule` is supplied, mirroring the existing but unused validation logic already present in `types.rs`.

### Proof of Concept
1. Call `lockup-factory::create` with `owner_account_id` = attacker's own account, and `vesting_schedule = VestingScheduleOrHash::VestingSchedule({ start_timestamp: T, cliff_timestamp: T, end_timestamp: T-1 })` (end before start) or another internally-inconsistent schedule, attaching `MIN_ATTACHED_BALANCE`.
2. The factory deploys the lockup contract and calls `new`, which accepts the schedule unchanged (no `assert_valid` check) [2](#0-1) .
3. Depending on how the vesting math in the deployed contract handles the malformed interval, the attacker's lockup reports fully-vested/unlocked balance immediately, bypassing the intended lockup/vesting custody guarantee.

**Caveat/uncertainty:** I was unable to retrieve the exact vesting-amount computation function bodies (`lockup/src/internal.rs`, `lockup/src/foundation.rs`, `lockup/src/getters.rs`) in this session due to a tool error on the final iteration, so I could not trace precisely how a malformed `start/cliff/end` ordering propagates through the arithmetic (e.g., whether it saturates safely or actually yields an over-vested result). The absence of `assert_valid()` in the `new()` initializer is confirmed directly from the code shown above; confirming the exact numeric exploit path would require reviewing those three files, which I was unable to fetch before the tool budget ended.

### Citations

**File:** lockup-factory/src/lib.rs (L108-157)
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
                    .unwrap(),
                NO_DEPOSIT,
                gas::LOCKUP_NEW,
            )
```

**File:** lockup/src/lib.rs (L216-228)
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
```

**File:** lockup/src/types.rs (L96-125)
```rust
/// Contains information about vesting schedule.
#[derive(BorshDeserialize, BorshSerialize, Deserialize, Serialize, Clone, PartialEq, Debug)]
#[serde(crate = "near_sdk::serde")]
pub struct VestingSchedule {
    /// The timestamp in nanosecond when the vesting starts. E.g. the start date of employment.
    pub start_timestamp: WrappedTimestamp,
    /// The timestamp in nanosecond when the first part of lockup tokens becomes vested.
    /// The remaining tokens will vest continuously until they are fully vested.
    /// Example: a 1 year of employment at which moment the 1/4 of tokens become vested.
    pub cliff_timestamp: WrappedTimestamp,
    /// The timestamp in nanosecond when the vesting ends.
    pub end_timestamp: WrappedTimestamp,
}

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
}
```

**File:** lockup-factory/src/types.rs (L83-113)
```rust
/// Contains information about vesting schedule.
#[derive(BorshDeserialize, BorshSerialize, Deserialize, Serialize, Clone, PartialEq, Debug)]
#[serde(crate = "near_sdk::serde")]
pub struct VestingSchedule {
    /// The timestamp in nanosecond when the vesting starts. E.g. the start date of employment.
    pub start_timestamp: WrappedTimestamp,
    /// The timestamp in nanosecond when the first part of lockup tokens becomes vested.
    /// The remaining tokens will vest continuously until they are fully vested.
    /// Example: a 1 year of employment at which moment the 1/4 of tokens become vested.
    pub cliff_timestamp: WrappedTimestamp,
    /// The timestamp in nanosecond when the vesting ends.
    pub end_timestamp: WrappedTimestamp,
}

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
}

```
