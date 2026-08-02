[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1081-1110)
```text
    public(friend) fun join_validator_set_internal(
        operator: &signer, pool_address: address
    ) acquires StakePool, ValidatorConfig, ValidatorSet {
        assert_reconfig_not_in_progress();
        assert_stake_pool_exists(pool_address);
        let stake_pool = borrow_global_mut<StakePool>(pool_address);
        assert!(
            signer::address_of(operator) == stake_pool.operator_address,
            error::unauthenticated(ENOT_OPERATOR)
        );
        assert!(
            get_validator_state(pool_address) == VALIDATOR_STATUS_INACTIVE,
            error::invalid_state(EALREADY_ACTIVE_VALIDATOR)
        );

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
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L2569-2597)
```text
    #[test(aptos_framework = @aptos_framework, validator_1 = @0x123, validator_2 = @0x234)]
    #[expected_failure(abort_code = 0x1000D, location = Self)]
    public entry fun test_inactive_validator_cannot_join_if_exceed_increase_limit(
        aptos_framework: &signer, validator_1: &signer, validator_2: &signer
    ) acquires AllowedValidators, AptosCoinCapabilities, OwnerCapability, PendingTransactionFee, PrecomputedValidatorSet, StakePool, TransactionFeeConfig, ValidatorConfig, ValidatorPerformance, ValidatorSet {
        // Only 50% voting power increase is allowed in each epoch.
        initialize_for_test_custom(
            aptos_framework,
            50,
            10000,
            LOCKUP_CYCLE_SECONDS,
            true,
            1,
            10,
            50
        );
        let (_sk_1, pk_1, pop_1) = generate_identity();
        let (_sk_2, pk_2, pop_2) = generate_identity();
        initialize_test_validator(&pk_1, &pop_1, validator_1, 100, false, false);
        initialize_test_validator(&pk_2, &pop_2, validator_2, 100, false, false);

        // Validator 1 needs to be in the set so validator 2's added stake counts against the limit.
        join_validator_set(validator_1, signer::address_of(validator_1));
        end_epoch();

        // Validator 2 joins the validator set but their stake would lead to exceeding the voting power increase limit.
        // Therefore, this should fail.
        join_validator_set(validator_2, signer::address_of(validator_2));
    }
```
