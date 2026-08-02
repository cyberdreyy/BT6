[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L662-683)
```text
        active = pool.active_shares.shares_to_amount_with_total_stats(delegator_active_shares, active - commission_active, total_active_shares);

        // get state and stake (0 if there is none) of the pending withdrawal
        let (withdrawal_inactive, withdrawal_stake) = get_pending_withdrawal(pool_address, delegator_address);
        // report non-active stakes accordingly to the state of the pending withdrawal
        let (inactive, pending_inactive) = if (withdrawal_inactive) (withdrawal_stake, 0) else (0, withdrawal_stake);

        // should also include commission rewards in case of the operator account
        // operator rewards are actually used to buy shares which is introducing
        // some imprecision (received stake would be slightly less)
        // but adding rewards onto the existing stake is still a good approximation
        if (delegator_address == beneficiary_for_operator(get_operator(pool_address))) {
            active += commission_active;
            // in-flight pending_inactive commission can coexist with already inactive withdrawal
            if (lockup_cycle_ended) {
                inactive += commission_pending_inactive
            } else {
                pending_inactive += commission_pending_inactive
            }
        };

        (active, inactive, pending_inactive)
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1507-1511)
```text
        // grant temporary ownership over `add_stake` fees to a separate shareholder in order to:
        // - not mistake them for rewards to pay the operator from
        // - distribute them together with the `active` rewards when this epoch ends
        // in order to appreciate all shares on the active pool atomically
        buy_in_active_shares(pool, NULL_SHAREHOLDER, add_stake_fee);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1540-1545)
```text
    fun unlock_internal(
        delegator_address: address,
        pool_address: address,
        amount: u64
    ) acquires DelegationPool, GovernanceRecords {
        assert!(delegator_address != NULL_SHAREHOLDER, error::invalid_argument(ECANNOT_UNLOCK_NULL_SHAREHOLDER));
```
