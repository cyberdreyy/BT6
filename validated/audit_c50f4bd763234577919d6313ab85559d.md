[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_proxy.move (L155-159)
```text
        set_operator(owner, operator_1_address, new_operator_address);
        // No staking or vesting contracts changed.
        assert!(!staking_contract::staking_contract_exists(owner_address, new_operator_address), 0);
        assert!(staking_contract::staking_contract_exists(owner_address, operator_2_address), 1);
        assert!(vesting::operator(vesting_contract_2) == operator_2_address, 2);
```

**File:** aptos-move/framework/aptos-framework/sources/staking_proxy.move (L193-204)
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
```
