[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1664-1671)
```text
            stake::withdraw(stake_pool_owner, amount);
            // restore excess stake to the pending_inactive state
            stake::unlock(stake_pool_owner, pending_inactive);
        } else {
            // no excess stake if `stake::withdraw` does not inactivate at all
            stake::withdraw(stake_pool_owner, amount);
        };
        aptos_account::transfer(stake_pool_owner, delegator_address, amount);
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1197-1203)
```text
        // Cap withdraw amount by total inactive coins.
        withdraw_amount = min(withdraw_amount, coin::value(&stake_pool.inactive));
        if (withdraw_amount == 0) return coin::zero<AptosCoin>();

        event::emit(WithdrawStake { pool_address, amount_withdrawn: withdraw_amount });

        coin::extract(&mut stake_pool.inactive, withdraw_amount)
```
