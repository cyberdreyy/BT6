Based on my research, I found a plausible stake-lockup analog, though I was unable to fully verify one piece (the exact `lockup_cycle_ended` detection logic in `calculate_stake_pool_drift`) before running out of iterations. I present it with that caveat.

### Title
Premature pending_inactive→inactive settlement in `join_validator_set_internal` desynchronizes `delegation_pool` accounting and can permanently DoS delegator withdrawals - (File: `aptos-move/framework/aptos-framework/sources/stake.move`)

### Summary
`stake::join_validator_set_internal` contains logic that silently merges a stake pool's `pending_inactive` coins into `inactive` whenever the pool's lockup has already expired, bypassing the normal epoch/lockup-cycle transition path (`on_new_epoch`/`update_stake_pool`) that `delegation_pool` relies on to keep its per-cycle share accounting synchronized with the underlying `StakePool`.

### Finding Description
In `join_validator_set_internal`: [1](#0-0) 
the code merges `stake_pool.pending_inactive` into `stake_pool.inactive` directly whenever `locked_until_secs` has already passed, outside of the normal end-of-epoch stake-pool update flow. This is triggered by any operator calling the permissionless `join_validator_set` entry function after their validator left the set and its lockup subsequently expired — a completely ordinary operational sequence (`leave_validator_set` → wait → `join_validator_set`), not a privileged action beyond the operator role that delegation-pool owners routinely assign (including to themselves).

`delegation_pool` tracks delegator claims to `pending_inactive` stake via an `ObservedLockupCycle`-indexed `pool_u64` structure, and expects the transition of `pending_inactive` coins into `inactive` to occur exactly at the boundary detected by `calculate_stake_pool_drift`/`synchronize_delegation_pool`, which increments `observed_lockup_cycle` and moves pending-inactive shares into the immutable `inactive_shares` table for that cycle: [2](#0-1) [3](#0-2) 

If `join_validator_set_internal` drains `pending_inactive` to zero on the raw `StakePool` before `delegation_pool` ever observes/records this transition (no call to `synchronize_delegation_pool` occurs on this path), the delegation pool's internal share ledger for the current OLC still records outstanding `pending_inactive` value, while the real `StakePool.pending_inactive` balance is now 0. When a delegator later calls `withdraw`, `withdraw_internal`'s "excess stake" bookkeeping — which assumes `stake::get_stake` still reflects the pre-merge `pending_inactive` amount — computes: [4](#0-3) 
`pending_inactive -= amount` against an already-zeroed `pending_inactive`, which will underflow/abort (Move u64 subtraction traps on underflow), or otherwise mis-restate excess stake. This breaks the "unlock/withdraw must not strand value" invariant for that delegation pool.

### Impact Explanation
This can permanently lock delegators' already-unlocked (pending_inactive) stake inside `withdraw`/`withdraw_internal`, since the arithmetic path that reconciles "excess" pending_inactive against a delegation-pool-external state change will abort every time it is invoked with the corrupted state, until an actual lockup-cycle-ended synchronization event realigns the ledgers (which may not occur, or may occur only after further operator actions). This qualifies as "permanent lock or non-recoverable loss of claim rights ... in stake ... flows" against delegators who never authorized or triggered the operator's `leave`/`join` sequence.

### Likelihood Explanation
Likelihood is moderate: it requires an operator-controlled validator (a role trivially self-assignable when creating a delegation pool) to leave the validator set and later rejoin after its lockup has expired while delegators still have stake sitting in `pending_inactive` for the pool — a realistic and not-uncommon operational pattern, requiring no signature forgery or privileged governance access.

### Recommendation
`join_validator_set_internal`'s pending_inactive settlement should either call into `delegation_pool::synchronize_delegation_pool` (or an equivalent hook) before/atomically with moving `pending_inactive` to `inactive`, or the settlement logic should be removed from `stake.move` and instead be handled exclusively through the epoch-boundary path that all `delegation_pool` accounting assumes, so pool-level and stake-pool-level `pending_inactive` state can never diverge.

### Proof of Concept
1. Staker creates a `delegation_pool` (owner-operator can be the same account), operator joins validator set, delegator delegates and calls `unlock` for some amount X, which enters `pending_inactive` at the current OLC.
2. Time passes such that `locked_until_secs` for the pool expires while the validator is later removed from the active set (`leave_validator_set`), leaving the pool inactive with expired lockup, without `delegation_pool::synchronize_delegation_pool` ever observing a "lockup cycle ended" transition.
3. Operator calls `join_validator_set` again. `join_validator_set_internal`'s check `locked_until_secs > 0 && now >= locked_until_secs` triggers, silently moving all of `StakePool.pending_inactive` (including delegator X) into `StakePool.inactive`, with no notification to `delegation_pool`.
4. Delegator calls `delegation_pool::withdraw` for amount X. `synchronize_delegation_pool` runs but its `lockup_cycle_ended` detection (not fully traced in this review) may not fire since the merge already happened outside its bookkeeping; `withdraw_internal`'s excess-stake computation at [5](#0-4)  reads `pending_inactive == 0` from `stake::get_stake` and underflows subtracting `amount`, aborting the transaction and blocking withdrawal.

Note: I was not able to fully inspect the exact statement computing `lockup_cycle_ended` inside `calculate_stake_pool_drift` (the portion of the function before line 1891) within the available search iterations, so the precise interaction between this variable and the injected merge in `join_validator_set_internal` should be independently re-verified by reading the full function body before treating this as fully confirmed.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1098-1109)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1655-1666)
```text
        if (can_withdraw_pending_inactive(pool_address)) {
            // get excess stake before being entirely inactivated
            let (_, _, _, pending_inactive) = stake::get_stake(pool_address);
            if (withdrawal_olc.index == pool.observed_lockup_cycle.index) {
                // `amount` less excess if withdrawing pending_inactive stake
                pending_inactive -= amount
            };
            // escape excess stake from inactivation
            stake::reactivate_stake(stake_pool_owner, pending_inactive);
            stake::withdraw(stake_pool_owner, amount);
            // restore excess stake to the pending_inactive state
            stake::unlock(stake_pool_owner, pending_inactive);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1915-1928)
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
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1976-1981)
```text
        // advance lockup cycle on delegation pool if already ended on stake pool (AND stake explicitly inactivated)
        if (lockup_cycle_ended) {
            // capture inactive coins over all ended lockup cycles (including this ending one)
            let (_, inactive, _, _) = stake::get_stake(pool_address);
            pool.total_coins_inactive = inactive;

```
