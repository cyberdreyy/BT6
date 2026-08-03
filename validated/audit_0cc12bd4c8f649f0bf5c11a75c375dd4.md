[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1621-1650)
```text
        assert!(
            operator_1_balance == INITIAL_BALANCE + commission_for_operator_1,
            operator_1_balance
        );
        stake::assert_stake_pool(
            pool_address,
            new_balance,
            0,
            0,
            commission_for_operator_2
        );
        assert!(
            last_recorded_principal(staker_address, operator_2_address) == new_balance,
            0
        );
        stake::fast_forward_to_unlock(pool_address);

        // Operator 2's commission is distributed.
        distribute(staker_address, operator_2_address);
        let operator_2_balance = coin::balance<AptosCoin>(operator_2_address);
        new_balance = with_rewards(new_balance);
        commission_for_operator_2 = with_rewards(commission_for_operator_2);
        assert!(
            operator_2_balance
                == INITIAL_BALANCE + commission_for_operator_2
                    + commission_on_operator_1_distribution,
            operator_2_balance
        );
        stake::assert_stake_pool(pool_address, new_balance, 0, 0, 0);
    }
```
