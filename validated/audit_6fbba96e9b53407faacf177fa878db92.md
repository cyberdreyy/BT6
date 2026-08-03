[1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1750-1770)
```text
        let new_shares = pending_inactive_shares_pool(pool).amount_to_shares(coins_amount);
        // never create a new pending withdrawal unless delegator owns some pending_inactive shares
        if (new_shares == 0) { return 0 };

        // Always update governance records before any change to the shares pool.
        let pool_address = get_pool_address(pool);
        if (partial_governance_voting_enabled(pool_address)) {
            update_governance_records_for_buy_in_pending_inactive_shares(pool, pool_address, new_shares, shareholder);
        };

        // cannot buy inactive shares, only pending_inactive at current lockup cycle
        pending_inactive_shares_pool_mut(pool).buy_in(shareholder, coins_amount);

        // execute the pending withdrawal if exists and is inactive before creating a new one
        execute_pending_withdrawal(pool, shareholder);

        // save observed lockup cycle for the new pending withdrawal
        let observed_lockup_cycle = pool.observed_lockup_cycle;
        assert!(*pool.pending_withdrawals.borrow_mut_with_default(shareholder, observed_lockup_cycle) == observed_lockup_cycle,
            error::invalid_state(EPENDING_WITHDRAWAL_EXISTS)
        );
```
