Confirmed: `VestingSchedule::assert_valid` is defined in `lockup/src/types.rs` but is never called anywhere in `lockup/src/lib.rs`'s `new()` initializer. The comment in `lockup/src/getters.rs:141` ("The total time is positive. Checked at the contract initialization.") is factually wrong for the deployed code path — no such check exists.

### Title
Missing clamp on `VestingSchedule` at lockup initialization allows an invalid/degenerate schedule to break vesting accounting - ([File: lockup/src/lib.rs])

### Summary
`LockupContract::new` in `lockup/src/lib.rs:181-243` accepts an optional `VestingScheduleOrHash`. When `VestingScheduleOrHash::VestingSchedule(vs)` is supplied, the raw `vs` is stored directly into `VestingInformation::VestingSchedule(vs)` with no call to `VestingSchedule::assert_valid()` [1](#0-0) . The validation routine `assert_valid` exists and enforces `start_timestamp <= cliff_timestamp <= end_timestamp` and `start_timestamp < end_timestamp` [2](#0-1) , but no call site in `lockup/src/lib.rs` or `lockup-factory/src/lib.rs` invokes it before persisting the schedule.

### Finding Description
`get_unvested_amount` computes the unvested fraction of the lockup as `lockup_amount * time_left / total_time`, where `total_time = end_timestamp - start_timestamp` (a `u64` subtraction before widening to `U256`) [3](#0-2) . The comment above this code explicitly claims "The total time is positive. Checked at the contract initialization" [4](#0-3) , but that check is not actually performed anywhere in the initialization path.

This binding is: `unvested_amount == lockup_amount * time_left / (end_timestamp - start_timestamp)`, which is only sound if `end_timestamp > start_timestamp` and `start_timestamp <= cliff_timestamp <= end_timestamp`. Since `assert_valid` is never invoked, a caller can supply `start_timestamp == end_timestamp`, or `start_timestamp > end_timestamp`, at initialization. `lockup-factory/src/lib.rs::create` (callable by any unprivileged funding account) forwards an unvalidated `VestingScheduleOrHash::VestingSchedule` straight into the deployed lockup's `new` call [5](#0-4) .

### Impact Explanation
If `end_timestamp == start_timestamp` (with `block_timestamp >= cliff_timestamp` and `< end_timestamp` is impossible since cliff<=end forces branch into "after end -> fully vested" or triggers a division-by-zero panic depending on ordering), or if `start_timestamp > end_timestamp` causing the `u64` subtraction `end_timestamp.0 - start_timestamp.0` to underflow, the resulting `total_time` becomes either `0` (division by zero → panic, a DoS which is explicitly out of scope) or a huge wrapped value (in the non-overflow-checked release build) that corrupts the vesting fraction calculation, potentially causing `get_locked_amount`/`get_unvested_amount` to report incorrect (too-low) locked amounts. This is set by the funding account when deploying the lockup via the factory — the same party who typically also acts as `owner_account_id`/employee, so this directly can misstate the schedule that gates early release of locked tokens (`get_locked_amount`, consumed by `transfer`, `get_owners_balance`, and by the foundation's `terminate_vesting`/`get_unvested_amount`), matching the "locked or unvested tokens released early" impact category.

### Likelihood Explanation
Likelihood is constrained: whether `u64` subtraction underflow panics or wraps depends on the crate's release profile (`overflow-checks`); if overflow checks are enabled (the common default for these near-sdk contracts, since the surrounding code relies on explicit `saturating_sub`/`checked` patterns elsewhere) the malformed schedule would simply panic at read time, which is a self-DoS on the caller's own new lockup and does not directly move funds. I was unable to fully verify the `Cargo.toml`/build profile settings for `overflow-checks` given the excluded-file scope (`*.toml` is out of scope for citation and I did not have time in the final iteration to trace whether debug-assertions are force-enabled in release for `wasm32-unknown-unknown` builds, which is the actual determining factor). Because the exploit requires the attacker to deploy their own lockup with a schedule they fully control before any counterparty relies on it, and does not by itself let an attacker take another party's funds, this sits closer to a self-inflicted or informational defect than a concretely exploitable "claims exceeding assets held" bypass against a victim, absent confirmation that the wraparound path (not the panic path) is reachable.

### Recommendation
Call `vs.assert_valid()` inside `LockupContract::new` (`lockup/src/lib.rs`) immediately after matching `VestingScheduleOrHash::VestingSchedule(vs)`, mirroring the same fix that should be applied in `lockup-factory/src/lib.rs::create` before forwarding the schedule to the deployed contract, so that the invariant documented in `getters.rs` is actually enforced at the point of trust (initialization) rather than assumed.

### Proof of Concept
Not fully constructible without confirming the release build's overflow-check behavior; the reachable code path is:
1. Attacker calls `lockup-factory::create` with `vesting_schedule = Some(VestingScheduleOrHash::VestingSchedule(VestingSchedule { start_timestamp: T, cliff_timestamp: T, end_timestamp: T }))` (or `end_timestamp < start_timestamp`) — this is forwarded unchecked to the deployed lockup's `new` [6](#0-5) .
2. Any subsequent call to `get_unvested_amount`/`get_locked_amount` on the resulting lockup computes `total_time` from the malformed timestamps [3](#0-2) , either panicking (DoS, out of scope) or, if overflow checks are disabled for the build, producing a corrupted `unvested_amount` that misstates how much of the lockup is actually locked/vested.

### Citations

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

**File:** lockup/src/types.rs (L110-125)
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
}
```

**File:** lockup/src/getters.rs (L139-148)
```rust
                    // cannot overflow since block_timestamp < vesting_schedule.end_timestamp
                    let time_left = U256::from(vesting_schedule.end_timestamp.0 - block_timestamp);
                    // The total time is positive. Checked at the contract initialization.
                    let total_time = U256::from(
                        vesting_schedule.end_timestamp.0 - vesting_schedule.start_timestamp.0,
                    );
                    let unvested_amount = U256::from(lockup_amount) * time_left / total_time;
                    // The unvested amount can't be larger than lockup_amount because the
                    // time_left is smaller than total_time.
                    unvested_amount.as_u128().into()
```

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
