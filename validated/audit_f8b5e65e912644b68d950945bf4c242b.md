[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L686-709)
```text
    #[view]
    /// Return refundable stake to be extracted from added `amount` at `add_stake` operation on pool `pool_address`.
    /// If the validator produces rewards this epoch, added stake goes directly to `pending_active` and
    /// does not earn rewards. However, all shares within a pool appreciate uniformly and when this epoch ends:
    /// - either added shares are still `pending_active` and steal from rewards of existing `active` stake
    /// - or have moved to `pending_inactive` and get full rewards (they displaced `active` stake at `unlock`)
    /// To mitigate this, some of the added stake is extracted and fed back into the pool as placeholder
    /// for the rewards the remaining stake would have earned if active:
    /// extracted-fee = (amount - extracted-fee) * reward-rate% * (100% - operator-commission%)
    public fun get_add_stake_fee(
        pool_address: address,
        amount: u64
    ): u64 acquires DelegationPool, NextCommissionPercentage {
        if (stake::is_current_epoch_validator(pool_address)) {
            let (rewards_rate, rewards_rate_denominator) = staking_config::get_reward_rate(&staking_config::get());
            if (rewards_rate_denominator > 0) {
                assert_delegation_pool_exists(pool_address);

                rewards_rate *= (MAX_FEE - operator_commission_percentage(pool_address));
                rewards_rate_denominator *= MAX_FEE;
                ((((amount as u128) * (rewards_rate as u128)) / ((rewards_rate as u128) + (rewards_rate_denominator as u128))) as u64)
            } else { 0 }
        } else { 0 }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1491-1521)
```text
        // synchronize delegation and stake pools before any user operation
        synchronize_delegation_pool(pool_address);

        // fee to be charged for adding `amount` stake on this delegation pool at this epoch
        let add_stake_fee = get_add_stake_fee(pool_address, amount);

        let pool = borrow_global_mut<DelegationPool>(pool_address);

        // stake the entire amount to the stake pool
        aptos_account::transfer(delegator, pool_address, amount);
        stake::add_stake(&retrieve_stake_pool_owner(pool), amount);

        // but buy shares for delegator just for the remaining amount after fee
        buy_in_active_shares(pool, delegator_address, amount - add_stake_fee);
        assert_min_active_balance(pool, delegator_address);

        // grant temporary ownership over `add_stake` fees to a separate shareholder in order to:
        // - not mistake them for rewards to pay the operator from
        // - distribute them together with the `active` rewards when this epoch ends
        // in order to appreciate all shares on the active pool atomically
        buy_in_active_shares(pool, NULL_SHAREHOLDER, add_stake_fee);

        event::emit(
            AddStake {
                pool_address,
                delegator_address,
                amount_added: amount,
                add_stake_fee,
            },
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1523-1563)
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
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1865-1886)
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

```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L2716-2769)
```text
    #[test(aptos_framework = @aptos_framework, validator = @0x123, delegator = @0x010)]
    public entry fun test_unlock_single(
        aptos_framework: &signer,
        validator: &signer,
        delegator: &signer,
    ) acquires DelegationPoolOwnership, DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage, DelegationPoolAllowlisting {
        initialize_for_test(aptos_framework);
        initialize_test_validator(validator, 100 * ONE_APT, true, true);

        let validator_address = signer::address_of(validator);
        let pool_address = get_owned_pool_address(validator_address);

        let delegator_address = signer::address_of(delegator);
        account::create_account_for_test(delegator_address);

        // add 200 coins pending_active until next epoch
        stake::mint(validator, 200 * ONE_APT);
        add_stake(validator, pool_address, 200 * ONE_APT);

        let fee = get_add_stake_fee(pool_address, 200 * ONE_APT);
        assert_delegation(validator_address, pool_address, 300 * ONE_APT - fee, 0, 0);
        stake::assert_stake_pool(pool_address, 100 * ONE_APT, 0, 200 * ONE_APT, 0);

        // cannot unlock pending_active stake (only 100/300 stake can be displaced)
        unlock(validator, pool_address, 100 * ONE_APT);
        assert_delegation(validator_address, pool_address, 200 * ONE_APT - fee, 0, 100 * ONE_APT);
        assert_pending_withdrawal(validator_address, pool_address, true, 0, false, 100 * ONE_APT);
        stake::assert_stake_pool(pool_address, 0, 0, 200 * ONE_APT, 100 * ONE_APT);
        assert_inactive_shares_pool(pool_address, 0, true, 100 * ONE_APT);

        // reactivate entire pending_inactive stake progressively
        reactivate_stake(validator, pool_address, 50 * ONE_APT);

        assert_delegation(validator_address, pool_address, 250 * ONE_APT - fee, 0, 50 * ONE_APT);
        assert_pending_withdrawal(validator_address, pool_address, true, 0, false, 50 * ONE_APT);
        stake::assert_stake_pool(pool_address, 50 * ONE_APT, 0, 200 * ONE_APT, 50 * ONE_APT);

        reactivate_stake(validator, pool_address, 50 * ONE_APT);

        assert_delegation(validator_address, pool_address, 300 * ONE_APT - fee, 0, 0);
        assert_pending_withdrawal(validator_address, pool_address, false, 0, false, 0);
        stake::assert_stake_pool(pool_address, 100 * ONE_APT, 0, 200 * ONE_APT, 0);
        // pending_inactive shares pool has not been deleted (as can still `unlock` this OLC)
        assert_inactive_shares_pool(pool_address, 0, true, 0);

        end_aptos_epoch();
        // 10000000000 * 1.01 active stake + 20000000000 pending_active stake
        assert_delegation(validator_address, pool_address, 301 * ONE_APT, 0, 0);
        stake::assert_stake_pool(pool_address, 301 * ONE_APT, 0, 0, 0);

        // can unlock more than at previous epoch as the pending_active stake became active
        unlock(validator, pool_address, 150 * ONE_APT);
        assert_delegation(validator_address, pool_address, 15100000001, 0, 14999999999);
        stake::assert_stake_pool(pool_address, 15100000001, 0, 0, 14999999999);
```
