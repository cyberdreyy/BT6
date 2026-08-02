No vulnerability found for this question.

**Analysis:**

The `unlock` entry function derives `delegator_address` exclusively from the transaction signer, never from a caller-supplied parameter: [1](#0-0) 

This `delegator_address` is threaded through `unlock_internal` → `redeem_active_shares` (redeems shares from the caller's own active-shares balance) → `buy_in_pending_inactive_shares` (buys pending_inactive shares only for that same caller address): [2](#0-1) 

`redeem_active_shares` operates strictly on the `shareholder` argument it's given (the caller), and if that shareholder has zero shares, `amount_to_shares_to_redeem` returns 0 shares and the function silently exits, transferring nothing: [3](#0-2) [4](#0-3) 

There is no code path in `unlock`/`unlock_internal` that accepts an address parameter distinct from the transaction signer, so a delegator cannot "specify" another delegator's shares — the shares redeemed and the `pending_withdrawals` table entry created via `buy_in_pending_inactive_shares` are always keyed to `signer::address_of(delegator)`, i.e., the caller itself: [5](#0-4) 

The existing test suite explicitly validates this isolation, asserting that one delegator cannot withdraw or otherwise touch stake unlocked by another, even when both own pending_inactive shares in the pool: [6](#0-5) 

Since `unlock` has no attacker-controlled "victim" argument and all accounting keys off `signer::address_of`, a zero-active-share delegator calling `unlock` simply results in a no-op (0 shares redeemed, 0 stake moved, no pending withdrawal entry created), and there is no way to corrupt another delegator's `pending_withdrawals` table entry.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1525-1538)
```text
    public entry fun unlock(
        delegator: &signer,
        pool_address: address,
        amount: u64
    ) acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage {
        // short-circuit if amount to unlock is 0 so no event is emitted
        if (amount == 0) { return };

        // synchronize delegation and stake pools before any user operation
        synchronize_delegation_pool(pool_address);

        let delegator_address = signer::address_of(delegator);
        unlock_internal(delegator_address, pool_address, amount);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1540-1572)
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

        let pool = borrow_global_mut<DelegationPool>(pool_address);
        amount = coins_to_transfer_to_ensure_min_stake(
            &pool.active_shares,
            pending_inactive_shares_pool(pool),
            delegator_address,
            amount,
        );
        amount = redeem_active_shares(pool, delegator_address, amount);

        stake::unlock(&retrieve_stake_pool_owner(pool), amount);

        buy_in_pending_inactive_shares(pool, delegator_address, amount);
        assert_min_pending_inactive_balance(pool, delegator_address);

        event::emit(
            UnlockStake {
                pool_address,
                delegator_address,
                amount_unlocked: amount,
            },
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1745-1773)
```text
    fun buy_in_pending_inactive_shares(
        pool: &mut DelegationPool,
        shareholder: address,
        coins_amount: u64,
    ): u128 acquires GovernanceRecords {
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

        new_shares
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1777-1788)
```text
    fun amount_to_shares_to_redeem(
        shares_pool: &pool_u64::Pool,
        shareholder: address,
        coins_amount: u64,
    ): u128 {
        if (coins_amount >= shares_pool.balance(shareholder)) {
            // cap result at total shares of shareholder to pass `EINSUFFICIENT_SHARES` on subsequent redeem
            shares_pool.shares(shareholder)
        } else {
            shares_pool.amount_to_shares(coins_amount)
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1794-1810)
```text
    fun redeem_active_shares(
        pool: &mut DelegationPool,
        shareholder: address,
        coins_amount: u64,
    ): u64 acquires GovernanceRecords {
        let shares_to_redeem = amount_to_shares_to_redeem(&pool.active_shares, shareholder, coins_amount);
        // silently exit if not a shareholder otherwise redeem would fail with `ESHAREHOLDER_NOT_FOUND`
        if (shares_to_redeem == 0) return 0;

        // Always update governance records before any change to the shares pool.
        let pool_address = get_pool_address(pool);
        if (partial_governance_voting_enabled(pool_address)) {
            update_governanace_records_for_redeem_active_shares(pool, pool_address, shares_to_redeem, shareholder);
        };

        pool.active_shares.redeem_shares(shareholder, shares_to_redeem)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L3090-3103)
```text
        // check cannot withdraw inactive stake unlocked by others
        withdraw(delegator, pool_address, MAX_U64);
        assert_delegation(delegator_address, pool_address, 200 * ONE_APT, 0, 0);
        assert_delegation(validator_address, pool_address, 90899999999, 10100000000, 0);

        unlock(delegator, pool_address, 100 * ONE_APT);
        assert_delegation(delegator_address, pool_address, 10000000000, 0, 9999999999);
        assert_delegation(validator_address, pool_address, 90900000000, 10100000000, 0);
        assert_pending_withdrawal(delegator_address, pool_address, true, 1, false, 9999999999);

        // check cannot withdraw inactive stake unlocked by others even if owning pending_inactive
        withdraw(delegator, pool_address, MAX_U64);
        assert_delegation(delegator_address, pool_address, 10000000000, 0, 9999999999);
        assert_delegation(validator_address, pool_address, 90900000000, 10100000000, 0);
```
