No vulnerability found for this question.

**Rationale:**

Both operations in the alleged exploit chain require privileged roles the threat model explicitly excludes:

- `unselect_staking_pool` is gated by `self.assert_owner()` and additionally `self.assert_no_termination()`, meaning it can only be invoked by the lockup's owner, and only when no termination is in progress. [1](#0-0) 

- `terminate_vesting` is gated by `self.assert_called_by_foundation()`, meaning only the NEAR Foundation can start termination. [2](#0-1) 

Since `unselect_staking_pool` asserts `assert_no_termination()`, it cannot run once a termination is already in progress, and `terminate_vesting` can only be triggered by the foundation, not by an unprivileged attacker. There is no reachable sequence in which an unprivileged caller (per the rules: not owner, not foundation) can trigger the `unselect_staking_pool` → `terminate_vesting` ordering described in the question.

Separately, even ignoring privilege, the `Terminating` branch's frozen `unvested_amount` is captured by `get_unvested_amount` at the moment of `terminate_vesting`, which computes the live schedule's unvested value at that exact block timestamp: `unvested_amount = self.get_unvested_amount(vesting_schedule)` [3](#0-2) . Because a linear vesting schedule's unvested amount is monotonically non-increasing in time [4](#0-3) , the frozen snapshot can only be greater than or equal to what a continuously-running live schedule would report afterward — it never releases tokens the live schedule would still lock. `get_locked_amount` further takes `max(unreleased_amount, unvested_amount)` [5](#0-4) , which preserves the invariant rather than breaking it.

Since the required actions are gated behind owner/foundation-only assertions that this threat model excludes, and the mathematical direction of the freeze does not lower the locked amount below what the live schedule would compute, there is no demonstrable path for an unprivileged attacker to exploit this.

### Citations

**File:** lockup/src/owner.rs (L49-60)
```rust
    pub fn unselect_staking_pool(&mut self) {
        self.assert_owner();
        self.assert_staking_pool_is_idle();
        self.assert_no_termination();
        // NOTE: This is best effort checks. There is still some balance might be left on the
        // staking pool, but it's up to the owner whether to unselect the staking pool.
        // The contract doesn't care about leftovers.
        assert_eq!(
            self.staking_information.as_ref().unwrap().deposit_amount.0,
            0,
            "There is still a deposit on the staking pool"
        );
```

**File:** lockup/src/foundation.rs (L15-22)
```rust
    pub fn terminate_vesting(
        &mut self,
        vesting_schedule_with_salt: Option<VestingScheduleWithSalt>,
    ) {
        self.assert_called_by_foundation();
        let vesting_schedule = self.assert_vesting(vesting_schedule_with_salt);
        let unvested_amount = self.get_unvested_amount(vesting_schedule);
        assert!(unvested_amount.0 > 0, "The account is fully vested");
```

**File:** lockup/src/getters.rs (L103-108)
```rust
                return std::cmp::max(
                    unreleased_amount
                        .saturating_sub(self.lockup_information.termination_withdrawn_tokens),
                    unvested_amount.0,
                )
                .into();
```

**File:** lockup/src/getters.rs (L123-150)
```rust
    pub fn get_unvested_amount(&self, vesting_schedule: VestingSchedule) -> WrappedBalance {
        let block_timestamp = env::block_timestamp();
        let lockup_amount = self.lockup_information.lockup_amount;
        match &self.vesting_information {
            VestingInformation::Terminating(termination_information) => {
                termination_information.unvested_amount
            }
            VestingInformation::None => U128::from(0),
            _ => {
                if block_timestamp < vesting_schedule.cliff_timestamp.0 {
                    // Before the cliff, nothing is vested
                    lockup_amount.into()
                } else if block_timestamp >= vesting_schedule.end_timestamp.0 {
                    // After the end, everything is vested
                    0.into()
                } else {
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
                }
            }
```
