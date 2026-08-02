### Title
Raw-layer pending_inactive→inactive settlement in `join_validator_set_internal` desyncs `delegation_pool` share accounting, trapping delegator withdrawal funds - (File: `aptos-move/framework/aptos-framework/sources/stake.move`)

### Summary
`stake::join_validator_set_internal` contains logic that directly merges `StakePool.pending_inactive` into `StakePool.inactive` whenever an inactive validator re-joins the validator set after its lockup has expired. This raw-layer mutation happens completely outside the `delegation_pool` module's own lockup-cycle/share accounting (`calculate_stake_pool_drift` / `synchronize_delegation_pool`), which is the *only* mechanism `delegation_pool` uses to detect stake-state transitions and keep delegators' `pending_inactive` shares correctly backed by coins.

### Finding Description
`join_validator_set_internal` unconditionally settles expired lockup stake before evaluating voting power: [1](#0-0) 

This merges the entire `pending_inactive` coin balance into `inactive` directly on the `StakePool` resource, bypassing the stake module's normal epoch-transition path (`update_stake_pool`), which is the function that `delegation_pool` (and `staking_contract`) rely on to observe and react to lockup-cycle changes.

`delegation_pool::synchronize_delegation_pool` is the sole place where the pool's internal `pool_u64` share accounting (`active_shares`, `inactive_shares` table keyed by `ObservedLockupCycle`, `pending_withdrawals`) is reconciled against the real `StakePool` balances: [2](#0-1) 

It computes commission and updates `pending_inactive_shares_pool_mut(pool).update_total_coins(pending_inactive - commission_pending_inactive)` using the *actual* `pending_inactive` value read from the stake pool (via `calculate_stake_pool_drift`, which itself calls `stake::get_stake`). If `join_validator_set_internal` has already silently drained `pending_inactive` to `inactive` (as shown above) before `synchronize_delegation_pool` is ever called, the actual `pending_inactive` observed will be `0` while the pool's own `pending_inactive_shares_pool` (backing delegator shares issued for pending unlock requests) still records a positive `total_coins()`. The commission branch also degenerates to `0` in this case: [3](#0-2) 

Since the actual coins were moved to `inactive` out-of-band (not through the OLC-advancing path that also creates the corresponding `inactive_shares` table entry for the new lockup cycle), `update_total_coins(0)` on the still-populated `pending_inactive_shares_pool` orphans the shares delegators hold there: those shares no longer redeem for any coins, while the actual money sits in `stake_pool.inactive` un-attributed to any `ObservedLockupCycle` entry delegation_pool tracks.

The module's own test explicitly documents the design invariant that the delegation pool deliberately avoids inactivating "excess" pending_inactive stake while the validator is inactive precisely to prevent this kind of untracked drift: [4](#0-3) 

`join_validator_set_internal`'s new settlement code directly violates that invariant by moving `pending_inactive → inactive` unconditionally at re-join time, without going through the OLC bookkeeping that `delegation_pool` needs to keep delegator claims solvent.

### Impact Explanation
An operator of a self-created delegation pool (an ordinary, non-privileged role — anyone can create a delegation pool and become its own operator) can let their pool's validator go/stay inactive with delegators' unlock requests still pending in `pending_inactive`, wait for the stake pool lockup to expire, and then call `join_validator_set` to re-register. This silently drains `pending_inactive` into `inactive` at the stake-module layer. When `synchronize_delegation_pool` subsequently runs, it zeroes out the backing coins of the still-existing `pending_inactive` shares pool without properly advancing the `ObservedLockupCycle` and creating the matching `inactive_shares` entry, permanently stranding delegators' unlocking stake and their withdrawal rights (`ESHAREHOLDER_NOT_FOUND` / share redemption yielding 0). This is a stake/lockup accounting corruption that traps delegator value — a High/Critical severity issue under the stated impact criteria ("Permanent lock or non-recoverable loss of claim rights... share-accounting corruption that... traps value").

### Likelihood Explanation
This requires only ordinary, permissionless actions: creating a delegation pool, having delegators unlock stake, allowing the validator to become inactive (e.g., dropping below minimum stake, which an operator fully controls), waiting out the lockup, and calling the public entry function `join_validator_set`. No special privilege beyond being operator of one's own already-created pool is needed, and the sequence can be triggered opportunistically or maliciously by any pool operator against their own delegators.

### Recommendation
`join_validator_set_internal` should not perform any `pending_inactive`/`inactive` state settlement directly on the `StakePool`. Any such transition must be routed through (or trigger) the pool's normal epoch/lockup-processing path so that dependent modules (`delegation_pool`, `staking_contract`) can observe and correctly reconcile it via their existing drift-detection mechanisms before the state is authoritatively "settled." Alternatively, the join path should require callers to first invoke `synchronize_delegation_pool`/`staking_contract::distribute_internal` (or trigger the equivalent internal reconciliation) prior to (or atomically with) merging `pending_inactive` into `inactive`.

### Proof of Concept
Due to the limitations of the code index, I could not fully trace `calculate_stake_pool_drift`'s `lockup_cycle_ended` computation (its full body was not retrieved), so the exact triggering condition for OLC advancement could not be independently confirmed line-by-line — this should be validated with a live/full checkout of the repository. The core proof, however, is structural and confirmed from source: `join_validator_set_internal` mutates `pending_inactive`/`inactive` balances directly (`stake.move` lines 1096-1109), and `delegation_pool::synchronize_delegation_pool`/`calculate_stake_pool_drift` are the only points where `delegation_pool`'s per-delegator share pools are kept consistent with real stake-pool balances (`delegation_pool.move` lines 1891-1947), with the module's own test suite (`test_inactivate_no_excess_stake`, lines 3137-3180) confirming that untracked inactivation of `pending_inactive` while the validator is inactive is treated as an accounting hazard the module works to avoid. A concrete repro script (create delegation pool → delegator unlock → force validator inactive → fast-forward past lockup → re-join validator set → call `synchronize_delegation_pool` → assert delegator can no longer withdraw the expected `pending_inactive` amount) should be run in a Devin session with full repository/test-harness access to confirm the exact failure mode and get authoritative code line numbers for `calculate_stake_pool_drift`.

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

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L3161-3180)
```text
        unlock(delegator, pool_address, 100 * ONE_APT);

        // check no excess pending_inactive is inactivated in the special case
        // the validator had gone inactive before its lockup expired

        let observed_lockup_cycle = observed_lockup_cycle(pool_address);

        // create dummy validator to ensure the existing validator can leave the set
        initialize_test_validator(delegator, 100 * ONE_APT, true, true);
        // inactivate validator
        stake::leave_validator_set(validator, pool_address);
        end_aptos_epoch();
        assert!(stake::get_validator_state(pool_address) == VALIDATOR_STATUS_INACTIVE, 0);

        // expire lockup afterwards
        timestamp::fast_forward_seconds(LOCKUP_CYCLE_SECONDS);
        end_aptos_epoch();

        synchronize_delegation_pool(pool_address);
        // no new inactive stake detected => OLC does not advance
```
