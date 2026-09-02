## Title
Lockup schedule rounds *down* the still-locked/unvested amount, letting the owner withdraw or fully unlock tokens ahead of the vesting/release schedule - (File: `lockup/src/getters.rs`)

### Summary
`LockupContract::get_locked_amount` and `LockupContract::get_unvested_amount` compute the amount of tokens still restricted using integer division that truncates toward zero, instead of rounding up. This is the same class of bug as the reported issue: a value that the specification requires to be rounded in the "protective" direction (here, the *locked/unvested* amount, which gates what an unprivileged owner can move) is instead rounded in the direction that favors the party making the withdrawal, letting them claim slightly more than the schedule allows.

### Finding Description
The linear release calculation in `get_locked_amount` computes:
```
let unreleased_amount = U256::from(lockup_amount) * time_left / U256::from(release_duration);
``` [1](#0-0) 

and the vesting calculation in `get_unvested_amount` computes:
```
let unvested_amount = U256::from(lockup_amount) * time_left / total_time;
``` [2](#0-1) 

Both divisions truncate (round down). Since `unreleased_amount`/`unvested_amount` represent the portion of `lockup_amount` that must still remain locked, rounding this quantity down means the reported "still-locked" figure is *less than* the true, real-number linear-schedule value. The remaining fractional yoctoNEAR (up to `release_duration - 1` / `total_time - 1` in relative terms, bounded by `lockup_amount`'s magnitude) is treated as already released/vested, even though the exact schedule says it is still locked.

`get_locked_amount` combines both figures as `max(unreleased_amount, unvested_amount)` [3](#0-2) 
so the final `get_locked_amount` inherits the same downward bias whenever either or both mechanisms are rounded down.

This is directly analogous to the referenced Bond Protocol bug: there, `_currentMarketPrice` rounded down a value that the whitepaper required to round up, letting a taker get tokens cheaper than the correct integer bound. Here, the locked-amount calculation rounds down a value that gates fund custody (the schedule "owes" the protocol/foundation that fraction as still-locked), letting the owner extract or fully unlock funds slightly ahead of schedule.

### Impact Explanation
- `owner::transfer` in `lockup/src/owner.rs` checks `self.get_liquid_owners_balance().0 >= amount.0` before transferring [4](#0-3) 
  and `get_liquid_owners_balance`/`get_owners_balance` are derived from `get_account_balance() - get_locked_amount()` (per README description of the lockup mechanics). Because `get_locked_amount()` under-reports the truly-locked amount, `get_liquid_owners_balance()` over-reports what the owner (an unprivileged party with respect to the NEAR Foundation/vesting grantor) is entitled to withdraw, allowing a transfer of funds that should still be locked.
- `owner::add_full_access_key` requires `self.get_locked_amount().0 == 0` before turning the contract into a normal, unrestricted account [5](#0-4) 
  Rounding down means this check can be satisfied a tiny bit earlier than the exact linear schedule dictates, releasing the entire account (and any dust still contractually locked) prematurely.

This matches the "locked or unvested tokens released early" Critical impact category, since it breaks the equality `recorded liquid/available balance == actual entitlement under the schedule`.

### Likelihood Explanation
Any lockup contract owner (an unprivileged actor from the perspective of the schedule/foundation) can trigger this at any time by calling the standard, public `transfer` or `add_full_access_key` methods once the arithmetic favors them—no special privilege, redeploy, or third party is required. The magnitude of the discrepancy is small per call (bounded by `lockup_amount / release_duration` or `lockup_amount / total_time` truncation, i.e. at most one part in the denominator's precision, generally sub-yoctoNEAR to a few yoctoNEAR in practice given `release_duration`/`total_time` are typically large nanosecond durations), so the value is realistically only a handful of yoctoNEAR per calculation — this is a rounding/precision issue, not a large-value exploit.

### Recommendation
Round the "still locked/unvested" quantities up rather than down, mirroring the fix applied to the referenced bug (`mulDivUp` instead of `mulDiv`):
```rust
// round up: (a + b - 1) / b
let unreleased_amount = (U256::from(lockup_amount) * time_left + U256::from(release_duration) - 1)
    / U256::from(release_duration);
```
and similarly for `get_unvested_amount`'s division by `total_time`. This guarantees the computed locked/unvested amount is never smaller than the true schedule-implied amount, so the owner can never withdraw or unlock more than entitled.

### Proof of Concept
Given `lockup_amount = 1000` yoctoNEAR-equivalent (illustrative), `release_duration = 3` (three indivisible time units), and `time_left = 1` at some block timestamp:
- Real-valued locked amount = `1000 * 1/3 = 333.33...`
- Current code: `unreleased_amount = 1000 * 1 / 3 = 333` (rounded down) — 0.33 units become prematurely "liquid".
- With correct rounding up: `unreleased_amount = (1000*1 + 3 - 1)/3 = 334`, which correctly keeps the fractional remainder locked.

While the per-call leakage is small in absolute terms (bounded by denominator precision), it demonstrates the exact same rounding-direction violation as the referenced report, and it directly breaks the custody binding between the documented linear schedule and the contract's `transfer`/`add_full_access_key` gating logic — this repeats every time these getters are queried as time advances, and compounds each time the owner calls `transfer` at a rounding-favorable instant.

**Note:** I could not fully verify the exact implementation of `get_account_balance`/`get_liquid_owners_balance`/`get_owners_balance` (their bodies were not returned by search), so the precise arithmetic composing "liquid balance" from `get_locked_amount` is inferred from the README and the `transfer`/`add_full_access_key` call sites rather than directly cited from their function bodies. If a Devin session is used to fix this, it should confirm those getter bodies directly in `lockup/src/lib.rs` or `lockup/src/getters.rs`.

### Citations

**File:** lockup/src/getters.rs (L86-92)
```rust
                            let time_left = U256::from(end_timestamp - block_timestamp);
                            let unreleased_amount = U256::from(lockup_amount) * time_left
                                / U256::from(release_duration);
                            // The unreleased amount can't be larger than lockup_amount because the
                            // time_left is smaller than total_time.
                            unreleased_amount.as_u128()
                        }
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

**File:** lockup/src/getters.rs (L140-148)
```rust
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

**File:** lockup/src/owner.rs (L467-487)
```rust
    pub fn transfer(&mut self, amount: WrappedBalance, receiver_id: AccountId) -> Promise {
        self.assert_owner();
        assert!(amount.0 > 0, "Amount should be positive");
        assert!(
            env::is_valid_account_id(receiver_id.as_bytes()),
            "The receiver account ID is invalid"
        );
        self.assert_transfers_enabled();
        self.assert_no_staking_or_idle();
        self.assert_no_termination();
        assert!(
            self.get_liquid_owners_balance().0 >= amount.0,
            "The available liquid balance {} is smaller than the requested transfer amount {}",
            self.get_liquid_owners_balance().0,
            amount.0,
        );

        env::log(format!("Transferring {} to account @{}", amount.0, receiver_id).as_bytes());

        Promise::new(receiver_id).transfer(amount.0)
    }
```

**File:** lockup/src/owner.rs (L502-514)
```rust
    pub fn add_full_access_key(&mut self, new_public_key: Base58PublicKey) -> Promise {
        self.assert_owner();
        self.assert_transfers_enabled();
        self.assert_no_staking_or_idle();
        self.assert_no_termination();
        assert_eq!(self.get_locked_amount().0, 0, "Tokens are still locked/unvested");

        env::log(b"Adding a full access key");

        let new_public_key: PublicKey = new_public_key.into();

        Promise::new(env::current_account_id()).add_full_access_key(new_public_key)
    }
```
