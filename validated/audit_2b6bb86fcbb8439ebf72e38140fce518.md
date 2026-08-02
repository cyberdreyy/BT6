[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L152-156)
```text
    /// Slashing (if implemented) should not be applied to already `inactive` stake.
    /// Not only it invalidates the accounting of past observed lockup cycles (OLC),
    /// but is also unfair to delegators whose stake has been inactive before validator started misbehaving.
    /// Additionally, the inactive stake does not count on the voting power of validator.
    const ESLASHED_INACTIVE_STAKE_ON_PAST_OLC: u64 = 7;
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1673-1676)
```text
        // commit withdrawal of possibly inactive stake to the `total_coins_inactive`
        // known by the delegation pool in order to not mistake it for slashing at next synchronization
        let (_, inactive, _, _) = stake::get_stake(pool_address);
        pool.total_coins_inactive = inactive;
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1976-1986)
```text
        // advance lockup cycle on delegation pool if already ended on stake pool (AND stake explicitly inactivated)
        if (lockup_cycle_ended) {
            // capture inactive coins over all ended lockup cycles (including this ending one)
            let (_, inactive, _, _) = stake::get_stake(pool_address);
            pool.total_coins_inactive = inactive;

            // advance lockup cycle on the delegation pool
            pool.observed_lockup_cycle.index += 1;
            // start new lockup cycle with a fresh shares pool for `pending_inactive` stake
            pool.inactive_shares.add(pool.observed_lockup_cycle, pool_u64::create_with_scaling_factor(SHARES_SCALING_FACTOR));
        };
```
