[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L566-601)
```text
    public entry fun update_commision(
        staker: &signer, operator: address, new_commission_percentage: u64
    ) acquires Store, BeneficiaryForOperator {
        assert!(
            new_commission_percentage >= 0 && new_commission_percentage <= 100,
            error::invalid_argument(EINVALID_COMMISSION_PERCENTAGE)
        );

        let staker_address = signer::address_of(staker);
        assert!(
            exists<Store>(staker_address),
            error::not_found(ENO_STAKING_CONTRACT_FOUND_FOR_STAKER)
        );

        let store = borrow_global_mut<Store>(staker_address);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);
        distribute_internal(
            staker_address,
            operator,
            staking_contract,
        );
        request_commission_internal(
            operator,
            staking_contract,
        );
        let old_commission_percentage = staking_contract.commission_percentage;
        staking_contract.commission_percentage = new_commission_percentage;
        emit(
            UpdateCommission {
                staker: staker_address,
                operator,
                old_commission_percentage,
                new_commission_percentage
            }
        );

```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L762-805)
```text
    public entry fun switch_operator(
        staker: &signer,
        old_operator: address,
        new_operator: address,
        new_commission_percentage: u64
    ) acquires Store, BeneficiaryForOperator {
        let staker_address = signer::address_of(staker);
        assert_staking_contract_exists(staker_address, old_operator);

        assert!(
            new_commission_percentage <= 100,
            error::invalid_argument(EINVALID_COMMISSION_PERCENTAGE)
        );
        // Merging two existing staking contracts is too complex as we'd need to merge two separate stake pools.
        let store = borrow_global_mut<Store>(staker_address);
        let staking_contracts = &mut store.staking_contracts;
        assert!(
            !staking_contracts.contains_key(&new_operator),
            error::invalid_state(ECANT_MERGE_STAKING_CONTRACTS)
        );

        let (_, staking_contract) = staking_contracts.remove(&old_operator);
        // Force distribution of any already inactive stake.
        distribute_internal(
            staker_address,
            old_operator,
            &mut staking_contract,
        );

        // For simplicity, we request commission to be paid out first. This avoids having to ensure to staker doesn't
        // withdraw into the commission portion.
        request_commission_internal(
            old_operator,
            &mut staking_contract,
        );

        // Update the staking contract's commission rate and stake pool's operator.
        stake::set_operator_with_cap(&staking_contract.owner_cap, new_operator);
        staking_contract.commission_percentage = new_commission_percentage;

        let pool_address = staking_contract.pool_address;
        staking_contracts.add(new_operator, staking_contract);
        emit(SwitchOperator { pool_address, old_operator, new_operator });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L840-920)
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
            );

            emit(
                Distribute {
                    operator,
                    pool_address,
                    recipient,
                    amount: amount_to_distribute
                }
            );
        };

        // In case there's any dust left, send them all to the staker.
        if (coin::value(&coins) > 0) {
            aptos_account::deposit_coins(staker, coins);
            distribution_pool.update_total_coins(0);
        } else {
            coin::destroy_zero(coins);
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L36-49)
```text
Accounting main invariants:
 - each stake-management operation (add/unlock/reactivate/withdraw) and operator change triggers
the synchronization process before executing its own function.
 - each OLC maps to one or more real lockups on the stake pool, but not the opposite. Actually, only a real
lockup with 'activity' (which inactivated some unlocking stake) triggers the creation of a new OLC.
 - unlocking and/or unlocked stake originating from different real lockups are never mixed together into
the same pool_u64. This invalidates the accounting of which rewards belong to whom.
 - no delegator can have unlocking and/or unlocked stake (pending withdrawals) in different OLCs. This ensures
delegators do not have to keep track of the OLCs when they unlocked. When creating a new pending withdrawal,
the existing one is executed (withdrawn) if is already inactive.
 - <code>add_stake</code> fees are always refunded, but only after the epoch when they have been charged ends.
 - withdrawing pending_inactive stake (when validator had gone inactive before its lockup expired)
