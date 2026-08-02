## Finding

The user's specific "operator‑reuse via SimpleMap" framing isn't quite where the real bug lives, but tracing `update_distribution_pool`/`distribute_internal` through `switch_operator`, `request_commission`, and `unlock_stake` does reveal a genuine, unprivileged-reachable commission-misdirection bug — it just doesn't require re-adding the exact same operator address; it happens on any operator switch where the outgoing operator has unclaimed commission shares still sitting in the shared `Pool`.

### Title
Stale distribution-pool shares of a former operator are re-commissioned to the new operator on `switch_operator` - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`StakingContract.distribution_pool` is a single `pool_u64::Pool` keyed only by recipient *address*, shared across the lifetime of the underlying stake pool [1](#0-0) . `update_distribution_pool` exempts only the *address currently passed in as `operator`* from being charged commission on share-value growth: `if (shareholder != operator) { ... charge commission ... }` [2](#0-1) . When a staker calls `switch_operator`/`switch_operator_with_same_commission`, the same `StakingContract` (and its `distribution_pool`) is re-keyed from `old_operator` to `new_operator` in the `SimpleMap` [3](#0-2) . Before the switch, `distribute_internal` is called to flush the pool, but it only actually pays out and clears the pool when there is currently-withdrawable balance (`distribution_amount > 0`); if the stake pool's lockup hasn't expired yet, `distribution_amount` is `0` and the function returns immediately without touching the pool [4](#0-3) . `request_commission_internal`, called right after, adds a *fresh* share allocation for `old_operator`'s freshly-unlocked commission via `add_distribution(old_operator, ..., old_operator, commission_amount)` [5](#0-4) , which is correctly exempted from double-commissioning at that moment because `operator == old_operator` at that call site.

Once the map key is swapped to `new_operator`, the `old_operator` address remains a shareholder in the same pool holding its unpaid, unlocked-but-not-yet-withdrawn commission. Any subsequent trigger of `update_distribution_pool` under the new key (`distribute`, `distribute_internal` inside `unlock_stake`/`request_commission`, etc.) is called with `operator = new_operator`. In that pass, `old_operator`'s stale shares no longer match `shareholder == operator`, so they fall into the "charge commission on value growth" branch and a chunk of `old_operator`'s share value is transferred to `new_operator` [6](#0-5) . When those diminished shares are eventually redeemed and paid out, `old_operator` receives less than it earned, and the skimmed portion is paid to `new_operator` (or `new_operator`'s beneficiary, since the payout loop redirects `recipient == operator` to `beneficiary_for_operator(operator)` where `operator` is now `new_operator`) [7](#0-6) .

### Impact Explanation
This is a real accounting-integrity bug that matches the "Operator commission ... corruption that credits the wrong account" impact category: a former operator's already-earned, unlocked commission is partially redirected to whichever operator currently holds the position, purely as a side effect of the staker calling a normal, permitted function (`switch_operator`). It does not require any privilege escalation — the staker is acting within their rights on their own pool, but the loss falls on the third-party former operator, who has no way to prevent or detect it.

### Likelihood Explanation
This requires no attacker-owned pool assumption beyond the staker legitimately owning their own staking contract (which they always do for their own `staker` address). The triggering condition — a pending, not-yet-withdrawable commission distribution existing at the moment of an operator switch — is a common, ordinary operational occurrence, since Aptos lockup periods (typically 30 days) routinely exceed the time between a `request_commission` and a subsequent `switch_operator`.

### Recommendation
`update_distribution_pool`'s commission-exemption should not depend solely on comparing `shareholder == operator` using the *currently active* operator address. Either: (1) fully drain/settle the `distribution_pool` (forcing payout, not skipping on `distribution_amount == 0`) before ever re-keying a `StakingContract` in `switch_operator`, or (2) track which shares represent "already-realized operator commission" (e.g., a separate small pool/ledger per commission tranche) so they are never subject to a second, involuntary commission deduction regardless of which address is currently the `operator` parameter.

### Proof of Concept
1. Staker creates a staking contract with `operator = B`, some commission %, stake pool accrues rewards.
2. `B` (or staker) calls `request_commission` — this unlocks `B`'s commission into `pending_inactive`, recorded as shares owned by `B` in `distribution_pool` (`staking_contract.move:637-674`).
3. Before the lockup period expires, staker calls `switch_operator(staker, B, C, commission_pct)`. Inside, `distribute_internal` is a no-op because `distribution_amount == 0` (nothing withdrawable yet) — `B`'s shares remain untouched in the pool. `request_commission_internal` runs again (likely 0 new commission), and the contract is re-keyed to `C` (`staking_contract.move:783-804`).
4. Stake pool's lockup eventually expires (accruing more rewards in the meantime). Staker or `C` calls `unlock_stake`/`request_commission`, invoking `distribute_internal(staker, C, staking_contract)` → `update_distribution_pool(pool, ..., operator=C, commission_pct)`.
5. In `update_distribution_pool`, shareholder `B` (`B != C`) has commission charged on the growth of `B`'s share value and those shares are transferred to `C` (`staking_contract.move:1016-1034`).
6. When `B`'s remaining shares are redeemed and paid out, `B` receives less than the commission it originally earned; the skimmed amount is paid to `C` (or `C`'s beneficiary). Asserting the amount `B` receives equals its originally recorded `commission_amount` from step 2 will fail.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L73-85)
```text
    struct StakingContract has store {
        // Recorded principal after the last commission distribution.
        // This is only used to calculate the commission the operator should be receiving.
        principal: u64,
        pool_address: address,
        // The stake pool's owner capability. This can be used to control funds in the stake pool.
        owner_cap: OwnerCapability,
        commission_percentage: u64,
        // Current distributions, including operator commission withdrawals and staker's partial withdrawals.
        distribution_pool: Pool,
        // Just in case we need the SignerCap for stake pool account in the future.
        signer_cap: SignerCapability
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L651-661)
```text
        // Add a distribution for the operator.
        add_distribution(
            operator,
            staking_contract,
            operator,
            commission_amount
        );

        // Request to unlock the commission from the stake pool.
        // This won't become fully unlocked until the stake pool's lockup expires.
        stake::unlock_with_cap(commission_amount, &staking_contract.owner_cap);
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L783-804)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L868-879)
```text
        let (_, inactive, _, pending_inactive) = stake::get_stake(pool_address);
        let total_potential_withdrawable = inactive + pending_inactive;
        let coins =
            stake::withdraw_with_cap(
                &staking_contract.owner_cap, total_potential_withdrawable
            );
        let distribution_amount = coin::value(&coins);
        if (distribution_amount == 0) {
            coin::destroy_zero(coins);
            return
        };

```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-911)
```text
        // Buy all recipients out of the distribution pool.
        while (distribution_pool.shareholders_count() > 0) {
            let recipients = distribution_pool.shareholders();
            let recipient = recipients[0];
            let current_shares = distribution_pool.shares(recipient);
            let amount_to_distribute =
                distribution_pool.redeem_shares(recipient, current_shares);
            // If the recipient is the operator, send the commission to the beneficiary instead.
            if (recipient == operator) {
                recipient = beneficiary_for_operator(operator);
            };
            aptos_account::deposit_coins(
                recipient, coin::extract(&mut coins, amount_to_distribute)
            );

            emit(
                Distribute {
                    operator,
                    pool_address,
                    recipient,
                    amount: amount_to_distribute
                }
            );
        };
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1010-1036)
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
            }
        );
```
