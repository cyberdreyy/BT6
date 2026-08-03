## Finding: Commission percentage can flip before the lockup cycle it applies to actually ends

The reported issue is real, and the code confirms a genuine desynchronization between two different triggers used inside `synchronize_delegation_pool`.

### Summary
`synchronize_delegation_pool` is a permissionless `public entry fun` (no signer, only takes `pool_address`), so any unprivileged account can call it at any time [1](#0-0) . Inside it, whether the observed lockup cycle (OLC) advances is driven by *actually detected* state change on the stake pool (`inactive > pool.total_coins_inactive`), while whether the *new* commission percentage becomes effective is driven purely by wall-clock time (`timestamp::now_seconds() >= effective_after_secs`). These two triggers are not atomic with each other.

### Finding Description
- `calculate_stake_pool_drift` determines `lockup_cycle_ended` from the stake pool's actual `inactive` coin count, not from time [2](#0-1) .
- `is_next_commission_percentage_effective` is purely time-based: `timestamp::now_seconds() >= effective_after_secs`, where `effective_after_secs` was set to the stake pool's `locked_until_secs` at the time `update_commission_percentage` was called [3](#0-2) [4](#0-3) .
- On the underlying `stake` module, the pending_inactive → inactive move and the `locked_until_secs` renewal only happen inside `on_new_epoch`/reconfiguration, gated by `get_reconfig_start_time_secs() >= current_lockup_expiration` [5](#0-4)  and `stake_pool.locked_until_secs <= reconfig_start_secs` [6](#0-5) . This only executes at epoch boundaries (periodic reconfiguration), not continuously.
- Consequently, once wall-clock time passes the old `locked_until_secs`, there is a window — up to one epoch duration — where `is_next_commission_percentage_effective` is already `true`, but the stake pool has not yet actually inactivated the pending stake and `lockup_cycle_ended` is still `false`.
- In `synchronize_delegation_pool`, `commission_pending_inactive` is calculated first using the *current* `pool.operator_commission_percentage` [7](#0-6) , and OLC advancement is conditioned on `lockup_cycle_ended` [8](#0-7) . Only afterward does the function overwrite `pool.operator_commission_percentage` if `is_next_commission_percentage_effective` is `true` [9](#0-8) .
- If any unprivileged caller invokes `synchronize_delegation_pool` inside that window (time passed `locked_until_secs`, but reconfiguration hasn't fired yet), the current call's commission is computed correctly (old %), but `operator_commission_percentage` is switched to the *new* value while the OLC has **not** advanced and the pending_inactive shares pool is still the *same* pool for the still-open, not-yet-ended lockup cycle. Any further reward accrual/synchronization calls occurring in that same window (before the real epoch-driven inactivation) will then compute `commission_pending_inactive` for that still-open OLC using the *new* percentage instead of the percentage that was actually in force when that pending_inactive stake began accruing.

### Impact Explanation
This corrupts `commission_pending_inactive` for the pending_inactive shares pool of the boundary lockup cycle: rewards that accrued under the old percentage's regime can end up commissioned at the new rate for the remainder of the window. The increase is bounded by `MAX_COMMISSION_INCREASE` (10 percentage points) on the increase side [10](#0-9) , but a decrease is unbounded in the other direction, so the operator commission on a chunk of the boundary cycle's pending_inactive rewards can be miscalculated in either direction, misallocating value between the operator and delegators for the affected reward slice. This is not attacker fund redirection but a genuine accounting-invariant break ("commission across active/pending_inactive state must preserve value") triggerable purely by an unprivileged caller through a normal, permissionless entrypoint.

### Likelihood Explanation
Requires: (1) the operator has an in-flight `NextCommissionPercentage` change scheduled (via `update_commission_percentage`), and (2) a call to `synchronize_delegation_pool` (by anyone) lands in the window between `now_seconds() >= locked_until_secs` and the next reconfiguration's actual state update. Since reconfiguration/epoch intervals are much shorter than the multi-day lockup cycle, this window is narrow relative to the whole cycle but is a normal, recurring, and externally triggerable condition — not an edge case requiring privileged access.

### Recommendation
Gate `is_next_commission_percentage_effective`'s effect on the *same* state signal used for OLC advancement (`lockup_cycle_ended`) rather than on `timestamp::now_seconds()` alone, e.g. only flip `operator_commission_percentage` when `lockup_cycle_ended` is true in that same `synchronize_delegation_pool` call (or otherwise defer application until the OLC has actually advanced), so the commission-rate switch is atomic with the actual pending_inactive→inactive transition it is meant to track.

### Proof of Concept
1. Operator calls `update_commission_percentage` well before lockup end, setting `effective_after_secs = locked_until_secs` (call this `T`).
2. Delegators keep pending_inactive stake accruing rewards in the pool for the current OLC.
3. Time advances past `T`, but no reconfiguration has fired yet (this window can span up to one epoch duration).
4. Any unprivileged account calls `synchronize_delegation_pool(pool_address)`. At this call: `lockup_cycle_ended == false` (verified against `pool.total_coins_inactive`), so OLC does not advance and the pending_inactive shares pool used is still the boundary-cycle pool; but `is_next_commission_percentage_effective` returns `true`, so `operator_commission_percentage` is switched to the new value immediately.
5. Any subsequent reward/sync activity that still lands in the same (not-yet-advanced) OLC before the real reconfiguration event computes `commission_pending_inactive` at the new rate, not the rate that was active while that OLC's pending_inactive stake was accruing — matching the property-test described in the question (`observed_lockup_cycle`/`is_next_commission_percentage_effective` invariant violation right at `LOCKUP_CYCLE_SECONDS`).

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L550-555)
```text
    #[view]
    /// Return whether the commission percentage for the next lockup cycle is effective.
    public fun is_next_commission_percentage_effective(pool_address: address): bool acquires NextCommissionPercentage {
        exists<NextCommissionPercentage>(pool_address) &&
            timestamp::now_seconds() >= borrow_global<NextCommissionPercentage>(pool_address).effective_after_secs
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1298-1304)
```text
        assert!(new_commission_percentage <= MAX_FEE, error::invalid_argument(EINVALID_COMMISSION_PERCENTAGE));
        let owner_address = signer::address_of(owner);
        let pool_address = get_owned_pool_address(owner_address);
        assert!(
            operator_commission_percentage(pool_address) + MAX_COMMISSION_INCREASE >= new_commission_percentage,
            error::invalid_argument(ETOO_LARGE_COMMISSION_INCREASE)
        );
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1315-1326)
```text
        if (exists<NextCommissionPercentage>(pool_address)) {
            let commission_percentage = borrow_global_mut<NextCommissionPercentage>(pool_address);
            commission_percentage.commission_percentage_next_lockup_cycle = new_commission_percentage;
            commission_percentage.effective_after_secs = stake::get_lockup_secs(pool_address);
        } else {
            let delegation_pool = borrow_global<DelegationPool>(pool_address);
            let pool_signer = account::create_signer_with_capability(&delegation_pool.stake_pool_signer_cap);
            move_to(&pool_signer, NextCommissionPercentage {
                commission_percentage_next_lockup_cycle: new_commission_percentage,
                effective_after_secs: stake::get_lockup_secs(pool_address),
            });
        };
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1871-1885)
```text
        // determine whether a new lockup cycle has been ended on the stake pool and
        // inactivated SOME `pending_inactive` stake which should stop earning rewards now,
        // thus requiring separation of the `pending_inactive` stake on current observed lockup
        // and the future one on the newly started lockup
        let lockup_cycle_ended = inactive > pool.total_coins_inactive;

        // actual coins on stake pool belonging to the active shares pool
        active += pending_active;
        // actual coins on stake pool belonging to the shares pool hosting `pending_inactive` stake
        // at current observed lockup cycle, either pending: `pending_inactive` or already inactivated:
        if (lockup_cycle_ended) {
            // `inactive` on stake pool = any previous `inactive` stake +
            // any previous `pending_inactive` stake and its rewards (both inactivated)
            pending_inactive = inactive - pool.total_coins_inactive
        };
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1899-1910)
```text
        // operator `pending_inactive` rewards not persisted yet to the pending_inactive shares pool
        let pool_pending_inactive = pending_inactive_shares_pool(pool).total_coins();
        let commission_pending_inactive = if (pending_inactive > pool_pending_inactive) {
            math64::mul_div(
                pending_inactive - pool_pending_inactive,
                pool.operator_commission_percentage,
                MAX_FEE
            )
        } else {
            // handle any slashing applied to `pending_inactive` stake
            0
        };
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1917-1921)
```text
    public entry fun synchronize_delegation_pool(
        pool_address: address
    ) acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage {
        assert_delegation_pool_exists(pool_address);
        let pool = borrow_global_mut<DelegationPool>(pool_address);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1976-1986)
```text
        // advance lockup cycle on delegation pool if already ended on stake pool (AND stake explicitly inactivated)
        if (lockup_cycle_ended) {
            // capture inactive coins over all ended lockup cycles (including this ending one)
            let (_, inactive, _, _) = stake::get_stake(pool_address);
            pool.total_coins_inactive = inactive;

            // advance lockup cycle on the delegation pool
            pool.observed_lockup_cycle.index += 1;
            // start new lockup cycle with a fresh shares pool for `pending_inactive` stake
            pool.inactive_shares.add(pool.observed_lockup_cycle, pool_u64::create_with_scaling_factor(SHARES_SCALING_FACTOR));
        };
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1988-1993)
```text
        if (is_next_commission_percentage_effective(pool_address)) {
            pool.operator_commission_percentage = borrow_global<NextCommissionPercentage>(
                pool_address
            ).commission_percentage_next_lockup_cycle;
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1448-1464)
```text
            // Automatically renew a validator's lockup for validators that will still be in the validator set in the
            // next epoch.
            let stake_pool = borrow_global_mut<StakePool>(validator_info.addr);
            let now_secs = timestamp::now_seconds();
            let reconfig_start_secs =
                if (chain_status::is_operating()) {
                    get_reconfig_start_time_secs()
                } else {
                    now_secs
                };
            if (stake_pool.locked_until_secs <= reconfig_start_secs) {
                spec {
                    assume now_secs + recurring_lockup_duration_secs <= MAX_U64;
                };
                stake_pool.locked_until_secs = now_secs
                    + recurring_lockup_duration_secs;
            };
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1952-1959)
```text
        // Pending inactive stake is only fully unlocked and moved into inactive if the current lockup cycle has expired
        let current_lockup_expiration = stake_pool.locked_until_secs;
        if (get_reconfig_start_time_secs() >= current_lockup_expiration) {
            coin::merge(
                &mut stake_pool.inactive,
                coin::extract_all(&mut stake_pool.pending_inactive)
            );
        };
```
