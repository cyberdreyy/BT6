No vulnerability found for this question.

**Analysis:**

`synchronize_delegation_pool` is intentionally permissionless — it's called at the start of virtually every delegation-pool entry function (`unlock`, `vote`, `enable_partial_governance_voting`, `set_operator`, `update_commission_percentage`, etc.) specifically so that any user's transaction settles pending rewards/commission against current on-chain state before acting. [1](#0-0) 

Commission is always computed from the actual drift between the stake pool and the delegation pool's internal share totals (`calculate_stake_pool_drift`), and paid to whichever address `stake::get_operator(pool_address)` currently resolves to (via `beneficiary_for_operator`). Since the operator's identity is not persisted inside `DelegationPool` but read live from the `stake` module at the moment `synchronize_delegation_pool` runs, there's no possibility of a stale/future operator being used. [2](#0-1) 

Critically, `set_operator` itself forces a synchronization *before* the actual operator swap on the stake pool: it calls `synchronize_delegation_pool(pool_address)` first (settling all commission earned up to that point to the outgoing operator/beneficiary), and only afterward calls `stake::set_operator(...)` to perform the actual role change. [3](#0-2) 

This ordering means there is no window in which rewards accrued under the old operator could ever be attributed to a "new" operator that hasn't taken effect yet — `stake::get_operator` only changes at the point `stake::set_operator` executes, which is strictly after the settling sync. If an unprivileged delegator calls `synchronize_delegation_pool` earlier (e.g., in a separate transaction before the owner's `set_operator` transaction lands), it simply settles commission for the currently-active operator early — the same operator that `set_operator`'s internal sync would have paid anyway. It cannot redirect commission to a "wrong" operator because the operator identity used is always whatever is currently on-chain at call time, never a future or past value.

This exact behavior — old operator gets commission for pre-switch rewards, new operator gets commission for post-switch rewards — is explicitly covered by the test `test_set_beneficiary_for_operator`, which switches operator mid-flow and asserts rewards route to the correct party at each stage. [4](#0-3) 

The invariant "commission tracks the operator who earned it" is therefore preserved by construction: the permissionless, idempotent nature of `synchronize_delegation_pool` combined with the mandatory pre-swap sync in `set_operator` closes the timing window the question hypothesizes. No misrouting of commission to an outgoing operator's beneficiary is possible through this path.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1256-1266)
```text
    /// Allows an owner to change the operator of the underlying stake pool.
    public entry fun set_operator(
        owner: &signer,
        new_operator: address
    ) acquires DelegationPoolOwnership, DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage {
        let pool_address = get_owned_pool_address(signer::address_of(owner));
        // synchronize delegation and stake pools before any user operation
        // ensure the old operator is paid its uncommitted commission rewards
        synchronize_delegation_pool(pool_address);
        stake::set_operator(&retrieve_stake_pool_owner(borrow_global<DelegationPool>(pool_address)), new_operator);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1917-1956)
```text
    public entry fun synchronize_delegation_pool(
        pool_address: address
    ) acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage {
        assert_delegation_pool_exists(pool_address);
        let pool = borrow_global_mut<DelegationPool>(pool_address);
        let (
            lockup_cycle_ended,
            active,
            pending_inactive,
            commission_active,
            commission_pending_inactive
        ) = calculate_stake_pool_drift(pool);

        // zero `pending_active` stake indicates that either there are no `add_stake` fees or
        // previous epoch has ended and should release the shares owning the existing fees
        let (_, _, pending_active, _) = stake::get_stake(pool_address);
        if (pending_active == 0) {
            // renounce ownership over the `add_stake` fees by redeeming all shares of
            // the special shareholder, implicitly their equivalent coins, out of the active shares pool
            redeem_active_shares(pool, NULL_SHAREHOLDER, MAX_U64);
        };

        // distribute rewards remaining after commission, to delegators (to already existing shares)
        // before buying shares for the operator for its entire commission fee
        // otherwise, operator's new shares would additionally appreciate from rewards it does not own

        // update total coins accumulated by `active` + `pending_active` shares
        // redeemed `add_stake` fees are restored and distributed to the rest of the pool as rewards
        pool.active_shares.update_total_coins(active - commission_active);
        // update total coins accumulated by `pending_inactive` shares at current observed lockup cycle
        pending_inactive_shares_pool_mut(pool).update_total_coins(pending_inactive - commission_pending_inactive);

        // reward operator its commission out of uncommitted active rewards (`add_stake` fees already excluded)
        buy_in_active_shares(pool, beneficiary_for_operator(stake::get_operator(pool_address)), commission_active);
        // reward operator its commission out of uncommitted pending_inactive rewards
        buy_in_pending_inactive_shares(
            pool,
            beneficiary_for_operator(stake::get_operator(pool_address)),
            commission_pending_inactive
        );
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L3782-3792)
```text
        // switch operator to operator2. The rewards should go to operator2 not to the beneficiay of operator1.
        set_operator(operator1, operator2_address);
        end_aptos_epoch();
        unlock(operator2, pool_address, ONE_APT);
        timestamp::fast_forward_seconds(LOCKUP_CYCLE_SECONDS);
        end_aptos_epoch();

        withdraw(operator2, pool_address, ONE_APT);
        assert!(coin::balance<AptosCoin>(beneficiary_address) == ONE_APT - 1, 0);
        assert!(coin::balance<AptosCoin>(operator2_address) == ONE_APT - 1, 0);
    }
```
