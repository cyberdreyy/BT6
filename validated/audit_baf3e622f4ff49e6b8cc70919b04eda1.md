[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L43-49)
```text
 - no delegator can have unlocking and/or unlocked stake (pending withdrawals) in different OLCs. This ensures
delegators do not have to keep track of the OLCs when they unlocked. When creating a new pending withdrawal,
the existing one is executed (withdrawn) if is already inactive.
 - <code>add_stake</code> fees are always refunded, but only after the epoch when they have been charged ends.
 - withdrawing pending_inactive stake (when validator had gone inactive before its lockup expired)
does not inactivate any stake additional to the requested one to ensure OLC would not advance indefinitely.
 - the pending withdrawal exists at an OLC iff delegator owns some shares within the shares pool of that OLC.
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1558-1563)
```text
        amount = redeem_active_shares(pool, delegator_address, amount);

        stake::unlock(&retrieve_stake_pool_owner(pool), amount);

        buy_in_pending_inactive_shares(pool, delegator_address, amount);
        assert_min_pending_inactive_balance(pool, delegator_address);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1671-1671)
```text
        aptos_account::transfer(stake_pool_owner, delegator_address, amount);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1709-1718)
```text
    /// Execute the pending withdrawal of `delegator_address` on delegation pool `pool`
    /// if existing and already inactive to allow the creation of a new one.
    /// `pending_inactive` stake would be left untouched even if withdrawable and should
    /// be explicitly withdrawn by delegator
    fun execute_pending_withdrawal(pool: &mut DelegationPool, delegator_address: address) acquires GovernanceRecords {
        let (withdrawal_exists, withdrawal_olc) = pending_withdrawal_exists(pool, delegator_address);
        if (withdrawal_exists && withdrawal_olc.index < pool.observed_lockup_cycle.index) {
            withdraw_internal(pool, delegator_address, MAX_U64);
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1750-1764)
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
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1766-1771)
```text
        // save observed lockup cycle for the new pending withdrawal
        let observed_lockup_cycle = pool.observed_lockup_cycle;
        assert!(*pool.pending_withdrawals.borrow_mut_with_default(shareholder, observed_lockup_cycle) == observed_lockup_cycle,
            error::invalid_state(EPENDING_WITHDRAWAL_EXISTS)
        );

```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1818-1846)
```text
    fun redeem_inactive_shares(
        pool: &mut DelegationPool,
        shareholder: address,
        coins_amount: u64,
        lockup_cycle: ObservedLockupCycle,
    ): u64 acquires GovernanceRecords {
        let shares_to_redeem = amount_to_shares_to_redeem(
            pool.inactive_shares.borrow(lockup_cycle),
            shareholder,
            coins_amount);
        // silently exit if not a shareholder otherwise redeem would fail with `ESHAREHOLDER_NOT_FOUND`
        if (shares_to_redeem == 0) return 0;

        // Always update governance records before any change to the shares pool.
        let pool_address = get_pool_address(pool);
        // Only redeem shares from the pending_inactive pool at `lockup_cycle` == current OLC.
        if (partial_governance_voting_enabled(pool_address) && lockup_cycle.index == pool.observed_lockup_cycle.index) {
            update_governanace_records_for_redeem_pending_inactive_shares(
                pool,
                pool_address,
                shares_to_redeem,
                shareholder
            );
        };

        let inactive_shares = pool.inactive_shares.borrow_mut(lockup_cycle);
        // 1. reaching here means delegator owns inactive/pending_inactive shares at OLC `lockup_cycle`
        let redeemed_coins = inactive_shares.redeem_shares(shareholder, shares_to_redeem);

```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L2806-2817)
```text
        withdraw(validator, pool_address, 15301499997);
        assert!(coin::balance<AptosCoin>(validator_address) == 15301499997, 0);
        assert_delegation(validator_address, pool_address, 15403510001, 0, 0);
        // pending withdrawal has been executed and deleted
        assert_pending_withdrawal(validator_address, pool_address, false, 0, false, 0);
        // inactive shares pool on OLC 0 has been deleted because its stake has been withdrawn
        assert_inactive_shares_pool(pool_address, 0, false, 0);

        // new pending withdrawal can be created on lockup cycle 1
        unlock(validator, pool_address, 5403510001);
        assert_delegation(validator_address, pool_address, 10000000000, 0, 5403510000);
        assert_pending_withdrawal(validator_address, pool_address, true, 1, false, 5403510000);
```
