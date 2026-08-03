[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L661-663)
```text
        };
        active = pool.active_shares.shares_to_amount_with_total_stats(delegator_active_shares, active - commission_active, total_active_shares);

```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L669-681)
```text
        // should also include commission rewards in case of the operator account
        // operator rewards are actually used to buy shares which is introducing
        // some imprecision (received stake would be slightly less)
        // but adding rewards onto the existing stake is still a good approximation
        if (delegator_address == beneficiary_for_operator(get_operator(pool_address))) {
            active += commission_active;
            // in-flight pending_inactive commission can coexist with already inactive withdrawal
            if (lockup_cycle_ended) {
                inactive += commission_pending_inactive
            } else {
                pending_inactive += commission_pending_inactive
            }
        };
```
