[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L143-144)
```text
    /// There is a pending withdrawal to be executed before `unlock`ing any new stake.
    const EPENDING_WITHDRAWAL_EXISTS: u64 = 4;
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L664-667)
```text
        // get state and stake (0 if there is none) of the pending withdrawal
        let (withdrawal_inactive, withdrawal_stake) = get_pending_withdrawal(pool_address, delegator_address);
        // report non-active stakes accordingly to the state of the pending withdrawal
        let (inactive, pending_inactive) = if (withdrawal_inactive) (withdrawal_stake, 0) else (0, withdrawal_stake);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L877-884)
```text
        move_to(&stake_pool_signer, DelegationPool {
            active_shares: pool_u64::create_with_scaling_factor(SHARES_SCALING_FACTOR),
            observed_lockup_cycle: olc_with_index(0),
            inactive_shares,
            pending_withdrawals: table::new<address, ObservedLockupCycle>(),
            stake_pool_signer_cap,
            total_coins_inactive: 0,
            operator_commission_percentage,
```
