No vulnerability found for this question.

**Why the premise doesn't hold:**

1. **No sub-transaction interleaving exists.** In Aptos, `unlock()` and `withdraw()` are each separate `entry` transactions. Even though Block-STM executes transactions optimistically in parallel, its validation/commit protocol guarantees the final state is equivalent to *some serial order* of the conflicting transactions — there is no mechanism by which the internal steps of one transaction (e.g., the `execute_pending_withdrawal` call inside `withdraw_internal` or `buy_in_pending_inactive_shares`) can be observed or interleaved mid-execution by another transaction touching the same `DelegationPool` resource [1](#0-0) . Each call to `unlock`/`withdraw` first runs `synchronize_delegation_pool` and then atomically mutates `borrow_global_mut<DelegationPool>` within a single VM execution — nothing can "race" inside that.

2. **The data structure itself prevents the described corruption.** `pending_withdrawals` is a `Table<address, ObservedLockupCycle>` — a single value per delegator key, not a list/set — so it is structurally impossible to hold two pending withdrawals for the same delegator at once [2](#0-1) .

3. **The invariant is actively enforced with an assertion.** `buy_in_pending_inactive_shares` calls `execute_pending_withdrawal` before inserting/updating the table entry, and then asserts the resulting OLC equals the current one: [3](#0-2) . `execute_pending_withdrawal` only forcibly withdraws a stale entry when `withdrawal_olc.index < pool.observed_lockup_cycle.index`, i.e., only when it's from a strictly earlier, already-inactive OLC [4](#0-3) . If the delegator's existing pending withdrawal is at the *current* OLC (same as the new unlock), no auto-execution happens and the unlock simply adds shares to the same existing entry — no second entry is ever created.

4. **`withdraw_internal`'s own guard** re-derives `withdrawal_olc` fresh via `pending_withdrawal_exists` at call time and only proceeds if that OLC is either already past (`< observed_lockup_cycle.index`) or currently withdrawable via `can_withdraw_pending_inactive` [5](#0-4) , all against the single authoritative table entry — there is no path to fork it into two.

Since Aptos's execution model provides serializable semantics for conflicting transactions and the `pending_withdrawals` table plus its accompanying assertions structurally forbid multiple OLC entries per delegator, the described race cannot occur, and no Move test interleaving `unlock`/`withdraw` across an OLC boundary could produce an inconsistent `get_pending_withdrawal` result for a single delegator.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L270-271)
```text
        // Mapping from delegator address to the OLC of its pending withdrawal if having one
        pending_withdrawals: Table<address, ObservedLockupCycle>,
```

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

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1635-1649)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1763-1770)
```text
        // execute the pending withdrawal if exists and is inactive before creating a new one
        execute_pending_withdrawal(pool, shareholder);

        // save observed lockup cycle for the new pending withdrawal
        let observed_lockup_cycle = pool.observed_lockup_cycle;
        assert!(*pool.pending_withdrawals.borrow_mut_with_default(shareholder, observed_lockup_cycle) == observed_lockup_cycle,
            error::invalid_state(EPENDING_WITHDRAWAL_EXISTS)
        );
```
