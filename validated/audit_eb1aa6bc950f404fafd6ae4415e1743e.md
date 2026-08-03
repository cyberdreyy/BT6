Based on my review of `delegation_pool.move`, this reported issue does not hold up as a valid vulnerability.

**Key facts from the code:**

1. The `add_stake` fee mechanism only involves the single `active_shares` pool — there is no per-OLC (observed lockup cycle) splitting of the active pool. OLC (Observed Lockup Cycle) tracking only applies to `pending_inactive`/`inactive` pools, as documented explicitly: "one of `active` + `pending_active` stake" is a single, undivided pool, while inactive pools are "FOR each past observed lockup cycle." [1](#0-0) 

2. `add_stake` buys shares for the depositor and simultaneously buys placeholder shares for `NULL_SHAREHOLDER` for the fee amount — both in the very same `active_shares` pool, with no OLC-based routing at all. [2](#0-1) 

3. The refund happens in `synchronize_delegation_pool`, gated purely on whether `pending_active == 0` on the underlying stake pool (i.e., whether the previous epoch has ended and the added stake has been promoted from `pending_active` to `active`) — not on any OLC/lockup-cycle context: [3](#0-2) 

4. Once redeemed, those coins are folded back into `active_shares.update_total_coins(...)` and distributed pro-rata to whoever holds active shares at that moment — this is the explicitly documented intended behavior, not a bug: fees are deliberately "fed back into the pool as placeholder for the rewards the remaining stake would have earned if active" and later "distribute[d] ... together with the active rewards when this epoch ends in order to appreciate all shares on the active pool atomically."
<invoke name="codebase_search">
<parameter name="query">placeholder</parameter>
</invoke>

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L17-21)
```text
In order to distinguish between stakes in different states and route rewards accordingly,
separate pool_u64 pools are used for individual stake states:
1. one of <code>active</code> + <code>pending_active</code> stake
2. one of <code>inactive</code> stake FOR each past observed lockup cycle (OLC) on the stake pool
3. one of <code>pending_inactive</code> stake scheduled during this ongoing OLC
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1494-1511)
```text
        // fee to be charged for adding `amount` stake on this delegation pool at this epoch
        let add_stake_fee = get_add_stake_fee(pool_address, amount);

        let pool = borrow_global_mut<DelegationPool>(pool_address);

        // stake the entire amount to the stake pool
        aptos_account::transfer(delegator, pool_address, amount);
        stake::add_stake(&retrieve_stake_pool_owner(pool), amount);

        // but buy shares for delegator just for the remaining amount after fee
        buy_in_active_shares(pool, delegator_address, amount - add_stake_fee);
        assert_min_active_balance(pool, delegator_address);

        // grant temporary ownership over `add_stake` fees to a separate shareholder in order to:
        // - not mistake them for rewards to pay the operator from
        // - distribute them together with the `active` rewards when this epoch ends
        // in order to appreciate all shares on the active pool atomically
        buy_in_active_shares(pool, NULL_SHAREHOLDER, add_stake_fee);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1930-1937)
```text
        // zero `pending_active` stake indicates that either there are no `add_stake` fees or
        // previous epoch has ended and should release the shares owning the existing fees
        let (_, _, pending_active, _) = stake::get_stake(pool_address);
        if (pending_active == 0) {
            // renounce ownership over the `add_stake` fees by redeeming all shares of
            // the special shareholder, implicitly their equivalent coins, out of the active shares pool
            redeem_active_shares(pool, NULL_SHAREHOLDER, MAX_U64);
        };
```
