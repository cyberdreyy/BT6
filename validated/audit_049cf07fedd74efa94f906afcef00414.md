[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1268-1284)
```text
    /// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
    /// beneficiary. To ensure payment to the current beneficiary, one should first call `synchronize_delegation_pool`
    /// before switching the beneficiary. An operator can set one beneficiary for delegation pools, not a separate
    /// one for each pool.
    public entry fun set_beneficiary_for_operator(
        operator: &signer,
        new_beneficiary: address
    ) acquires BeneficiaryForOperator {
        // The beneficiay address of an operator is stored under the operator's address.
        // So, the operator does not need to be validated with respect to a staking pool.
        let operator_addr = signer::address_of(operator);
        let old_beneficiary = beneficiary_for_operator(operator_addr);
        if (exists<BeneficiaryForOperator>(operator_addr)) {
            borrow_global_mut<BeneficiaryForOperator>(operator_addr).beneficiary_for_operator = new_beneficiary;
        } else {
            move_to(operator, BeneficiaryForOperator { beneficiary_for_operator: new_beneficiary });
        };
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1456-1477)
```text
    /// Evict a delegator that is not allowlisted by unlocking their entire stake.
    public entry fun evict_delegator(
        owner: &signer,
        delegator_address: address,
    ) acquires DelegationPoolOwnership, DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage, DelegationPoolAllowlisting {
        let pool_address = get_owned_pool_address(signer::address_of(owner));
        assert_allowlisting_enabled(pool_address);
        assert!(
            !delegator_allowlisted(pool_address, delegator_address),
            error::invalid_state(ECANNOT_EVICT_ALLOWLISTED_DELEGATOR)
        );

        // synchronize pool in order to query latest balance of delegator
        synchronize_delegation_pool(pool_address);

        let pool = borrow_global<DelegationPool>(pool_address);
        if (get_delegator_active_shares(pool, delegator_address) == 0) { return };

        unlock_internal(delegator_address, pool_address, pool.active_shares.balance(delegator_address));

        event::emit(EvictDelegator { pool_address, delegator_address });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1479-1489)
```text
    /// Add `amount` of coins to the delegation pool `pool_address`.
    public entry fun add_stake(
        delegator: &signer,
        pool_address: address,
        amount: u64
    ) acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage, DelegationPoolAllowlisting {
        // short-circuit if amount to add is 0 so no event is emitted
        if (amount == 0) { return };

        let delegator_address = signer::address_of(delegator);
        assert_delegator_allowlisted(pool_address, delegator_address);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1574-1584)
```text
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
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1613-1620)
```text
    /// Withdraw `amount` of owned inactive stake from the delegation pool at `pool_address`.
    public entry fun withdraw(
        delegator: &signer,
        pool_address: address,
        amount: u64
    ) acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage {
        assert!(amount > 0, error::invalid_argument(EWITHDRAW_ZERO_STAKE));
        // synchronize delegation and stake pools before any user operation
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L3767-3792)
```text
        withdraw(operator1, pool_address, ONE_APT);
        assert!(coin::balance<AptosCoin>(operator1_address) == ONE_APT - 1, 0);

        set_beneficiary_for_operator(operator1, beneficiary_address);
        assert!(beneficiary_for_operator(operator1_address) == beneficiary_address, 0);
        end_aptos_epoch();

        unlock(beneficiary, pool_address, ONE_APT);
        timestamp::fast_forward_seconds(LOCKUP_CYCLE_SECONDS);
        end_aptos_epoch();

        withdraw(beneficiary, pool_address, ONE_APT);
        assert!(coin::balance<AptosCoin>(beneficiary_address) == ONE_APT - 1, 0);
        assert!(coin::balance<AptosCoin>(operator1_address) == ONE_APT - 1, 0);

        // switch operator to operator2. The rewards should go to operator2 not to the beneficiay of operator1.
        set_operator(operator1, operator2_address);
        end_aptos_epoch();
        unlock(operator2, pool_address, ONE_APT);
        timestamp::fast_forward_seconds(LOCKUP_CYCLE_SECONDS);
        end_aptos_epoch();

        withdraw(operator2, pool_address, ONE_APT);
        assert!(coin::balance<AptosCoin>(beneficiary_address) == ONE_APT - 1, 0);
        assert!(coin::balance<AptosCoin>(operator2_address) == ONE_APT - 1, 0);
    }
```
