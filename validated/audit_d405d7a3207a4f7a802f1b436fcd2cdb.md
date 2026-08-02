### Title
Inconsistent lockup-expiry gating in `join_validator_set_internal` can misclassify already-unlocked `pending_inactive` stake as active voting power - (File: `aptos-move/framework/aptos-framework/sources/stake.move`)

### Summary
`stake::join_validator_set_internal` contains a special-cased guard, `stake_pool.locked_until_secs > 0`, before deciding whether to settle (merge) `pending_inactive` stake into `inactive`. This mirrors the bug pattern in the external report: a heuristic condition on a piece of state (`locked_until_secs == 0` interpreted as "lockup never set") is used to short-circuit an otherwise-correct comparison (`now_seconds() >= locked_until_secs`), and that heuristic is not applied consistently everywhere else lockup expiry is checked in the same file. [1](#0-0) 

### Finding Description
In `join_validator_set_internal`, before evaluating whether the pool has enough/too much voting power to (re)join the validator set, the code attempts to "settle" any `pending_inactive` stake whose lockup has already expired, moving it into `inactive` so it does not count toward voting power: [2](#0-1) 

The settlement is gated by `stake_pool.locked_until_secs > 0`. Elsewhere in the same module, the analogous settlement performed in `withdraw_with_cap` uses only `timestamp::now_seconds() >= stake_pool.locked_until_secs` with **no** `> 0` guard: [3](#0-2) 

This means the two lockup-settlement code paths in `stake.move` disagree on how to treat a pool whose `locked_until_secs` is still `0` (i.e., a pool that has `pending_inactive` stake but has never had a lockup period explicitly started). `withdraw_with_cap` will treat `0` as "already expired" (since any `now_seconds() >= 0`) and immediately merge `pending_inactive` into `inactive`, allowing withdrawal. `join_validator_set_internal`, by contrast, will skip the merge entirely in this state, leaving that `pending_inactive` balance un-settled when computing `get_voting_power` for the minimum/maximum stake checks used to admit the validator into `pending_active`.

Because the two functions use different rules for the same state field, the stake accounting used for validator-set admission (`join_validator_set_internal`) can diverge from the actual withdrawability state used by `withdraw_with_cap`, meaning a delegator/owner's `pending_inactive` position may not be reflected consistently for the min/max stake gate that determines eligibility to enter the validator set.

### Impact Explanation
If `pending_inactive` stake is treated differently by the two functions, an owner/operator could exploit the discrepancy around the `locked_until_secs == 0` edge case to influence the voting-power calculation used to gate entry into the validator set (`ESTAKE_TOO_LOW` / `ESTAKE_TOO_HIGH` checks), which is unprivileged, reachable code (`join_validator_set` is an operator-callable entry function). This falls under "Accounting across active, pending_active, pending_inactive, inactive... state must preserve value and withdrawal rights" per the stake/lockup invariant category. However, I could **not** fully confirm within the available tool budget whether `get_voting_power` actually includes `pending_inactive` in its computation, nor whether `unlock_with_cap` can be invoked on a pool that has never joined the validator set (and thus never had `locked_until_secs` set) — both are necessary preconditions for the discrepancy to be triggerable and impactful. Without confirming these two facts, I cannot assert this rises to a proven high/critical theft or stranding-of-funds scenario; it is at most a validator-set admission accounting inconsistency, not a confirmed fund-theft or permanent-loss path.

### Likelihood Explanation
Uncertain / not fully verified. This requires (1) confirming `get_voting_power`'s treatment of `pending_inactive`, and (2) confirming that a stake pool can accumulate `pending_inactive` balance while `locked_until_secs` is still `0` (i.e., before ever being active). I was unable to locate and inspect `get_voting_power` and the `initialize_validator`/`initialize_stake_owner` flow in the remaining tool budget to confirm these preconditions, so likelihood cannot be established with confidence.

### Recommendation
Have a Devin session with full repository access:
1. Inspect `get_voting_power` in `aptos-move/framework/aptos-framework/sources/stake.move` to confirm whether it includes `pending_inactive` coins.
2. Confirm whether `unlock_with_cap`/`unlock` can be called on a stake pool with `locked_until_secs == 0` (before any lockup has ever been set), and trace whether such a pool could reach `join_validator_set_internal` with a non-zero `pending_inactive` balance.
3. If confirmed, make the settlement guard in `join_validator_set_internal` consistent with `withdraw_with_cap` (drop the `> 0` special case, or apply the same special case in both places) so that `pending_inactive` stake is settled identically for both validator-set admission and withdrawal purposes.

### Proof of Concept
Not constructed — this requires confirming the two preconditions above (whether `get_voting_power` counts `pending_inactive`, and whether a pool can reach this state) using a Devin session with Move test/CLI execution access, which was not available in this ask-only investigation.

**Given the unresolved preconditions, this finding should be treated as a candidate requiring further local verification, not a confirmed high/critical vulnerability.** If a background agent cannot confirm both preconditions, this candidate does not meet the bar for a proven finding under the stated gate.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1096-1112)
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
        assert!(voting_power >= minimum_stake, error::invalid_argument(ESTAKE_TOO_LOW));
        assert!(voting_power <= maximum_stake, error::invalid_argument(ESTAKE_TOO_HIGH));
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1186-1195)
```text
        let stake_pool = borrow_global_mut<StakePool>(pool_address);
        // There's an edge case where a validator unlocks their stake and leaves the validator set before
        // the stake is fully unlocked (the current lockup cycle has not expired yet).
        // This can leave their stake stuck in pending_inactive even after the current lockup cycle expires.
        if (get_validator_state(pool_address) == VALIDATOR_STATUS_INACTIVE
            && timestamp::now_seconds() >= stake_pool.locked_until_secs) {
            let pending_inactive_stake =
                coin::extract_all(&mut stake_pool.pending_inactive);
            coin::merge(&mut stake_pool.inactive, pending_inactive_stake);
        };
```
