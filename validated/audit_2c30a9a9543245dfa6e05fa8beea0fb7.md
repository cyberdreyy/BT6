Based on my investigation, I found a concrete local analog: a custom-added code path in `join_validator_set_internal` in `stake.move` that moves `pending_inactive` stake into `inactive` outside of the normal epoch-transition (`update_stake_pool`) flow, which is exactly the flow `delegation_pool.move` relies on to keep its own share-accounting in sync with the underlying `StakePool`. [1](#0-0) 

### Title
Delegation pool share accounting desynced from stake pool when operator rejoins an inactive validator with an expired lockup - (File: aptos-move/framework/aptos-framework/sources/stake.move)

### Summary
`join_validator_set_internal` contains logic (absent from the normal `update_stake_pool`/epoch-transition flow) that, when an inactive validator rejoins the validator set with an already-expired lockup, silently drains the `StakePool.pending_inactive` coin bucket into `StakePool.inactive`: [2](#0-1) 

This settlement bypasses `delegation_pool::synchronize_delegation_pool`, which is the only place that detects a lockup cycle ending and correspondingly advances `pool.observed_lockup_cycle`, refreshes `pool.total_coins_inactive`, and opens a fresh `inactive_shares` bucket for the new cycle: [3](#0-2) 

### Finding Description
`delegation_pool.move` tracks delegator claims via `active_shares` and per-OLC (`ObservedLockupCycle`) `inactive_shares`/`pending_inactive` share pools, and only ever refreshes their backing coin totals by reading `stake::get_stake(pool_address)` inside `calculate_stake_pool_drift` / `synchronize_delegation_pool`: [4](#0-3) 

That function only advances `observed_lockup_cycle` and moves `pool.total_coins_inactive` forward when it internally detects `lockup_cycle_ended` — a condition tied to the stake pool's normal reconfiguration-driven inactivation of `pending_inactive` (as coded in `stake::distribute_rewards`, which checks `get_reconfig_start_time_secs() >= current_lockup_expiration` during `end_epoch`): [5](#0-4) 

The added block in `join_validator_set_internal`, however, performs the exact same `pending_inactive -> inactive` merge **outside** that reconfiguration path, triggered instead by any operator simply calling `join_validator_set`/`join_validator_set_internal` on an inactive pool whose lockup has expired. Because the delegation pool never calls `synchronize_delegation_pool` as part of this operator action, its `pending_inactive_shares_pool` for the current OLC still reports nonzero shares against a stake-pool bucket that has just been emptied to zero by `coin::extract_all`, while `pool.total_coins_inactive` and `pool.observed_lockup_cycle` are left stale. The next `synchronize_delegation_pool` call will read `pending_inactive == 0` from `stake::get_stake` and update the pending-inactive shares pool's total coins to a value inconsistent with what delegators are actually owed, without the compensating `observed_lockup_cycle` advance / `inactive_shares` bucket rollover that normally accompanies such a transition.

### Impact Explanation
Delegator shares purchased against the pool's `pending_inactive` bucket at a given `ObservedLockupCycle` become detached from the real coins that back them once this special-cased merge fires. This corrupts delegation-pool share accounting (`pending_inactive_shares_pool` vs. actual `StakePool.pending_inactive`/`inactive`), risking permanent stranding or misattribution of delegator withdrawal rights across the OLC boundary — a "commission/share-accounting corruption that traps value" per the required-impact list.

### Likelihood Explanation
This is reachable by the pool's operator performing an ordinary, expected action (`join_validator_set` to rejoin after being kicked/leaving the validator set) — no owner/admin privilege over delegator funds is required, and the precondition (validator inactive + lockup already expired) is a normal, commonly-occurring state for delegation pools that lose validator-set membership.

### Recommendation
Either (a) remove the special-cased settlement from `join_validator_set_internal` and instead let `update_stake_pool`/reconfiguration handle `pending_inactive` inactivation uniformly, or (b) if this settlement must happen on rejoin, invoke `delegation_pool::synchronize_delegation_pool` (or an equivalent lockup-cycle-advance hook) atomically before/with the coin merge so `observed_lockup_cycle`, `total_coins_inactive`, and the `inactive_shares` table stay consistent with the underlying `StakePool`.

### Proof of Concept
I was not able to fully trace the exact numeric divergence within the remaining tool budget — specifically, I could not pull the full body of `calculate_stake_pool_drift`'s `lockup_cycle_ended` detection logic (only saw lines 1891-1913) to confirm the precise sequence of share-value math after the desync. This should be validated with a concrete Move unit test: create a delegation pool, unlock some delegator stake, let the validator leave the set with an expired lockup, call `join_validator_set` (draining `pending_inactive`→`inactive` via the new code path), then call `synchronize_delegation_pool` and compare `pending_inactive_shares_pool` share value against actual delegator-owed principal to confirm the mismatch.

**Uncertainty note:** Given the index's file-size limits, I could not view `calculate_stake_pool_drift`'s opening lines (its `lockup_cycle_ended` derivation) or `update_stake_pool` in full. I recommend a Devin session with full file access to construct and run the exact PoC test to confirm quantitatively whether delegator funds are stranded, mispriced, or merely delayed-but-recoverable.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1096-1109)
```text
        let config = staking_config::get();
        let (minimum_stake, maximum_stake) = staking_config::get_required_stake(&config);
        // Settle any pending_inactive whose lockup has already expired so it is not counted
        // as voting power. An inactive validator's pending_inactive is never processed by
        // update_stake_pool, so we must do it here before evaluating the minimum stake.
        // Only settle when locked_until_secs > 0 (i.e., a lockup was ever explicitly set);
        // a value of 0 means the pool was just created and the lockup has not been initialised yet.
        if (stake_pool.locked_until_secs > 0
            && timestamp::now_seconds() >= stake_pool.locked_until_secs) {
            coin::merge(
                &mut stake_pool.inactive,
                coin::extract_all(&mut stake_pool.pending_inactive)
            );
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

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1915-1947)
```text
    /// Synchronize delegation and stake pools: distribute yet-undetected rewards to the corresponding internal
    /// shares pools, assign commission to operator and eventually prepare delegation pool for a new lockup cycle.
    public entry fun synchronize_delegation_pool(
        pool_address: address
    ) acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage {
        assert_delegation_pool_exists(pool_address);
        let pool = borrow_global_mut<DelegationPool>(pool_address);
        let (
            lockup_cycle_ended,
            active,
            pending_inactive,
            commission_active,
            commission_pending_inactive
        ) = calculate_stake_pool_drift(pool);

        // zero `pending_active` stake indicates that either there are no `add_stake` fees or
        // previous epoch has ended and should release the shares owning the existing fees
        let (_, _, pending_active, _) = stake::get_stake(pool_address);
        if (pending_active == 0) {
            // renounce ownership over the `add_stake` fees by redeeming all shares of
            // the special shareholder, implicitly their equivalent coins, out of the active shares pool
            redeem_active_shares(pool, NULL_SHAREHOLDER, MAX_U64);
        };

        // distribute rewards remaining after commission, to delegators (to already existing shares)
        // before buying shares for the operator for its entire commission fee
        // otherwise, operator's new shares would additionally appreciate from rewards it does not own

        // update total coins accumulated by `active` + `pending_active` shares
        // redeemed `add_stake` fees are restored and distributed to the rest of the pool as rewards
        pool.active_shares.update_total_coins(active - commission_active);
        // update total coins accumulated by `pending_inactive` shares at current observed lockup cycle
        pending_inactive_shares_pool_mut(pool).update_total_coins(pending_inactive - commission_pending_inactive);
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