does not inactivate any stake additional to the requested one to ensure OLC would not advance indefinitely.
 - the pending withdrawal exists at an OLC iff delegator owns some shares within the shares pool of that OLC.
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1523-1650)
```text
    /// Unlock `amount` from the active + pending_active stake of `delegator` or
    /// at most how much active stake there is on the stake pool.
    public entry fun unlock(
        delegator: &signer,
        pool_address: address,
        amount: u64
    ) acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage {
        // short-circuit if amount to unlock is 0 so no event is emitted































        buy_in_pending_inactive_shares(pool, delegator_address, amount);
        assert_min_pending_inactive_balance(pool, delegator_address);

        event::emit(
            UnlockStake {
                pool_address,
                delegator_address,
                amount_unlocked: amount,
            },
        );
    }

    /// Move `amount` of coins from pending_inactive to active.
    public entry fun reactivate_stake(
        delegator: &signer,
        pool_address: address,
        amount: u64
    ) acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage, DelegationPoolAllowlisting {
        // short-circuit if amount to reactivate is 0 so no event is emitted
        if (amount == 0) { return };

        let delegator_address = signer::address_of(delegator);
        assert_delegator_allowlisted(pool_address, delegator_address);

        // synchronize delegation and stake pools before any user operation
        synchronize_delegation_pool(pool_address);

        let pool = borrow_global_mut<DelegationPool>(pool_address);
        amount = coins_to_transfer_to_ensure_min_stake(
            pending_inactive_shares_pool(pool),
            &pool.active_shares,
            delegator_address,
            amount,
        );
        let observed_lockup_cycle = pool.observed_lockup_cycle;
        amount = redeem_inactive_shares(pool, delegator_address, amount, observed_lockup_cycle);

        stake::reactivate_stake(&retrieve_stake_pool_owner(pool), amount);

        buy_in_active_shares(pool, delegator_address, amount);
        assert_min_active_balance(pool, delegator_address);

        event::emit(
            ReactivateStake {
                pool_address,
                delegator_address,
                amount_reactivated: amount,
            },
        );
    }

    /// Withdraw `amount` of owned inactive stake from the delegation pool at `pool_address`.
    public entry fun withdraw(
        delegator: &signer,
        pool_address: address,
        amount: u64
    ) acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage {
        assert!(amount > 0, error::invalid_argument(EWITHDRAW_ZERO_STAKE));
        // synchronize delegation and stake pools before any user operation
        synchronize_delegation_pool(pool_address);
        withdraw_internal(borrow_global_mut<DelegationPool>(pool_address), signer::address_of(delegator), amount);
    }

    fun withdraw_internal(
        pool: &mut DelegationPool,
        delegator_address: address,
        amount: u64
    ) acquires GovernanceRecords {
        // TODO: recycle storage when a delegator fully exits the delegation pool.
        // short-circuit if amount to withdraw is 0 so no event is emitted
        if (amount == 0) { return };

        let pool_address = get_pool_address(pool);
        let (withdrawal_exists, withdrawal_olc) = pending_withdrawal_exists(pool, delegator_address);
        // exit if no withdrawal or (it is pending and cannot withdraw pending_inactive stake from stake pool)
        if (!(
            withdrawal_exists &&
                (withdrawal_olc.index < pool.observed_lockup_cycle.index || can_withdraw_pending_inactive(pool_address))
        )) { return };

        if (withdrawal_olc.index == pool.observed_lockup_cycle.index) {
            amount = coins_to_redeem_to_ensure_min_stake(
                pending_inactive_shares_pool(pool),
                delegator_address,
                amount,
            )
        };
        amount = redeem_inactive_shares(pool, delegator_address, amount, withdrawal_olc);

```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L770-810)
```text
    /// Terminate the vesting contract and send all funds back to the withdrawal address.
    public entry fun terminate_vesting_contract(admin: &signer, contract_address: address) acquires VestingContract {
        assert_active_vesting_contract(contract_address);

        // Distribute all withdrawable coins, which should have been from previous rewards withdrawal or vest.
        distribute(contract_address);

        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        verify_admin(admin, vesting_contract);
        let (active_stake, _, pending_active_stake, _) = stake::get_stake(vesting_contract.staking.pool_address);
        assert!(pending_active_stake == 0, error::invalid_state(EPENDING_STAKE_FOUND));

        // Unlock all remaining active stake.
        vesting_contract.state = VESTING_POOL_TERMINATED;
        vesting_contract.remaining_grant = 0;
        unlock_stake(vesting_contract, active_stake);

        emit(
            Terminate {
                admin: vesting_contract.admin,
                vesting_contract_address: contract_address,
            },
        );
    }

    /// Withdraw all funds to the preset vesting contract's withdrawal address. This can only be called if the contract
    /// has already been terminated.
    public entry fun admin_withdraw(admin: &signer, contract_address: address) acquires VestingContract {
        let vesting_contract = borrow_global<VestingContract>(contract_address);
        assert!(
            vesting_contract.state == VESTING_POOL_TERMINATED,
            error::invalid_state(EVESTING_CONTRACT_STILL_ACTIVE)
        );

        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        verify_admin(admin, vesting_contract);
        let coins = withdraw_stake(vesting_contract, contract_address);
        let amount = coin::value(&coins);
        if (amount == 0) {
            coin::destroy_zero(coins);
            return
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L1052-1078)
```text
    fun verify_admin(admin: &signer, vesting_contract: &VestingContract) {
        assert!(signer::address_of(admin) == vesting_contract.admin, error::unauthenticated(ENOT_ADMIN));
    }

    fun assert_vesting_contract_exists(contract_address: address) {
        assert!(exists<VestingContract>(contract_address), error::not_found(EVESTING_CONTRACT_NOT_FOUND));
    }

    fun assert_active_vesting_contract(contract_address: address) acquires VestingContract {
        assert_vesting_contract_exists(contract_address);
        let vesting_contract = borrow_global<VestingContract>(contract_address);
        assert!(vesting_contract.state == VESTING_POOL_ACTIVE, error::invalid_state(EVESTING_CONTRACT_NOT_ACTIVE));
    }

    fun unlock_stake(vesting_contract: &VestingContract, amount: u64) {
        let contract_signer = &get_vesting_account_signer_internal(vesting_contract);
        staking_contract::unlock_stake(contract_signer, vesting_contract.staking.operator, amount);
    }

    fun withdraw_stake(vesting_contract: &VestingContract, contract_address: address): Coin<AptosCoin> {
        // Claim any withdrawable distribution from the staking contract. The withdrawn coins will be sent directly to
        // the vesting contract's account.
        staking_contract::distribute(contract_address, vesting_contract.staking.operator);
        let withdrawn_coins = coin::balance<AptosCoin>(contract_address);
        let contract_signer = &get_vesting_account_signer_internal(vesting_contract);
        coin::withdraw<AptosCoin>(contract_signer, withdrawn_coins)
    }
```
