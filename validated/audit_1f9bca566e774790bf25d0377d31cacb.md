No vulnerability found for this question.

**Analysis:**

`delegation_pool.move` never caches the operator or voter address — every read of `stake::get_operator(pool_address)` and `stake::get_delegated_voter(pool_address)` is a live lookup against the current `stake::StakePool` resource, evaluated at the exact moment `synchronize_delegation_pool` (or any function that calls it) executes.

Specifically, in `synchronize_delegation_pool`, the commission is credited to whichever address `stake::get_operator(pool_address)` returns at that instant: [1](#0-0) 

Critically, `set_operator` (the only function that can change the operator) forces a synchronization *before* performing the operator swap on the stake pool, guaranteeing the old operator is paid all uncommitted commission under its own address prior to the switch — there's no window where a stale operator reference could siphon rewards intended for the new operator, or vice versa: [2](#0-1) 

This exact ordering is validated by the existing `test_change_operator` test, which asserts that pre-change rewards accrue to `old_operator_address` and post-change rewards accrue to `new_operator_address`, with no commission attributed to the wrong party across the transition: [3](#0-2) 

Since every read is live (not cached) and every mutating entrypoint (`add_stake`, `unlock`, `reactivate_stake`, `withdraw`, `set_operator`, `update_commission_percentage`, etc.) triggers `synchronize_delegation_pool` first, there is no reachable state where an unprivileged caller can force commission distribution or voter association against a stale operator/voter. The "operator change synchronized late" scenario described in the question is precisely what the pre-change `synchronize_delegation_pool` call in `set_operator` is designed to prevent, and no unprivileged function bypasses this ordering.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1261-1266)
```text
        let pool_address = get_owned_pool_address(signer::address_of(owner));
        // synchronize delegation and stake pools before any user operation
        // ensure the old operator is paid its uncommitted commission rewards
        synchronize_delegation_pool(pool_address);
        stake::set_operator(&retrieve_stake_pool_owner(borrow_global<DelegationPool>(pool_address)), new_operator);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1949-1956)
```text
        // reward operator its commission out of uncommitted active rewards (`add_stake` fees already excluded)
        buy_in_active_shares(pool, beneficiary_for_operator(stake::get_operator(pool_address)), commission_active);
        // reward operator its commission out of uncommitted pending_inactive rewards
        buy_in_pending_inactive_shares(
            pool,
            beneficiary_for_operator(stake::get_operator(pool_address)),
            commission_pending_inactive
        );
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L3692-3712)
```text
        // change operator
        set_operator(old_operator, new_operator_address);

        end_aptos_epoch();
        stake::assert_stake_pool(pool_address, 10303010000, 0, 0, 10303010000);
        // 25426500 active stake * 1.008735 and 25426500 pending_inactive stake * 1.008735
        assert_delegation(old_operator_address, pool_address, 25648600, 0, 25648600);
        // 102010000 active rewards * 0.1265 and 102010000 pending_inactive rewards * 0.1265
        assert_delegation(new_operator_address, pool_address, 12904265, 0, 12904265);

        // restake `new_operator` commission rewards
        synchronize_delegation_pool(pool_address);

        end_aptos_epoch();
        stake::assert_stake_pool(pool_address, 10406040100, 0, 0, 10406040100);
        // 25648600 active stake * 1.008735 and 25648600 pending_inactive stake * 1.008735
        assert_delegation(old_operator_address, pool_address, 25872641, 0, 25872641);
        // 103030100 active rewards * 0.1265 and 12904265 active stake * 1.008735
        // 103030100 pending_inactive rewards * 0.1265 and 12904265 pending_inactive stake * 1.008735
        assert_delegation(new_operator_address, pool_address, 26050290, 0, 26050290);
    }
```
