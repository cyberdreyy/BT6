Based on the investigation, I found a concrete accounting-corruption analog in `staking_contract.move` that mirrors the report's underlying pattern (a downstream state-mutating operation is executed under stale/mismatched identity assumptions, letting a party divert value that is not theirs).

### Title
Operator switch lets the new operator steal reward growth on the old operator's already-requested commission - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`update_distribution_pool` assumes every shareholder in a `StakingContract`'s `distribution_pool` other than the current `operator` address is a *staker* withdrawal, and therefore charges them the operator's commission on any value appreciation, transferring the corresponding shares to `operator`. This assumption breaks after `switch_operator`/`switch_operator_with_same_commission`, because a not-yet-paid-out commission entry that legitimately belongs to the **old operator** (keyed by the old operator's own address) is misclassified as a staker entitlement once the contract's `operator` field changes to the new operator. The new operator then skims commission on reward growth that accrued on the old operator's own earned, already-unlocked-but-not-yet-withdrawn commission.

### Finding Description
`request_commission_internal` unlocks the operator's commission from the stake pool and records it as a distribution share owned by the operator's own address: [1](#0-0) 

`switch_operator` flushes only already-*inactive* funds via `distribute_internal`, then calls `request_commission_internal` (adding/confirming the old operator's own pending commission share), and only afterward re-keys the `StakingContract` under the new operator: [2](#0-1) 

If the old operator's previously-requested commission is still `pending_inactive` (has not finished the stake pool's lockup) at switch time, `distribute_internal`'s withdrawal is zero/partial and the old operator's distribution-pool share entry survives the switch, still keyed by the old operator's address, but now living inside a contract whose `operator` field is the new operator.

The bug surfaces the next time anyone calls `distribute()`. `distribute_internal` calls `update_distribution_pool` with the **new** operator's address: [3](#0-2) 

Inside `update_distribution_pool`, every shareholder that isn't equal to the passed-in `operator` (the new operator) is charged commission on its value growth, and the equivalent shares are transferred to `operator`: [4](#0-3) 

Because the old operator's own commission entry is keyed by the old operator's address (not the new operator's), the `shareholder != operator` check fails to recognize it as "the operator's own money," and the growth in value of that entry (rewards earned by the pending_inactive commission stake while it waits to unlock) is skimmed and redirected to the new operator via `pool_u64::transfer_shares`.

### Impact Explanation
This directly corrupts commission/share accounting and credits value to the wrong account: the new operator receives shares (and eventually coins upon `distribute()`) that rightfully belong to the old operator's already-earned, already-requested commission. This is unprivileged from the new operator's perspective (they do nothing but exist as the new operator) and is triggerable purely by the staker's ordinary `switch_operator` / `switch_operator_with_same_commission` call combined with the natural reward-accrual delay before the stake pool's lockup expires — no admin/governance privilege is required. It falls squarely under "Operator commission ... share-accounting corruption that credits the wrong account or traps value."

### Likelihood Explanation
Likelihood is **Low-to-Medium**: it requires (1) an operator to have an outstanding requested-but-not-yet-inactive commission distribution, and (2) the staker to switch operators before that commission is withdrawn/distributed, and (3) at least one reward-earning epoch to elapse before the next `distribute()` call. Stakers naturally switch operators, and commission unlock windows are dictated by the stake pool's lockup duration, so this timing window is realistic and can even be intentionally engineered by a staker colluding with (or acting as) the new operator to profit at the old operator's expense.

### Recommendation
`update_distribution_pool` should not rely solely on comparing a shareholder address to the *current* `operator` field to decide whether to skip commission-charging. Instead, either:
- Flush/force-pay all outstanding distribution shares owned by the *previous* operator (not just already-inactive stake) inside `switch_operator` before re-keying the contract to the new operator, or
- Track distribution-pool entries with an explicit "is-operator-commission" flag (independent of address identity) so that reward growth on an already-recorded commission share is never re-charged commission regardless of which address currently holds the `operator` role.

### Proof of Concept
1. Staker `S` creates a staking contract with operator `O1` and commission 10%, stakes 1000 APT, `O1` joins the validator set.
2. Rewards accrue; `O1` calls `request_commission(O1, S, O1)`. This unlocks e.g. 10 APT of commission as `pending_inactive` and adds a distribution-pool entry for recipient `O1` with worth 10 APT via `add_distribution`/`request_commission_internal` (`staking_contract.move` lines 637-674).
3. Before the stake pool's lockup expires (so the 10 APT is still `pending_inactive`, not `inactive`), `S` calls `switch_operator_with_same_commission(S, O1, O2)` (or `switch_operator` with a different commission rate). `distribute_internal` flushes 0 (nothing inactive yet), and `O1`'s 10 APT distribution entry remains in the pool, now stored under the `StakingContract` keyed by `O2` (lines 745-805).
4. One or more epochs pass; the `pending_inactive` 10 APT (still tagged to `O1`) earns additional rewards, e.g. growing to 10.1 APT.
5. Anyone calls `distribute(S, O2)`. `distribute_internal` invokes `update_distribution_pool(pool, ..., operator=O2, commission_percentage)`. Since the shareholder is `O1` and `O1 != O2`, the 0.1 APT of growth is charged "commission" and those shares are transferred from `O1` to `O2` (lines 1001-1039), and then paid out — `O2` receives value from `O1`'s already-earned, locked-in commission, which `O1` never authorized to share with `O2`.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L651-657)
```text
        // Add a distribution for the operator.
        add_distribution(
            operator,
            staking_contract,
            operator,
            commission_amount
        );
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L783-805)
```text
        let (_, staking_contract) = staking_contracts.remove(&old_operator);
        // Force distribution of any already inactive stake.
        distribute_internal(
            staker_address,
            old_operator,
            &mut staking_contract,
        );

        // For simplicity, we request commission to be paid out first. This avoids having to ensure to staker doesn't
        // withdraw into the commission portion.
        request_commission_internal(
            old_operator,
            &mut staking_contract,
        );

        // Update the staking contract's commission rate and stake pool's operator.
        stake::set_operator_with_cap(&staking_contract.owner_cap, new_operator);
        staking_contract.commission_percentage = new_commission_percentage;

        let pool_address = staking_contract.pool_address;
        staking_contracts.add(new_operator, staking_contract);
        emit(SwitchOperator { pool_address, old_operator, new_operator });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L880-886)
```text
        let distribution_pool = &mut staking_contract.distribution_pool;
        update_distribution_pool(
            distribution_pool,
            distribution_amount,
            operator,
            staking_contract.commission_percentage
        );
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1010-1034)
```text
        // Charge all stakeholders (except for the operator themselves) commission on any rewards earnt relatively to the
        // previous value of the distribution pool.
        let shareholders = &distribution_pool.shareholders();
        shareholders.for_each_ref(
            |shareholder| {
                let shareholder: address = *shareholder;
                if (shareholder != operator) {
                    let shares = pool_u64::shares(distribution_pool, shareholder);
                    let previous_worth = pool_u64::balance(distribution_pool, shareholder);
                    let current_worth =
                        pool_u64::shares_to_amount_with_total_coins(
                            distribution_pool, shares, updated_total_coins
                        );
                    let unpaid_commission =
                        (current_worth - previous_worth) * commission_percentage / 100;
                    // Transfer shares from current shareholder to the operator as payment.
                    // The value of the shares should use the updated pool's total value.
                    let shares_to_transfer =
                        pool_u64::amount_to_shares_with_total_coins(
                            distribution_pool, unpaid_commission, updated_total_coins
                        );
                    pool_u64::transfer_shares(
                        distribution_pool, shareholder, operator, shares_to_transfer
                    );
                };
```
