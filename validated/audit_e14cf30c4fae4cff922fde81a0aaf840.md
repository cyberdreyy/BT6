No vulnerability found for this question.

**Analysis:**

The `set_operator` function in `delegation_pool.move` already synchronizes the pool before changing the operator, explicitly to prevent this exact issue: [1](#0-0) 

`synchronize_delegation_pool` computes commission owed based on `stake::get_operator(pool_address)` (the **current**, i.e. old, operator) at the time it runs, and pays commission via `buy_in_active_shares`/`buy_in_pending_inactive_shares` to `beneficiary_for_operator(stake::get_operator(pool_address))` — i.e., the old operator's beneficiary — before `stake::set_operator` swaps in the new operator: [2](#0-1) 

Because both the synchronization and the operator swap happen atomically within the single `set_operator` transaction, there is no window in which an unprivileged actor can interleave a call and redirect the pre-change commission. Any subsequent `synchronize_delegation_pool` call (by anyone, since it's permissionless) after `set_operator` will only distribute rewards accrued *after* the operator change — correctly attributing them to the new operator, since `calculate_stake_pool_drift` measures the delta between the stake pool's current state and the delegation pool's last-synced state, which was just brought current inside `set_operator` itself.

This exact scenario — commission accrued under `operator1` being paid to `operator1`'s beneficiary and not leaking to `operator2` — is explicitly covered by the existing test `test_set_beneficiary_for_operator`, which asserts old commission is paid to the old beneficiary and post-switch commission goes to the new operator: [3](#0-2) 

Also, `set_operator` is gated by `get_owned_pool_address(signer::address_of(owner))`, which asserts the caller owns a `DelegationPoolOwnership` capability for that pool — an unprivileged account cannot call it for a pool it doesn't own: [4](#0-3) 

The premise of a "race" also doesn't hold: `synchronize_delegation_pool` and the internal operator swap occur within the same atomic transaction, so there's no attacker-controllable window between them, and even if a separate actor triggers `synchronize_delegation_pool` afterward, it only distributes rewards that accrued under the new operator, which is correct behavior, not corruption.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L524-529)
```text
    #[view]
    /// Return address of the delegation pool owned by `owner` or fail if there is none.
    public fun get_owned_pool_address(owner: address): address acquires DelegationPoolOwnership {
        assert_owner_cap_exists(owner);
        borrow_global<DelegationPoolOwnership>(owner).pool_address
    }
```

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

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1915-1966)
```text
    /// Synchronize delegation and stake pools: distribute yet-undetected rewards to the corresponding internal
    /// shares pools, assign commission to operator and eventually prepare delegation pool for a new lockup cycle.
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

        event::emit_event(
            &mut pool.distribute_commission_events,
            DistributeCommissionEvent {
                pool_address,
                operator: stake::get_operator(pool_address),
                commission_active,
                commission_pending_inactive,
            },
        );
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L3770-3792)
```text
        set_beneficiary_for_operator(operator1, beneficiary_address);
        assert!(beneficiary_for_operator(operator1_address) == beneficiary_address, 0);
        end_aptos_epoch();

        unlock(beneficiary, pool_address, ONE_APT);
        timestamp::fast_forward_seconds(LOCKUP_CYCLE_SECONDS);
        end_aptos_epoch();

        withdraw(beneficiary, pool_address, ONE_APT);
        assert!(coin::balance<AptosCoin>(beneficiary_address) == ONE_APT - 1, 0);
        assert!(coin::balance<AptosCoin>(operator1_address) == ONE_APT - 1, 0);

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
