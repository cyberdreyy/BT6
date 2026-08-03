[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1503-1520)
```text
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
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1540-1549)
```text
    fun unlock_internal(
        delegator_address: address,
        pool_address: address,
        amount: u64
    ) acquires DelegationPool, GovernanceRecords {
        assert!(delegator_address != NULL_SHAREHOLDER, error::invalid_argument(ECANNOT_UNLOCK_NULL_SHAREHOLDER));

        // fail unlock of more stake than `active` on the stake pool
        let (active, _, _, _) = stake::get_stake(pool_address);
        assert!(amount <= active, error::invalid_argument(ENOT_ENOUGH_ACTIVE_STAKE_TO_UNLOCK));
```
