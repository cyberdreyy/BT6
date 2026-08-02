[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L607-617)
```text
    public entry fun request_commission(
        account: &signer, staker: address, operator: address
    ) acquires Store, BeneficiaryForOperator {
        let account_addr = signer::address_of(account);
        assert!(
            account_addr == staker
                || account_addr == operator
                || account_addr == beneficiary_for_operator(operator),
            error::unauthenticated(ENOT_STAKER_OR_OPERATOR_OR_BENEFICIARY)
        );
        assert_staking_contract_exists(staker, operator);
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L807-810)
```text
    /// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
    /// beneficiary. To ensures payment to the current beneficiary, one should first call `distribute` before switching
    /// the beneficiary. An operator can set one beneficiary for staking contract pools, not a separate one for each pool.
    public entry fun set_beneficiary_for_operator(
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-900)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1696-1734)
```text
    public entry fun test_operator_can_set_beneficiary(
        aptos_framework: &signer,
        staker: &signer,
        operator1: &signer,
        beneficiary: &signer,
        operator2: &signer
    ) acquires Store, BeneficiaryForOperator {
        setup_staking_contract(
            aptos_framework,
            staker,
            operator1,
            INITIAL_BALANCE,
            10
        );
        let staker_address = signer::address_of(staker);
        let operator1_address = signer::address_of(operator1);
        let operator2_address = signer::address_of(operator2);
        let beneficiary_address = signer::address_of(beneficiary);

        // account::create_account_for_test(beneficiary_address);
        aptos_framework::aptos_account::create_account(beneficiary_address);
        assert_staking_contract_exists(staker_address, operator1_address);
        assert_staking_contract(
            staker_address,
            operator1_address,
            INITIAL_BALANCE,
            10
        );

        // Verify that the stake pool has been set up properly.
        let pool_address = stake_pool_address(staker_address, operator1_address);
        stake::assert_stake_pool(pool_address, INITIAL_BALANCE, 0, 0, 0);
        assert!(
            last_recorded_principal(staker_address, operator1_address)
                == INITIAL_BALANCE,
            0
        );
        assert!(stake::get_operator(pool_address) == operator1_address, 0);
        assert!(beneficiary_for_operator(operator1_address) == operator1_address, 0);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1613-1623)
```text
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
```

**File:** aptos-move/framework/aptos-framework/tests/delegation_pool_integration_tests.move (L694-720)
```text
    #[test(aptos_framework = @aptos_framework, validator = @0x123)]
    public entry fun test_active_validator_withdraw_should_cap_by_inactive_stake(
        aptos_framework: &signer, validator: &signer
    ) {
        initialize_for_test(aptos_framework);
        // Initial balance = 900 (idle) + 100 (staked) = 1000.
        let (_sk, pk, pop) = generate_identity();
        initialize_test_validator(&pk, &pop, validator, 100 * ONE_APT, true, true);
        stake::mint(validator, 900 * ONE_APT);

        // Validator unlocks stake.
        let validator_address = dp::get_owned_pool_address(signer::address_of(validator));
        dp::unlock(validator, validator_address, 100 * ONE_APT);
        // Enough time has passed so the stake is fully unlocked.
        timestamp::fast_forward_seconds(LOCKUP_CYCLE_SECONDS);
        end_epoch();

        // Validator can only withdraw a max of 100 unlocked coins even if they request to withdraw more than 100.
        dp::withdraw(validator, validator_address, 200 * ONE_APT);

        // Receive back all coins with an extra 1 for rewards.
        assert!(
            coin::balance<AptosCoin>(signer::address_of(validator)) == 100100000000,
            2
        );
        stake::assert_validator_state(validator_address, 0, 0, 0, 0, 0);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_proxy.move (L69-80)
```text
    public entry fun set_staking_contract_voter(owner: &signer, operator: address, new_voter: address) {
        let owner_address = signer::address_of(owner);
        if (staking_contract::staking_contract_exists(owner_address, operator)) {
            staking_contract::update_voter(owner, operator, new_voter);
        };
    }

    public entry fun set_stake_pool_voter(owner: &signer, new_voter: address) {
        if (stake::stake_pool_exists(signer::address_of(owner))) {
            stake::set_delegated_voter(owner, new_voter);
        };
    }
```
