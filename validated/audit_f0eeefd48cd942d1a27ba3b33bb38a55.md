## Title
Silent `pending_inactive → inactive` settlement in `stake::join_validator_set_internal` desynchronizes `delegation_pool` OLC accounting - (File: `aptos-move/framework/aptos-framework/sources/stake.move`)

### Summary
`stake::join_validator_set_internal` contains logic that is not present in a normal, minimal validator re-activation path: when an inactive validator rejoins the validator set and its lockup has already expired, the function directly merges the `StakePool.pending_inactive` coin store into `StakePool.inactive`: [1](#0-0) 

This mutation is triggered by an ordinary, unprivileged call to `join_validator_set` made by the operator address of the pool [2](#0-1) , completely outside of `delegation_pool::synchronize_delegation_pool`, which is the module that is supposed to be the sole authority for translating stake-pool level lockup/OLC transitions into the delegation pool's per-cycle `inactive_shares` table and `total_coins_inactive` bookkeeping.

### Finding Description
`delegation_pool.move` builds its withdrawal/lockup accounting on the assumption that `pending_inactive` stake for a given "observed lockup cycle" (OLC) is only converted to `inactive` stake through the normal epoch-transition path (`update_stake_pool` for active validators) or through delegator-initiated `withdraw`, both of which are always preceded by (or embed) `synchronize_delegation_pool`, which advances `pool.observed_lockup_cycle` and finalizes the OLC's `inactive_shares` pool [3](#0-2) .

`stake::withdraw_internal` (called from `delegation_pool::withdraw_internal`) is written assuming pending_inactive is only inactivated deterministically at `stake::withdraw` time or at epoch boundaries, and the delegation pool code even contains special-casing to "escape excess stake from inactivation" precisely because it depends on knowing exactly when `pending_inactive` becomes `inactive` [4](#0-3) .

The code added at `stake.move` lines 1096-1109 breaks this assumption: it inactivates the entire `pending_inactive` balance of the stake pool the moment the operator re-calls `join_validator_set`, with no coordination with `delegation_pool`'s OLC index or `total_coins_inactive` counter. Because `delegation_pool` has no hook into `join_validator_set`, the module's internal state (`pool.observed_lockup_cycle`, the `inactive_shares` table keyed by OLC, and `total_coins_inactive`) is left describing the *old* state (stake still nominally "pending_inactive" for the current OLC) while the actual `stake::StakePool` resource now reports that stake as `inactive`.

The next time any user operation triggers `synchronize_delegation_pool`, it must reconcile `stake::get_stake` against its own OLC bookkeeping. Since the pending_inactive-to-inactive transition happened without the pool's cycle-ending logic ever running, delegators who unlocked stake in the *current, not-yet-closed* OLC (i.e., `pending_withdrawals` entries pointing at `pool.observed_lockup_cycle`, and shares residing in `pending_inactive_shares_pool`) end up with their principal now sitting in the stake-pool's raw `inactive` coin store while the delegation pool believes it is still `pending_inactive` in an active, non-finalized OLC. This is exactly the class of value-tracking corruption the report's stake/lockup rubric targets: "Accounting across active, pending_active, pending_inactive, inactive... state must preserve value and withdrawal rights," and "reactivate, withdraw ... paths must not redirect value or strand it permanently."

Concretely, this can produce two distinct bad outcomes depending on how the desync resolves in `withdraw_internal`/`reactivate_stake_internal`:
- Delegators who called `unlock` in the current OLC (funds nominally `pending_inactive`, tracked via `pending_inactive_shares_pool`) can find `reactivate_stake` operating against a share pool whose backing coins have already been physically moved to `inactive` by the operator's `join_validator_set` call, since `stake::reactivate_stake` operates on the `pending_inactive` coin field which no longer contains the coins that were merged away.
- The `total_coins_inactive` invariant relied upon by delegation pool's spec ("Slashing is not possible for inactive stakes... inactive staked coins must be greater than or equal to total_coins_inactive") is calculated by comparing pool-level bookkeeping against `stake::get_stake`; a merge that happens outside of `synchronize_delegation_pool` can make `total_coins_inactive` diverge from the actual `inactive` balance, corrupting withdrawal accounting for the pool's active OLC.

### Impact Explanation
This breaks the "Accounting across active, pending_active, pending_inactive, inactive... state must preserve value and withdrawal rights" invariant for delegation pools without any privileged action - the trigger is simply the pool's operator calling the ordinary `stake::join_validator_set`/`stake::rotate_consensus_key` flow to rejoin after being kicked/left the validator set with an expired lockup, which is a normal, expected operational sequence (exercised by the existing test `test_active_validator_leaves_staking_and_rejoins_with_expired_lockup_should_be_renewed` [5](#0-4) ). Because delegators' pending withdrawal shares and the pool's cycle counters are not touched, but the underlying coins are silently moved, this can strand or misattribute delegator principal/reward claim rights tied to the currently-open OLC, a High-severity accounting corruption in the stake/lockup domain the task explicitly targets.

### Likelihood Explanation
Likelihood is significant for any delegation pool backing a validator whose operator leaves the validator set (voluntarily via `stake::leave_validator_set` or involuntarily by falling under minimum stake / being evicted) and later rejoins after the lockup has already expired — a routine, permissionless operational sequence with no special conditions required beyond ordinary epoch/time passage. No malicious actor collusion is required; only normal operator behavior combined with pre-existing delegator `unlock` activity in the current OLC.

### Recommendation
Do not let `stake::join_validator_set_internal` (or any other unprivileged/operator-triggered path outside of `delegation_pool`) directly mutate `pending_inactive`/`inactive` coin balances. Either:
1. Remove this settlement logic from `join_validator_set_internal` and instead compute "effective voting power" for the minimum/maximum stake check without physically moving pending_inactive coins, or
2. Expose a synchronization hook that `delegation_pool` (and other consumers of `stake::OwnerCapability`, e.g. `staking_contract`) can call/observe atomically with this merge, so that OLC advancement and `total_coins_inactive` are updated in the same transaction as the coin merge.

### Proof of Concept
Not independently executed (no code execution/test environment available in this investigation); the trace below is derived purely from static analysis of the linked source:
1. Operator creates a delegation pool, delegator A calls `delegation_pool::unlock` during the current OLC — funds move to `pending_inactive_shares_pool(pool)` at `pool.observed_lockup_cycle` [6](#0-5) .
2. Validator leaves the validator set (`stake::leave_validator_set`), lockup subsequently expires (`timestamp::fast_forward_seconds(LOCKUP_CYCLE_SECONDS)`), matching the pattern in the existing rejoin test [7](#0-6) .
3. Operator calls `stake::join_validator_set` to rejoin; because `locked_until_secs > 0` and has passed, `join_validator_set_internal` merges `pending_inactive` into `inactive` directly on the `StakePool` [8](#0-7)  — without `delegation_pool::synchronize_delegation_pool` ever running to close out the OLC.
4. Delegator A subsequently calls `delegation_pool::reactivate_stake` or `withdraw` against the OLC that the pool still considers open; the pool's internal share/OLC bookkeeping is now inconsistent with the physical `inactive`/`pending_inactive` split reported by `stake::get_stake`, which the recommendation above is meant to prevent.

I was not able to independently confirm the exact downstream abort/mis-transfer behavior in `withdraw_internal`/`reactivate_stake_internal` given the desynchronized state (this would require executing a Move unit test), so this should be validated with an actual Move test reproducing steps 1-4 before being treated as fully confirmed.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1064-1073)
```text
    public entry fun join_validator_set(
        operator: &signer, pool_address: address
    ) acquires StakePool, ValidatorConfig, ValidatorSet {
        assert!(
            staking_config::get_allow_validator_set_change(&staking_config::get()),
            error::invalid_argument(ENO_POST_GENESIS_VALIDATOR_SET_CHANGE_ALLOWED)
        );

        join_validator_set_internal(operator, pool_address);
    }
```

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

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L258-285)
```text
    struct ObservedLockupCycle has copy, drop, store {
        index: u64,
    }

    struct DelegationPool has key {
        // Shares pool of `active` + `pending_active` stake
        active_shares: pool_u64::Pool,
        // Index of current observed lockup cycle on the delegation pool since its creation
        observed_lockup_cycle: ObservedLockupCycle,
        // Shares pools of `inactive` stake on each ended OLC and `pending_inactive` stake on the current one.
        // Tracks shares of delegators who requested withdrawals in each OLC
        inactive_shares: Table<ObservedLockupCycle, pool_u64::Pool>,
        // Mapping from delegator address to the OLC of its pending withdrawal if having one
        pending_withdrawals: Table<address, ObservedLockupCycle>,
        // Signer capability of the resource account owning the stake pool
        stake_pool_signer_cap: account::SignerCapability,
        // Total (inactive) coins on the shares pools over all ended OLCs
        total_coins_inactive: u64,
        // Commission fee paid to the node operator out of pool rewards
        operator_commission_percentage: u64,

        // The events emitted by stake-management operations on the delegation pool
        add_stake_events: EventHandle<AddStakeEvent>,
        reactivate_stake_events: EventHandle<ReactivateStakeEvent>,
        unlock_stake_events: EventHandle<UnlockStakeEvent>,
        withdraw_stake_events: EventHandle<WithdrawStakeEvent>,
        distribute_commission_events: EventHandle<DistributeCommissionEvent>,
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1652-1667)
```text
        // stake pool will inactivate entire pending_inactive stake at `stake::withdraw` to make it withdrawable
        // however, bypassing the inactivation of excess stake (inactivated but not withdrawn) ensures
        // the OLC is not advanced indefinitely on `unlock`-`withdraw` paired calls
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
        } else {
```

**File:** aptos-move/framework/aptos-framework/tests/delegation_pool_integration_tests.move (L941-990)
```text
    #[test(aptos_framework = @aptos_framework, validator = @0x123, validator_2 = @0x234)]
    public entry fun test_active_validator_leaves_staking_and_rejoins_with_expired_lockup_should_be_renewed(
        aptos_framework: &signer,
        validator: &signer,
        validator_2: &signer
    ) {
        initialize_for_test(aptos_framework);
        let (_sk_1, pk_1, pop_1) = generate_identity();
        let (_sk_2, pk_2, pop_2) = generate_identity();
        initialize_test_validator(
            &pk_1,
            &pop_1,
            validator,
            100 * ONE_APT,
            true,
            false
        );
        // We need a second validator here just so the first validator can leave.
        initialize_test_validator(
            &pk_2,
            &pop_2,
            validator_2,
            100 * ONE_APT,
            true,
            true
        );

        // Leave the validator set while still having a lockup.
        let validator_address = dp::get_owned_pool_address(signer::address_of(validator));
        assert!(
            stake::get_remaining_lockup_secs(validator_address) == LOCKUP_CYCLE_SECONDS,
            0
        );
        stake::leave_validator_set(validator, validator_address);
        end_epoch();

        // Fast forward enough so the lockup expires.
        timestamp::fast_forward_seconds(LOCKUP_CYCLE_SECONDS);
        assert!(stake::get_remaining_lockup_secs(validator_address) == 0, 1);

        // Validator rejoins the validator set. Once the current epoch ends, their lockup should be automatically
        // renewed.
        stake::join_validator_set(validator, validator_address);
        end_epoch();
        assert!(
            stake::get_validator_state(validator_address) == VALIDATOR_STATUS_ACTIVE, 2
        );
        assert!(
            stake::get_remaining_lockup_secs(validator_address) == LOCKUP_CYCLE_SECONDS,
            2
```
