## Finding: `join_validator_set_internal` silently inactivates delegators' `pending_inactive` stake without advancing `delegation_pool`'s observed lockup cycle

### Title
Operator rejoin settles expired `pending_inactive` stake into `inactive` outside the epoch-transition path, desynchronizing `delegation_pool`'s lockup-cycle accounting - ([File: aptos-move/framework/aptos-framework/sources/stake.move])

### Summary
`stake::join_validator_set_internal` contains logic that, when an inactive validator rejoins the validator set, unconditionally merges the entire `pending_inactive` balance into `inactive` if the pool's `locked_until_secs` has passed: [1](#0-0) 

This mutates the stake pool's `inactive`/`pending_inactive` split **outside** of `update_stake_pool`/`on_new_epoch`, which is the only settlement path that `delegation_pool::synchronize_delegation_pool` is designed to observe via `calculate_stake_pool_drift`.

### Finding Description
`delegation_pool` tracks its own notion of "lockup cycle ended" by comparing the stake pool's `inactive` amount against `pool.total_coins_inactive`, a value it caches at the end of the last observed synchronization: [2](#0-1) 

The delegation pool's own `observed_lockup_cycle` and its `inactive_shares` table (indexed by that cycle) are only advanced inside `synchronize_delegation_pool`, when `lockup_cycle_ended` is detected: [3](#0-2) 

Normally, `inactive` only increases through the validator's own epoch-boundary settlement (`update_stake_pool`, called from `on_new_epoch`) while the validator is *active* — this always happens in the same transaction/epoch flow that `synchronize_delegation_pool` is called against, so the two stay consistent because delegators/operators always call `synchronize_delegation_pool` before any subsequent action, and the "inactive" jump is only ever produced through the expected reward-cycle path.

The newly added block in `join_validator_set_internal` introduces a **second, independent path** by which `inactive` can jump: an operator calling `join_validator_set` on a currently-inactive stake pool whose lockup has expired will move the *entire* `pending_inactive` balance to `inactive` in that single transaction, bypassing `update_stake_pool`. Because this happens on a stake pool owned by a `delegation_pool` resource account, `total_coins_inactive` on the `DelegationPool` becomes stale until the next `synchronize_delegation_pool` call — but crucially, the delegation pool's `inactive_shares` table for the *current* `observed_lockup_cycle` still holds the pending-inactive shares that now correspond to coins that stake.move has already coalesced into `inactive`, while `pool.observed_lockup_cycle.index` has not yet advanced.

Since `redeem_inactive_shares`/`withdraw_internal` and `can_withdraw_pending_inactive` gate withdrawal eligibility strictly by comparing `withdrawal_olc.index` to `pool.observed_lockup_cycle.index` (i.e., by the delegation pool's *own* bookkeeping, not the stake pool's live state), there is a window (before the next `synchronize_delegation_pool` call) in which:
- The stake pool has already converted delegators' `pending_inactive` into `inactive` (fully withdrawable, no longer earning rewards).
- The delegation pool still believes this stake is in the current OLC's `pending_inactive` shares pool and still accrues (or fails to accrue) rewards/commission against it using the pre-settlement accounting in `calculate_stake_pool_drift`, since `commission_pending_inactive` and `pending_inactive_shares_pool_mut(pool).update_total_coins(...)` are computed from the stale `pool.total_coins_inactive` baseline.

This can be triggered by an **operator**, an unprivileged role relative to delegators' funds, simply by leaving and rejoining the validator set at a chosen moment (waiting for lockup expiry while the pool is inactive, then calling `join_validator_set`), which is an action fully within the operator's normal (non-owner) permission set on a `delegation_pool`.

