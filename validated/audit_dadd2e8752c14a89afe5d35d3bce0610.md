### Title
Lockup contract never validates the `VestingSchedule` invariants at initialization, allowing an early/incorrect release of locked tokens - (File: `lockup/src/lib.rs`)

### Summary
`LockupContract::new` (the initializer that both direct callers and `lockup-factory` use to create a lockup) accepts a `VestingScheduleOrHash::VestingSchedule(vs)` variant and stores it directly as `VestingInformation::VestingSchedule(vs)` without ever calling `VestingSchedule::assert_valid()`, even though that validation method exists in the same crate. This mirrors the reported AMM bug class: a configuration structure that drives later financial calculations (here, the vesting release schedule) is accepted at `#[init]` time with no sanity checks on its internal ordering.

### Finding Description
The vesting schedule is defined by three timestamps whose ordering is required for the vesting math to behave correctly: [1](#0-0) 

`VestingSchedule::assert_valid()` enforces `start_timestamp <= cliff_timestamp <= end_timestamp` and `start_timestamp < end_timestamp`. This is the *only* place in the crate that performs this check, and it is a method that must be explicitly invoked — it is not called from anywhere in `Default`/`Deserialize`/construction paths.

In `LockupContract::new`, when a caller supplies a raw `VestingSchedule` (as opposed to a privacy-preserving hash), the schedule is stored as-is with no call to `assert_valid()`: [2](#0-1) 

The only checks performed in `new` are account-ID validity checks and the foundation-account-presence check; there is no `vs.assert_valid()` call anywhere in the function body: [3](#0-2) 

This initializer is not only callable directly, it is also the target of `lockup-factory`, which is explicitly documented as callable by **any unprivileged user** who funds the lockup: [4](#0-3) 

and which forwards a caller-supplied vesting schedule verbatim to `lockup`'s `new`: [5](#0-4) 

The vesting math documented for this contract computes the vested percentage as `(cliff_timestamp - start_timestamp) / (end_timestamp - start_timestamp)` and treats the schedule as strictly increasing in time: [6](#0-5) 

Because `assert_valid()` is never invoked, a caller (the "owner"/funder creating the lockup through `lockup-factory`, or any account calling `new` on a freshly-created lockup account before it's funded/handed off) can pass an out-of-order schedule (e.g. `end_timestamp < start_timestamp`, or `cliff_timestamp` far outside the `[start, end]` range, or `start_timestamp == end_timestamp` causing division issues). This breaks the invariant the release/vesting calculation logic depends on (`end_timestamp - start_timestamp` and `cliff_timestamp - start_timestamp` are assumed non-negative in u64 nanosecond arithmetic), which can make the unvested-amount computation collapse to zero (i.e., the contract treats tokens as fully vested/unlocked) far earlier than intended, or panic depending on how the arithmetic underflows in the (unseen in this scan) internal vesting-amount computation.

### Impact Explanation
This is the same class of bug as the reported finding: unvalidated configuration parameters at instantiation time silently corrupt downstream financial calculations. Here, the corrupted value is the vesting/lockup release schedule rather than an AMM's decimals/market ID, but the consequence maps onto the "Critical" bucket explicitly listed in scope: **locked or unvested tokens released early**. A malformed `VestingSchedule` accepted without validation can cause the vesting logic's linear-release invariant to be violated, letting the lockup owner claim/unlock tokens ahead of the intended (and by-design foundation-approved) schedule.

### Likelihood Explanation
`lockup-factory` is documented to let "any user" create and fund a lockup contract with a custom `vesting_schedule` parameter — this is a normal, unprivileged, permissionless entry point, not one requiring the foundation or a redeploy. Combined with the fact that `assert_valid()` exists in the codebase (showing the developers were aware such validation is needed) but is not wired into the actual `new()` constructor, this is a straightforward, reachable defect rather than a theoretical one.

### Recommendation
Call `vesting_schedule.assert_valid()` inside `LockupContract::new` whenever a raw `VestingScheduleOrHash::VestingSchedule(vs)` is supplied, before storing it as `VestingInformation::VestingSchedule(vs)`. The same should be enforced in `lockup-factory`'s `create` method before it forwards the schedule to the lockup contract's `new`, so malformed schedules are rejected at the earliest point of unprivileged input.

### Proof of Concept
1. Call `lockup-factory`'s `create` (or `lockup`'s `new` directly if reachable pre-funding) supplying:
   `vesting_schedule = { start_timestamp: T, cliff_timestamp: T, end_timestamp: T-1 }` (i.e., `end_timestamp < start_timestamp`).
2. Because `LockupContract::new` never calls `VestingSchedule::assert_valid()` (see `lockup/src/lib.rs:216-233`), the malformed schedule is accepted and stored in `VestingInformation::VestingSchedule`.
3. Any subsequent vesting-percentage/unvested-amount calculation that relies on `end_timestamp - start_timestamp` being positive (per the documented formula in `lockup/README.md:43-55`) will compute an incorrect (likely near-zero "unvested" or wrapped/overflowed) value, causing the contract to treat locked/unvested tokens as available for transfer earlier than the schedule the foundation intended.

**Note on completeness:** I was unable to directly inspect the internal function that computes the unvested/vested amount at runtime (e.g., in `lockup/src/internal.rs` or `lockup/src/getters.rs`) within this scan to show the exact numeric overflow/underflow behavior, nor could I confirm whether `lockup-factory/src/lib.rs`'s `create` function independently calls `assert_valid()` before forwarding to `lockup::new` (a file read attempt failed due to a tool-call error and no further iterations were available). If `lockup-factory` does perform this check independently, the attack surface would be reduced to direct calls to `lockup`'s `new` before the lockup account is otherwise locked down — this should be verified in a follow-up session with full repository access.

### Citations

**File:** lockup/src/types.rs (L97-125)
```rust
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

**File:** lockup/src/lib.rs (L190-234)
```rust
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

```

**File:** lockup-factory/README.md (L1-18)
```markdown
# Lockup Factory Contract

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
4. Refund deposit on errors.

```

**File:** lockup-factory/README.md (L30-34)
```markdown
# Create a new lockup with the given parameters.
near call lockup.nearnet create '{"owner_account_id":"lockup_owner.testnet","lockup_duration":"63036000000000000"}' --accountId funding_account.testnet --amount 50000

# Create a new lockup with the vesting schedule.
near call lockup.nearnet create '{"owner_account_id":"lockup_owner.testnet","lockup_duration":"31536000000000000","vesting_schedule": { "VestingSchedule": {"start_timestamp": "1535760000000000000", "cliff_timestamp": "1567296000000000000", "end_timestamp": "1661990400000000000"}}}' --accountId funding_account.testnet --amount 50000 --gas 110000000000000
```

**File:** lockup/README.md (L43-55)
```markdown
A vesting schedule is described by three timestamps in nanoseconds:
- `start_timestamp` - When the vesting starts. E.g. the start date of employment;
- `cliff_timestamp` - When the first part of lockup tokens becomes vested.
  The remaining tokens will vest continuously until they are fully vested.
  Assume we have a 4-year contract with a 1-year cliff.
  In the first year, nothing is vested, then 25% is vested, then we have linear vesting till the end of the contract.
  25% is the number calculated by the formula:
  ```
  cliff_tokens_percentage = (cliff_timestamp - start_timestamp) / (end_timestamp - start_timestamp)
  ```
- `end_timestamp` -  When the vesting ends.

Once the `cliff_timestamp` passed, the tokens are vested on a pro-rata basis from the `start_timestamp` to the `end_timestamp`.
```
