No vulnerability found for this question.

**Analysis:**

The proposed attack path does not exist because of how `withdraw` and `withdraw_internal` are structured atomically within a single transaction.

1. `withdraw` first calls `synchronize_delegation_pool(pool_address)`, which advances `pool.observed_lockup_cycle` if the stake pool's lockup has already ended, *before* any withdrawal-specific state is read. [1](#0-0) 

2. Only after this synchronization does `withdraw_internal` call `pending_withdrawal_exists(pool, delegator_address)`, which reads that specific delegator's own stored OLC from `pool.pending_withdrawals` — not a cached/stale value from before the call. [2](#0-1) [3](#0-2) 

3. `redeem_inactive_shares` is then invoked with that delegator-specific `withdrawal_olc`, and it uses this exact OLC to index into `pool.inactive_shares` (a `Table<ObservedLockupCycle, pool_u64::Pool>`) — never the pool's current `observed_lockup_cycle` directly. This is precisely the mechanism that guarantees each delegator's redemption draws only from the inactive-shares pool corresponding to *their own* OLC, regardless of how many lockup cycles have since elapsed on the pool. [4](#0-3) 

4. There is no window for "stale OLC" confusion because Move transactions execute atomically — `synchronize_delegation_pool` and the subsequent read of `withdrawal_olc` happen within the same transaction with no intervening state change. The delegator's OLC is not "one that no longer matches `pool.observed_lockup_cycle`" in a way that causes misattribution — it's precisely tracked per-delegator and correctly dereferenced.

5. This exact scenario — multiple delegators unlocking across different, expired OLC boundaries and withdrawing later — is directly covered by `test_withdraw_multiple_delegators`, which unlocks stake for `delegator1` in OLC 0 and `delegator2` in OLC 1, advances through multiple lockup cycles, and asserts each delegator's withdrawal draws exactly and only from their own OLC's inactive-shares pool with correct balances. [5](#0-4) 

This invariant is also explicitly documented and formally specified: the pending withdrawal's OLC index cannot exceed the current pool OLC, and a delegator only ever holds inactive shares in the one pool designated as their pending withdrawal. [6](#0-5) 

No unprivileged input sequencing can force `redeem_inactive_shares` to redeem from the wrong OLC's pool — the OLC used is always the one stored against that specific delegator, correctly synchronized before use.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1614-1623)
```text
    public entry fun withdraw(
        delegator: &signer,
        pool_address: address,
        amount: u64
    ) acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage {
        assert!(amount > 0, error::invalid_argument(EWITHDRAW_ZERO_STAKE));
        // synchronize delegation and stake pools before any user operation
        synchronize_delegation_pool(pool_address);
        withdraw_internal(borrow_global_mut<DelegationPool>(pool_address), signer::address_of(delegator), amount);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1625-1649)
```text
    fun withdraw_internal(
        pool: &mut DelegationPool,
        delegator_address: address,
        amount: u64
    ) acquires GovernanceRecords {
        // TODO: recycle storage when a delegator fully exits the delegation pool.
        // short-circuit if amount to withdraw is 0 so no event is emitted
        if (amount == 0) { return };

        let pool_address = get_pool_address(pool);
        let (withdrawal_exists, withdrawal_olc) = pending_withdrawal_exists(pool, delegator_address);
        // exit if no withdrawal or (it is pending and cannot withdraw pending_inactive stake from stake pool)
        if (!(
            withdrawal_exists &&
                (withdrawal_olc.index < pool.observed_lockup_cycle.index || can_withdraw_pending_inactive(pool_address))
        )) { return };

        if (withdrawal_olc.index == pool.observed_lockup_cycle.index) {
            amount = coins_to_redeem_to_ensure_min_stake(
                pending_inactive_shares_pool(pool),
                delegator_address,
                amount,
            )
        };
        amount = redeem_inactive_shares(pool, delegator_address, amount, withdrawal_olc);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1690-1696)
```text
    fun pending_withdrawal_exists(pool: &DelegationPool, delegator_address: address): (bool, ObservedLockupCycle) {
        if (pool.pending_withdrawals.contains(delegator_address)) {
            (true, *pool.pending_withdrawals.borrow(delegator_address))
        } else {
            (false, olc_with_index(0))
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1818-1860)
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

        // if entirely reactivated pending_inactive stake or withdrawn inactive one,
        // re-enable unlocking for delegator by deleting this pending withdrawal
        if (inactive_shares.shares(shareholder) == 0) {
            // 2. a delegator owns inactive/pending_inactive shares only at the OLC of its pending withdrawal
            // 1 & 2: the pending withdrawal itself has been emptied of shares and can be safely deleted
            pool.pending_withdrawals.remove(shareholder);
        };
        // destroy inactive shares pool of past OLC if all its stake has been withdrawn
        if (lockup_cycle.index < pool.observed_lockup_cycle.index && inactive_shares.total_coins() == 0) {
            pool.inactive_shares.remove(lockup_cycle).destroy_empty();
        };

        redeemed_coins
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L3451-3501)
```text
        // create the pending withdrawal of delegator 1 in lockup cycle 0
        unlock(delegator1, pool_address, 150 * ONE_APT);
        assert_pending_withdrawal(delegator1_address, pool_address, true, 0, false, 14999999999);

        // move to lockup cycle 1
        timestamp::fast_forward_seconds(LOCKUP_CYCLE_SECONDS);
        end_aptos_epoch();

        // create the pending withdrawal of delegator 2 in lockup cycle 1
        unlock(delegator2, pool_address, 150 * ONE_APT);
        assert_pending_withdrawal(delegator2_address, pool_address, true, 1, false, 14999999999);
        // 14999999999 pending_inactive stake * 1.01
        assert_pending_withdrawal(delegator1_address, pool_address, true, 0, true, 15149999998);

        // move to lockup cycle 2
        timestamp::fast_forward_seconds(LOCKUP_CYCLE_SECONDS);
        end_aptos_epoch();

        assert_pending_withdrawal(delegator2_address, pool_address, true, 1, true, 15149999998);
        assert_pending_withdrawal(delegator1_address, pool_address, true, 0, true, 15149999998);

        // both delegators who unlocked at different lockup cycles should be able to withdraw their stakes
        withdraw(delegator1, pool_address, 15149999998);
        withdraw(delegator2, pool_address, 5149999998);

        assert_pending_withdrawal(delegator2_address, pool_address, true, 1, true, 10000000001);
        assert_pending_withdrawal(delegator1_address, pool_address, false, 0, false, 0);
        assert!(coin::balance<AptosCoin>(delegator1_address) == 15149999998, 0);
        assert!(coin::balance<AptosCoin>(delegator2_address) == 5149999997, 0);

        // recreate the pending withdrawal of delegator 1 in lockup cycle 2
        unlock(delegator1, pool_address, 100 * ONE_APT);

        // move to lockup cycle 3
        timestamp::fast_forward_seconds(LOCKUP_CYCLE_SECONDS);
        end_aptos_epoch();

        assert_pending_withdrawal(delegator2_address, pool_address, true, 1, true, 10000000001);
        // 9999999999 pending_inactive stake * 1.01
        assert_pending_withdrawal(delegator1_address, pool_address, true, 2, true, 10099999998);

        // withdraw inactive stake of delegator 2 left from lockup cycle 1 in cycle 3
        withdraw(delegator2, pool_address, 10000000001);
        assert!(coin::balance<AptosCoin>(delegator2_address) == 15149999998, 0);
        assert_pending_withdrawal(delegator2_address, pool_address, false, 0, false, 0);

        // withdraw inactive stake of delegator 1 left from previous lockup cycle
        withdraw(delegator1, pool_address, 10099999998);
        assert!(coin::balance<AptosCoin>(delegator1_address) == 15149999998 + 10099999998, 0);
        assert_pending_withdrawal(delegator1_address, pool_address, false, 0, false, 0);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.spec.move (L18-56)
```text
    /// No.: 3
    /// Requirement: A delegator holds shares exclusively in one inactive shares pool, which could either be an already
    /// inactive pool or the pending_inactive pool.
    /// Criticality: High
    /// Implementation: The get_stake function returns the inactive stake owned by a delegator and checks which
    /// state the shares are in via the get_pending_withdrawal function.
    /// Enforcement: Audited that either inactive or pending_inactive stake after invoking the get_stake function is
    /// zero and both are never non-zero.
    ///
    /// No.: 4
    /// Requirement: The specific pool in which the delegator possesses inactive shares becomes designated as the
    /// pending withdrawal pool for that delegator.
    /// Criticality: Medium
    /// Implementation: The get_pending_withdrawal function checks if any pending withdrawal exists for a delegate
    /// address and if there is neither inactive nor pending_inactive stake, the pending_withdrawal_exists returns
    /// false.
    /// Enforcement: This has been audited.
    ///
    /// No.: 5
    /// Requirement: The existence of a pending withdrawal implies that it is associated with a pool where the
    /// delegator possesses inactive shares.
    /// Criticality: Medium
    /// Implementation: In the get_pending_withdrawal function, if withdrawal_exists is true, the function returns
    /// true and a non-zero amount
    /// Enforcement: get_pending_withdrawal has been audited.
    ///
    /// No.: 6
    /// Requirement: An inactive shares pool should have coins allocated to it; otherwise, it should become deleted.
    /// Criticality: Medium
    /// Implementation: The redeem_inactive_shares function has a check that destroys the inactive shares pool,
    /// given that it is empty.
    /// Enforcement: shares pools have been audited.
    ///
    /// No.: 7
    /// Requirement: The index of the pending withdrawal will not exceed the current OLC on DelegationPool.
    /// Criticality: High
    /// Implementation: The get_pending_withdrawal function has a check which ensures that withdrawal_olc.index <
    /// pool.observed_lockup_cycle.index.
    /// Enforcement: This has been audited.
```
