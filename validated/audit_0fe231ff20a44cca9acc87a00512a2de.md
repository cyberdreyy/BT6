[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L344-347)
```text
    struct NextCommissionPercentage has key {
        commission_percentage_next_lockup_cycle: u64,
        effective_after_secs: u64,
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L552-555)
```text
    public fun is_next_commission_percentage_effective(pool_address: address): bool acquires NextCommissionPercentage {
        exists<NextCommissionPercentage>(pool_address) &&
            timestamp::now_seconds() >= borrow_global<NextCommissionPercentage>(pool_address).effective_after_secs
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1321-1326)
```text
            let pool_signer = account::create_signer_with_capability(&delegation_pool.stake_pool_signer_cap);
            move_to(&pool_signer, NextCommissionPercentage {
                commission_percentage_next_lockup_cycle: new_commission_percentage,
                effective_after_secs: stake::get_lockup_secs(pool_address),
            });
        };
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1862-1913)
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
