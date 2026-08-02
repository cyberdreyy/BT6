[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L864-893)
```text
        // generate a seed to be used to create the resource account hosting the delegation pool
        let seed = create_resource_account_seed(delegation_pool_creation_seed);

        let (stake_pool_signer, stake_pool_signer_cap) = account::create_resource_account(owner, seed);
        coin::register<AptosCoin>(&stake_pool_signer);

        // stake_pool_signer will be owner of the stake pool and have its `stake::OwnerCapability`
        let pool_address = signer::address_of(&stake_pool_signer);
        stake::initialize_stake_owner(&stake_pool_signer, 0, owner_address, owner_address);

        let inactive_shares = table::new<ObservedLockupCycle, pool_u64::Pool>();
        inactive_shares.add(olc_with_index(0), pool_u64::create_with_scaling_factor(SHARES_SCALING_FACTOR));

        move_to(&stake_pool_signer, DelegationPool {
            active_shares: pool_u64::create_with_scaling_factor(SHARES_SCALING_FACTOR),
            observed_lockup_cycle: olc_with_index(0),
            inactive_shares,
            pending_withdrawals: table::new<address, ObservedLockupCycle>(),
            stake_pool_signer_cap,
            total_coins_inactive: 0,
            operator_commission_percentage,
            add_stake_events: account::new_event_handle<AddStakeEvent>(&stake_pool_signer),
            reactivate_stake_events: account::new_event_handle<ReactivateStakeEvent>(&stake_pool_signer),
            unlock_stake_events: account::new_event_handle<UnlockStakeEvent>(&stake_pool_signer),
            withdraw_stake_events: account::new_event_handle<WithdrawStakeEvent>(&stake_pool_signer),
            distribute_commission_events: account::new_event_handle<DistributeCommissionEvent>(&stake_pool_signer),
        });

        // save delegation pool ownership and resource account address (inner stake pool address) on `owner`
        move_to(owner, DelegationPoolOwnership { pool_address });
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1915-1993)
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

        // reward operator its commission out of uncommitted active rewards (`add_stake` fees already excluded)
        buy_in_active_shares(pool, beneficiary_for_operator(stake::get_operator(pool_address)), commission_active);
        // reward operator its commission out of uncommitted pending_inactive rewards
        buy_in_pending_inactive_shares(
            pool,
            beneficiary_for_operator(stake::get_operator(pool_address)),
            commission_pending_inactive
        );

        event::emit_event(
            &mut pool.distribute_commission_events,
            DistributeCommissionEvent {
                pool_address,
                operator: stake::get_operator(pool_address),
                commission_active,
                commission_pending_inactive,
            },
        );

        emit(DistributeCommission {
            pool_address,
            operator: stake::get_operator(pool_address),
            beneficiary: beneficiary_for_operator(stake::get_operator(pool_address)),
            commission_active,
            commission_pending_inactive,
        });

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L840-900)
```text
    /// Allow anyone to distribute already unlocked funds. This does not affect reward compounding and therefore does
    /// not need to be restricted to just the staker or operator.
    public entry fun distribute(
        staker: address, operator: address
    ) acquires Store, BeneficiaryForOperator {
        assert_staking_contract_exists(staker, operator);
        let store = borrow_global_mut<Store>(staker);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);
        distribute_internal(
            staker,
            operator,
            staking_contract,
        );
    }

    /// Distribute all unlocked (inactive) funds according to distribution shares.
    fun distribute_internal(
        staker: address,
        operator: address,
        staking_contract: &mut StakingContract,
    ) acquires BeneficiaryForOperator {
        let pool_address = staking_contract.pool_address;
        // Create the Staker resource if it doesn't exist to backfill the Staker resource for each pool.
        if (!exists<Staker>(pool_address)) {
            let pool_signer =
                &account::create_signer_with_capability(&staking_contract.signer_cap);
            move_to(pool_signer, Staker { staker });
        };
        let (_, inactive, _, pending_inactive) = stake::get_stake(pool_address);
        let total_potential_withdrawable = inactive + pending_inactive;
        let coins =
            stake::withdraw_with_cap(
                &staking_contract.owner_cap, total_potential_withdrawable
            );
        let distribution_amount = coin::value(&coins);
        if (distribution_amount == 0) {
            coin::destroy_zero(coins);
            return
        };

        let distribution_pool = &mut staking_contract.distribution_pool;
        update_distribution_pool(
            distribution_pool,
            distribution_amount,
            operator,
            staking_contract.commission_percentage
        );

        // Buy all recipients out of the distribution pool.
        while (distribution_pool.shareholders_count() > 0) {
            let recipients = distribution_pool.shareholders();
            let recipient = recipients[0];
            let current_shares = distribution_pool.shares(recipient);
            let amount_to_distribute =
                distribution_pool.redeem_shares(recipient, current_shares);
            // If the recipient is the operator, send the commission to the beneficiary instead.
            if (recipient == operator) {
                recipient = beneficiary_for_operator(operator);
            };
            aptos_account::deposit_coins(
                recipient, coin::extract(&mut coins, amount_to_distribute)
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L718-720)
```text
    /// Distribute any withdrawable stake from the stake pool.
    public entry fun distribute(contract_address: address) acquires VestingContract {
        assert_active_vesting_contract(contract_address);
```
