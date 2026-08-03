## Finding

Wall-clock–based `effective_after_secs` in `NextCommissionPercentage` is decoupled from the actual on-chain lockup rollover, and because `synchronize_delegation_pool` is a permissionless `public entry` function, any unprivileged account can flip `operator_commission_percentage` to the new value *before* the stake pool's lockup cycle has actually ended on-chain, mis-taxing rewards that still belong to the still-open lockup cycle.

### Title
Premature commission-percentage flip via permissionless `synchronize_delegation_pool` call decoupled from actual lockup rollover — ([File: aptos-move/framework/aptos-framework/sources/delegation_pool.move])

### Summary
`update_commission_percentage` stores the pending new commission with `effective_after_secs = stake::get_lockup_secs(pool_address)` [1](#0-0) . Whether that pending percentage is "effective" is decided purely by comparing wall-clock time to this stamp: [2](#0-1) 

`synchronize_delegation_pool` is a permissionless `public entry fun` (only `assert_delegation_pool_exists`, no role check) [3](#0-2) . It computes reward drift and `lockup_cycle_ended` from the *actual* stake pool inactive-stake movement (`calculate_stake_pool_drift`) [4](#0-3) , but at the very end it flips `pool.operator_commission_percentage` solely based on the wall-clock check, independent of whether `lockup_cycle_ended` was actually observed true in that same call: [5](#0-4) 

On the stake side, the lockup's `locked_until_secs` is only advanced, and `pending_inactive`→`inactive` stake is only moved, during an actual epoch reconfiguration event (`on_new_epoch`) — not continuously with wall-clock time [6](#0-5) . Block/wall-clock time, however, advances every block. This creates a window: once `timestamp::now_seconds() >= effective_after_secs` but before the next reconfiguration actually rolls the lockup over on the `StakePool`, `is_next_commission_percentage_effective` already returns `true` while the delegation pool's own `lockup_cycle_ended` is still `false`.

### Finding Description
Any account (need not own the pool, be the operator, or even be a delegator) can call `synchronize_delegation_pool(pool_address)` at any time. If it is called during the above window (after the wall clock crosses the recorded `effective_after_secs`, but before the network's next epoch/reconfiguration event actually renews the stake pool's lockup), the call:
1. Correctly computes drift/commission for the still-unsynced rewards using the **old** commission percentage (since `pool.operator_commission_percentage` hasn't been updated yet at that point in the function), and
2. Then, purely because `timestamp::now_seconds() >= effective_after_secs`, immediately sets `pool.operator_commission_percentage` to the **new** percentage — even though `observed_lockup_cycle` did not advance and the stake pool's `locked_until_secs`/`inactive` state show the lockup has *not* actually ended.

When the real epoch transition subsequently occurs and distributes further rewards for the remainder of that still-open lockup cycle, the next `synchronize_delegation_pool` call (or the automatic one triggered from any user operation) will tax that reward slice at the prematurely-installed **new** commission percentage instead of the **old** one that governed that lockup cycle. This directly contradicts the intended and tested invariant that "the new commission percentage does not take effect until the next lockup cycle," as explicitly asserted in `test_update_commission_percentage` [7](#0-6) .

### Impact Explanation
Depending on the direction of the pending commission change:
- If the operator is raising commission, an unprivileged party (including the operator/beneficiary themselves) can trigger the premature flip to start collecting the higher rate on rewards technically still belonging to the old (unexpired) lockup cycle — over-paying the operator's beneficiary at delegators' expense.
- If commission is being lowered, any delegator can trigger the premature flip to make the lower rate apply early to rewards still owed to the operator at the old (higher) rate — under-paying the operator/beneficiary.

This is a value-redistribution bug between delegator and operator/beneficiary accounting state, matching the "Stake And Lockup Pivots" requirement that commission accounting across lockup-cycle boundaries preserve value, without the caller needing to already hold the owner/operator role.

### Likelihood Explanation
Requires: (a) a pending `NextCommissionPercentage` change already in place (owner-initiated, but that owner action alone is legitimate and common), and (b) an unprivileged caller submitting a `synchronize_delegation_pool` transaction inside the narrow window between wall-clock crossing `effective_after_secs` and the next actual epoch reconfiguration. Since epoch intervals are typically much shorter than lockup durations, this window is generally on the order of a single epoch interval and is trivially triggerable by simply watching the chain and firing an unsigned-cost `synchronize_delegation_pool` transaction — no special permissions needed. Magnitude scales with the reward accrued in that window and the commission delta, so it is a real but generally small-magnitude, opportunistic issue rather than a catastrophic drain.

### Recommendation
Gate the commission-percentage flip in `synchronize_delegation_pool` on the actual `lockup_cycle_ended` signal (i.e., only flip `pool.operator_commission_percentage` when `lockup_cycle_ended` is `true` for that call, or otherwise only after `observed_lockup_cycle.index` has actually advanced), rather than solely on `timestamp::now_seconds() >= effective_after_secs`. This ties the commission update to the true on-chain lockup rollover instead of a wall-clock proxy that permissionless callers can race ahead of the actual reconfiguration event.

### Proof of Concept
1. Operator creates delegation pool, delegator adds/unlocks stake; run a few `end_aptos_epoch()` cycles to accrue rewards, matching the setup in `test_update_commission_percentage` [8](#0-7) .
2. Operator calls `update_commission_percentage` to schedule a higher rate; `effective_after_secs` is set to the current `stake::get_lockup_secs`.
3. Instead of calling `end_aptos_epoch()` (which would perform the real reconfiguration/lockup-rollover), simply advance `timestamp::fast_forward_seconds` to just past the lockup boundary WITHOUT triggering `end_aptos_epoch()`.
4. Have an unprivileged/arbitrary account call `synchronize_delegation_pool(pool_address)`. Assert `operator_commission_percentage(pool_address)` is now the *new* value even though `observed_lockup_cycle(pool_address)` has not advanced and `stake::get_remaining_lockup_secs(pool_address)` is still reported as expired/zero (i.e., the stake pool's actual lockup has not been rolled over by reconfiguration).
5. Trigger `end_aptos_epoch()` to let the real lockup rollover and reward distribution happen, then call `synchronize_delegation_pool` again and compare the commission actually charged against the expected value computed at the *old* commission percentage (as in the existing test's assertions at lines 3846-3852) — showing the mismatch caused by the premature flip in step 4.

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

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1865-1913)
```text
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

        // on stake-management operations, total coins on the internal shares pools and individual
        // stakes on the stake pool are updated simultaneously, thus the only stakes becoming
        // unsynced are rewards and slashes routed exclusively to/out the stake pool

        // operator `active` rewards not persisted yet to the active shares pool
        let pool_active = pool.active_shares.total_coins();
        let commission_active = if (active > pool_active) {
            math64::mul_div(active - pool_active, pool.operator_commission_percentage, MAX_FEE)
        } else {
            // handle any slashing applied to `active` stake
            0
        };
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

        (lockup_cycle_ended, active, pending_inactive, commission_active, commission_pending_inactive)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1917-1921)
```text
    public entry fun synchronize_delegation_pool(
        pool_address: address
    ) acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage {
        assert_delegation_pool_exists(pool_address);
        let pool = borrow_global_mut<DelegationPool>(pool_address);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1976-1993)
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

        if (is_next_commission_percentage_effective(pool_address)) {
            pool.operator_commission_percentage = borrow_global<NextCommissionPercentage>(
                pool_address
            ).commission_percentage_next_lockup_cycle;
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L3794-3844)
```text
    #[test(aptos_framework = @aptos_framework, operator = @0x123, delegator = @0x010)]
    public entry fun test_update_commission_percentage(
        aptos_framework: &signer,
        operator: &signer,
        delegator: &signer,
    ) acquires DelegationPoolOwnership, DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage, DelegationPoolAllowlisting {
        initialize_for_test(aptos_framework);

        let operator_address = signer::address_of(operator);
        account::create_account_for_test(operator_address);

        // create delegation pool of commission fee 12.65%
        initialize_delegation_pool(operator, 1265, vector::empty<u8>());
        let pool_address = get_owned_pool_address(operator_address);
        assert!(stake::get_operator(pool_address) == operator_address, 0);

        let delegator_address = signer::address_of(delegator);
        account::create_account_for_test(delegator_address);

        stake::mint(delegator, 200 * ONE_APT);
        add_stake(delegator, pool_address, 200 * ONE_APT);
        unlock(delegator, pool_address, 100 * ONE_APT);

        // activate validator
        stake::rotate_consensus_key(operator, pool_address, CONSENSUS_KEY_1, CONSENSUS_POP_1);
        stake::join_validator_set(operator, pool_address);
        end_aptos_epoch();

        // produce active and pending_inactive rewards
        end_aptos_epoch();
        stake::assert_stake_pool(pool_address, 10100000000, 0, 0, 10100000000);
        assert_delegation(operator_address, pool_address, 12650000, 0, 12650000);
        end_aptos_epoch();
        stake::assert_stake_pool(pool_address, 10201000000, 0, 0, 10201000000);
        assert_delegation(operator_address, pool_address, 25426500, 0, 25426500);

        // change the commission percentage
        update_commission_percentage(operator, 2265);
        // the new commission percentage does not take effect until the next lockup cycle.
        assert!(operator_commission_percentage(pool_address) == 1265, 0);

        // end the lockup cycle
        fast_forward_to_unlock(pool_address);

        // Test that the `get_add_stake_fee` correctly uses the new commission percentage, and returns the correct
        // fee amount 76756290 in the following case, not 86593604 (calculated with the old commission rate).
        assert!(get_add_stake_fee(pool_address, 100 * ONE_APT) == 76756290, 0);

        synchronize_delegation_pool(pool_address);
        // the commission percentage is updated to the new one.
        assert!(operator_commission_percentage(pool_address) == 2265, 0);
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
