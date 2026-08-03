[1](#0-0) [2](#0-1) [3](#0-2) [3](#0-2)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L43-45)
```text
 - no delegator can have unlocking and/or unlocked stake (pending withdrawals) in different OLCs. This ensures
delegators do not have to keep track of the OLCs when they unlocked. When creating a new pending withdrawal,
the existing one is executed (withdrawn) if is already inactive.
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1664-1676)
```text
            stake::withdraw(stake_pool_owner, amount);
            // restore excess stake to the pending_inactive state
            stake::unlock(stake_pool_owner, pending_inactive);
        } else {
            // no excess stake if `stake::withdraw` does not inactivate at all
            stake::withdraw(stake_pool_owner, amount);
        };
        aptos_account::transfer(stake_pool_owner, delegator_address, amount);

        // commit withdrawal of possibly inactive stake to the `total_coins_inactive`
        // known by the delegation pool in order to not mistake it for slashing at next synchronization
        let (_, inactive, _, _) = stake::get_stake(pool_address);
        pool.total_coins_inactive = inactive;
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L3047-3051)
```text
        // unlock stake in the new lockup cycle (the pending withdrawal is executed)
        unlock(validator, pool_address, 100 * ONE_APT);
        assert!(coin::balance<AptosCoin>(validator_address) == 15149999998, 0);
        assert_delegation(validator_address, pool_address, 10402000002, 0, 9999999999);
        assert_pending_withdrawal(validator_address, pool_address, true, 1, false, 9999999999);
```
