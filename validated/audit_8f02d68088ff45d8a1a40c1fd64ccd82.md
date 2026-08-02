[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1042-1061)
```text
    public fun increase_lockup_with_cap(owner_cap: &OwnerCapability) acquires StakePool {
        let pool_address = owner_cap.pool_address;
        assert_stake_pool_exists(pool_address);
        let config = staking_config::get();

        let stake_pool = borrow_global_mut<StakePool>(pool_address);
        let old_locked_until_secs = stake_pool.locked_until_secs;
        let new_locked_until_secs =
            timestamp::now_seconds()
                + staking_config::get_recurring_lockup_duration(&config);
        assert!(
            old_locked_until_secs < new_locked_until_secs,
            error::invalid_argument(EINVALID_LOCKUP)
        );
        stake_pool.locked_until_secs = new_locked_until_secs;

        event::emit(
            IncreaseLockup { pool_address, old_locked_until_secs, new_locked_until_secs }
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1625-1671)
```text
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

        let stake_pool_owner = &retrieve_stake_pool_owner(pool);
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
            // no excess stake if `stake::withdraw` does not inactivate at all
            stake::withdraw(stake_pool_owner, amount);
        };
        aptos_account::transfer(stake_pool_owner, delegator_address, amount);
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L855-920)
```text
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