### Impact Explanation
This breaks the "Accounting across active, pending_active, pending_inactive, inactive, rewards, and commission state must preserve value and withdrawal rights" invariant. Concretely, delegators' `pending_inactive` shares are settled into on-chain `inactive` coins ahead of the delegation pool's own OLC bookkeeping, so `calculate_stake_pool_drift`'s commission and reward attribution for the pending_inactive tranche can be computed against a mismatched stake-pool/pool state (stale `total_coins_inactive`), and delegators' `get_pending_withdrawal`/`withdraw` results temporarily diverge from actual withdrawable value until `synchronize_delegation_pool` next runs and reconciles `total_coins_inactive`. Depending on timing of calls in between (e.g., `add_stake`/`unlock` by other delegators triggering `synchronize_delegation_pool` mid-cycle with a distorted `commission_pending_inactive`/`commission_active` split), operator commission can be over- or under-credited relative to the true reward split, and a delegator's `pending_inactive` balance can transiently be reported/redeemed incorrectly.

### Likelihood Explanation
Requires the specific state where a delegation pool's stake pool is `INACTIVE` (fell out of / never joined the validator set, or was evicted for falling below minimum stake) with an expired lockup and non-zero `pending_inactive`, and the operator subsequently calls `join_validator_set`. This is a normal recovery operation for a delegation pool operator, not an exotic path, but requires the pool to have gone inactive first (e.g. after eviction for low stake or being kicked from the validator set) — a state reachable in production without special privilege beyond the operator role.

### Recommendation
Do not mutate `stake_pool.inactive`/`pending_inactive` in `join_validator_set_internal` outside of the standard `update_stake_pool` settlement path. Instead, either (a) call the same settlement routine that `on_new_epoch`/`update_stake_pool` uses so all callers (including `delegation_pool::synchronize_delegation_pool`) observe consistent state, or (b) require callers to invoke `delegation_pool::synchronize_delegation_pool` (or equivalent settlement) atomically before/after this mutation so `total_coins_inactive` and `observed_lockup_cycle` are updated in the same transaction as the stake-pool-level settlement.

### Proof of Concept
1. Create a `delegation_pool` at `pool_address`; delegator unlocks stake, moving it to `pending_inactive`.
2. Let the lockup fully expire while the validator is `INACTIVE` (e.g., operator calls `leave_validator_set` or the pool is evicted for falling below minimum stake) — `synchronize_delegation_pool` is not called during this window.
3. Operator calls `stake::join_validator_set(operator, pool_address)`. Because `locked_until_secs` has passed, `join_validator_set_internal` merges the delegator's entire `pending_inactive` into `inactive` directly on `StakePool`.
4. At this point `stake::get_stake(pool_address)` shows `pending_inactive == 0`, `inactive` increased by the delegator's full pending amount, yet `DelegationPool.total_coins_inactive` and `observed_lockup_cycle` are untouched (last set at whatever the state was before step 2).
5. Before anyone calls `synchronize_delegation_pool`, query `delegation_pool::get_pending_withdrawal`/`get_stake` for the delegator: `calculate_stake_pool_drift` computes `lockup_cycle_ended` from the stale `total_coins_inactive`, comparing it to the now-jumped `inactive`, producing a reward/commission split for a lockup transition the delegation pool never itself observed happen at epoch boundary — verify (by comparison against expected values from a normal `unlock`→epoch-driven inactivation flow) that operator commission and/or delegator withdrawable amount reported differs from the value that would result had the same coin movement been driven through `update_stake_pool`/`on_new_epoch`.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1096-1110)
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
        let voting_power = get_voting_power(stake_pool);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1862-1886)
```text
    /// Calculate stake deviations between the delegation and stake pools in order to
    /// capture the rewards earned in the meantime, resulted operator commission and
    /// whether the lockup expired on the stake pool.
    fun calculate_stake_pool_drift(pool: &DelegationPool): (bool, u64, u64, u64, u64) {
        let (active, inactive, pending_active, pending_inactive) = stake::get_stake(get_pool_address(pool));
        assert!(
            inactive >= pool.total_coins_inactive,
            error::invalid_state(ESLASHED_INACTIVE_STAKE_ON_PAST_OLC)
        );
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
