# Analysis: Stale Operator Exclusion in `update_distribution_pool` After `switch_operator`

## Verification note
The file `staking_contract.move` is 2130 lines and was truncated during retrieval; I was able to view `request_commission_internal`, `add_distribution`, `distribute_internal`, and the tail of the `switch_operator`-style function (commission-changing switch, lines ~781-805), but **not** the body of `update_distribution_pool` itself (only its call sites and signature). My conclusion below rests on inference from the call sites and comments, since the exact internals of `update_distribution_pool` were not directly visible in this session. If a Devin session is started, the full body of `update_distribution_pool` should be re-verified before treating this as confirmed.

## What is confirmed from the visible code

1. `request_commission_internal` computes `commission_amount` from the **current** `staking_contract.commission_percentage`, then calls `add_distribution(operator, staking_contract, operator, commission_amount)`, which buys the operator into `distribution_pool` as shares at the *current* price (via `update_distribution_pool(...)` called with the pre-switch `operator` and pre-switch `commission_percentage`). [1](#0-0) [2](#0-1) 

2. In the commission-switching operator flow, the order is: `distribute_internal` → `request_commission_internal(old_operator, ...)` (both still using the old rate and old operator identity) → **only then** `staking_contract.commission_percentage = new_commission_percentage;` and `staking_contracts.add(new_operator, staking_contract)`. [3](#0-2) 

3. `distribute_internal` (invoked later by anyone via the public `distribute` entry function) re-invokes `update_distribution_pool(distribution_pool, distribution_amount, operator, staking_contract.commission_percentage)` — where `operator` is now whatever key the `StakingContract` is currently filed under, i.e. **`new_operator`**, and `commission_percentage` is the **new** rate. [4](#0-3) [5](#0-4) 

4. `distribute_internal` only withdraws whatever is *currently* withdrawable from the stake pool (`inactive + pending_inactive`, but `stake::withdraw_with_cap` only actually extracts truly-inactive coins). If the lockup has not yet expired at the time of `switch_operator`, the just-requested commission for `old_operator` remains as **unredeemed shares** in `distribution_pool` past the switch. [6](#0-5) 

## The mechanism (best-supported hypothesis)

`update_distribution_pool` is called with an `operator` argument that is used to identify which shareholder is exempt from having `commission_percentage` deducted from their pool-share growth (the operator's own commission entry should not itself be taxed again). Because the `StakingContract` struct is mutated in place and re-keyed from `old_operator` to `new_operator`, any subsequent call to `update_distribution_pool` after the switch passes `new_operator` as the exempt address — not `old_operator`. If `old_operator`'s already-purchased-but-undistributed commission shares are still sitting in `distribution_pool` when the pool's `total_coins` grows further (which happens automatically each epoch while the corresponding stake is `pending_inactive`), those stale shares are no longer recognized as belonging to "the operator" and would be treated like an ordinary shareholder's balance — subject to having `commission_percentage` skimmed off their apparent growth, using the **new** rate, with the skimmed amount credited to `new_operator`.

This does not match the user's exact framing (shares "redeemed at the new rate"), but it does support the same broken invariant: **already-requested commission value belonging to `old_operator`, computed and locked in under the old rate, can be diminished by activity that occurs under `new_operator`'s tenure and at the new rate**, redirecting value from the old operator/beneficiary to the new operator.

## Impact
- Value transferred from an operator's already-earned (but not yet withdrawn) commission to a subsequently-installed operator, without either party's consent, purely as a side effect of a staker action.
- Both inflation and deflation directions are plausible depending on rate delta (higher new rate → more of old operator's growth siphoned to new operator).

## Confidence and scope caveat
I could not directly view `update_distribution_pool`'s body in this session due to file truncation, so I cannot cite its exact exclusion logic — only infer it from parameter usage at call sites and by the fact that `operator` is explicitly threaded through as an argument distinct from `commission_percentage` in every caller. **This should be treated as a lead requiring confirmation against the actual `update_distribution_pool` source**, not a fully proven finding.

### Title
Stale operator identity in `update_distribution_pool` after `switch_operator` may misattribute old operator's locked-in commission — ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Summary
`switch_operator`-style flows mutate `commission_percentage` and re-key the `StakingContract` to `new_operator` immediately after calling `request_commission_internal` for `old_operator`. If `old_operator`'s just-requested commission shares are not yet fully withdrawable (lockup not expired), they remain in `distribution_pool` past the switch. Subsequent `update_distribution_pool` calls use `new_operator` as the "operator" exemption identity and the new commission rate, which can cause old operator's already-committed commission shares to be treated as ordinary growth subject to being taxed and redirected to the new operator.

### Impact Explanation
Misappropriation of already-earned operator commission value between old and new operator, corrupting the commission accounting invariant that committed amounts should be distributed at the rate/identity in effect when requested.

### Likelihood Explanation
Requires: (a) `commission_percentage` change or operator switch while unredeemed commission shares exist in the pool, and (b) reward accrual/further pool growth before those shares are actually distributed (normal epoch cadence makes this easy to hit if lockup hasn't expired). No special privilege is needed beyond the staker's own legitimate `switch_operator`/`update_commision` call, but the harmed party (old operator) is a third party.

### Recommendation
Verify and, if confirmed, fix `update_distribution_pool`/`add_distribution`/`distribute_internal` so that shareholder exemption from commission-on-growth is tracked per-shareholder at buy-in time (e.g., tag shares as "commission" vs "principal" shares) rather than by comparing against the *current* `operator` field of the `StakingContract`, which changes across `switch_operator` calls.

### Proof of Concept
Cannot be finalized without confirming `update_distribution_pool`'s exact body (not available in this truncated read). Suggested test outline for a Devin session: create a staking contract, accrue rewards, call `request_commission` for `old_operator`, call `switch_operator`/`update_commision` with a materially different rate before lockup expiry, force additional reward accrual (advance epoch), then call `distribute` and assert `old_operator`'s payout equals the commission amount computed at request time rather than a value adjusted by growth taxed at the new rate/new operator identity.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L637-674)
```text
    fun request_commission_internal(
        operator: address,
        staking_contract: &mut StakingContract,
    ): u64 {
        // Unlock just the commission portion from the stake pool.
        let (total_active_stake, accumulated_rewards, commission_amount) =
            get_staking_contract_amounts_internal(staking_contract);
        staking_contract.principal = total_active_stake - commission_amount;

        // Short-circuit if there's no commission to pay.
        if (commission_amount == 0) {
            return 0
        };

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

        let pool_address = staking_contract.pool_address;
        emit(
            RequestCommission {
                operator,
                pool_address,
                accumulated_rewards,
                commission_amount
            }
        );

        commission_amount
    }
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L842-853)
```text
    public entry fun distribute(
        staker: address, operator: address
    ) acquires Store, BeneficiaryForOperator {
        assert_staking_contract_exists(staker, operator);
        let store = borrow_global_mut<Store>(staker);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);
        distribute_internal(
            staker,
            operator,
            staking_contract,
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L861-887)
```text
        let pool_address = staking_contract.pool_address;
        // Create the Staker resource if it doesn't exist to backfill the Staker resource for each pool.
        if (!exists<Staker>(pool_address)) {
            let pool_signer =
                &account::create_signer_with_capability(&staking_contract.signer_cap);
            move_to(pool_signer, Staker { staker });
        };
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

        let distribution_pool = &mut staking_contract.distribution_pool;
        update_distribution_pool(
            distribution_pool,
            distribution_amount,
            operator,
            staking_contract.commission_percentage
        );

```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L938-957)
```text
    fun add_distribution(
        operator: address,
        staking_contract: &mut StakingContract,
        recipient: address,
        coins_amount: u64,
    ) {
        let distribution_pool = &mut staking_contract.distribution_pool;
        let (_, _, _, total_distribution_amount) =
            stake::get_stake(staking_contract.pool_address);
        update_distribution_pool(
            distribution_pool,
            total_distribution_amount,
            operator,
            staking_contract.commission_percentage
        );

        distribution_pool.buy_in(recipient, coins_amount);
        let pool_address = staking_contract.pool_address;
        emit(AddDistribution { operator, pool_address, amount: coins_amount });
    }
```
