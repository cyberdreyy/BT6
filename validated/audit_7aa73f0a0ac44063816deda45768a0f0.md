[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_proxy.move (L192-205)
```text

        set_voter(owner, operator_1_address, new_voter_address);
        // Stake pool's voter has been updated.
        assert!(stake::get_delegated_voter(owner_address) == new_voter_address, 0);
        // Staking contract with operator 1's voter has been updated.
        // Staking contract with operator_2 should stay unchanged.
        let stake_pool_address_1 = staking_contract::stake_pool_address(owner_address, operator_1_address);
        let stake_pool_address_2 = staking_contract::stake_pool_address(owner_address, operator_2_address);
        assert!(stake::get_delegated_voter(stake_pool_address_1) == new_voter_address, 1);
        assert!(stake::get_delegated_voter(stake_pool_address_2) == operator_2_address, 2);
        // Vesting contract 1's voter has been updated while vesting contract 2's stays unchanged.
        assert!(vesting::voter(vesting_contract_1) == new_voter_address, 3);
        assert!(vesting::voter(vesting_contract_2) == owner_address, 4);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_proxy.move (L220-237)
```text
    ) {
        let owner_address = signer::address_of(owner);
        let operator_1_address = signer::address_of(operator_1);
        let operator_2_address = signer::address_of(operator_2);
        let new_voter_address = signer::address_of(new_voter);
        vesting::setup(
            aptos_framework, &vector[owner_address, operator_1_address, operator_2_address, new_voter_address]);
        staking_contract::setup_staking_contract(aptos_framework, owner, operator_2, INITIAL_BALANCE, 0);

        let vesting_contract_2 = vesting::setup_vesting_contract(owner, &vector[@12], &vector[INITIAL_BALANCE], owner_address, 0);
        vesting::update_operator(owner, vesting_contract_2, operator_2_address, 0);

        set_operator(owner, operator_1_address, new_voter_address);
        // No staking or vesting contracts changed.
        let stake_pool_address = staking_contract::stake_pool_address(owner_address, operator_2_address);
        assert!(stake::get_delegated_voter(stake_pool_address) == operator_2_address, 0);
        assert!(vesting::voter(vesting_contract_2) == owner_address, 1);
    }
```
