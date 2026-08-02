[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L840-853)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L855-870)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L937-957)
```text
    /// Add a new distribution for `recipient` and `amount` to the staking contract's distributions list.
    fun add_distribution(
        operator: address,
        staking_contract: &mut StakingContract,
        recipient: address,
        coins_amount: u64,
    ) {
        let distribution_pool = &mut staking_contract.distribution_pool;
        let (_, _, _, total_distribution_amount) =
            stake::get_stake(staking_contract.pool_address);
        update_distribution_pool(
            distribution_pool,
            total_distribution_amount,
            operator,
            staking_contract.commission_percentage
        );

        distribution_pool.buy_in(recipient, coins_amount);
        let pool_address = staking_contract.pool_address;
        emit(AddDistribution { operator, pool_address, amount: coins_amount });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L1690-1713)
```text
        let old_beneficiay_balance = coin::balance<AptosCoin>(beneficiary_address);

        // switch operator to operator2. The rewards should go to operator2 not to the beneficiay of operator1.
        update_operator(admin, contract_address, operator_address2, 10);

        stake::end_epoch();
        let (_, accumulated_rewards, _) = staking_contract::staking_contract_amounts(contract_address,
            operator_address2
        );

        let expected_commission = accumulated_rewards / 10;

        // Request commission.
        staking_contract::request_commission(operator2, contract_address, operator_address2);
        // Unlocks the commission.
        stake::fast_forward_to_unlock(stake_pool_address);
        expected_commission = with_rewards(expected_commission);

        // Distribute the commission to the operator.
        distribute(contract_address);

        // Assert that the rewards go to operator2, and the balance of the operator1's beneficiay remains the same.
        assert!(coin::balance<AptosCoin>(operator_address2) >= expected_commission, 1);
        assert!(coin::balance<AptosCoin>(beneficiary_address) == old_beneficiay_balance, 1);
```
