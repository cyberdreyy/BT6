Confirmed: `add_distribution` is called with `recipient = operator` (the old operator's own address, line 655), not the beneficiary. The beneficiary redirection only happens later, inside `distribute_internal`, via the `if (recipient == operator) { recipient = beneficiary_for_operator(operator); }` check, using whatever `operator` value is passed into that specific `distribute_internal` call.

### Title
Stale operator-commission distribution bypasses beneficiary redirection after `switch_operator` - ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Summary
`request_commission_internal` records unpaid commission in the `distribution_pool` keyed by the operator's own address (not the beneficiary) [1](#0-0) . Redirection to the operator's beneficiary happens later, in `distribute_internal`, only if `recipient == operator` for the `operator` value passed into that specific call [2](#0-1) . When `switch_operator` moves a `StakingContract` from `old_operator` to `new_operator` in the map (keeping the still-pending distribution entry recorded under `old_operator`'s address) [3](#0-2) , any later call to `distribute()`/`distribute_internal` for that contract passes `operator = new_operator`, so the stale entry's `recipient == operator` check fails, and the commission is paid directly to `old_operator`'s address rather than to `old_operator`'s beneficiary set via `set_beneficiary_for_operator`.

### Finding Description
`staking_contract::distribute_internal` is the sole place where operator commission is redirected to a configured beneficiary:
```
if (recipient == operator) {
    recipient = beneficiary_for_operator(operator);
};
``` [4](#0-3) 

This comparison is against the `operator` parameter passed to that call — which is the *current* key in the staker's `staking_contracts` map (i.e., whichever operator address the caller of `distribute`/`distribute_internal` supplies), not necessarily the operator address that the distribution entry was originally created for.

`request_commission_internal` always records the pending commission recipient as the operator's raw address at the time of the call:
```
add_distribution(operator, staking_contract, operator, commission_amount);
``` [1](#0-0) 

In `switch_operator`, the staker (unprivileged, calling their own function) can:
1. `distribute_internal` for `old_operator` (flushes any already-inactive stake),
2. `request_commission_internal(old_operator, ...)` — this unlocks new commission and records a distribution keyed to `old_operator`'s own address in the pool (this entry has NOT yet been distributed since the newly unlocked stake is `pending_inactive`, not yet `inactive`),
3. Reassign the `StakingContract` (with its `distribution_pool` unchanged) from key `old_operator` to key `new_operator` in the map [5](#0-4) .

Once the lockup period elapses and the staker (or anyone, since `distribute` is public/permissionless) later calls `distribute(staker, new_operator)`, `distribute_internal` is invoked with `operator = new_operator`. The stale pending-distribution entry in the pool still has `recipient = old_operator`'s address. Since `old_operator != new_operator`, the `recipient == operator` check fails, so the funds are deposited directly to `old_operator`'s account address — completely bypassing `beneficiary_for_operator(old_operator)`, even though `old_operator` had explicitly configured a beneficiary via `set_beneficiary_for_operator` [6](#0-5) .

### Impact Explanation
This breaks the operator/beneficiary role boundary: commission that was contractually supposed to be routed to the beneficiary account is instead paid to the operator's own address. This matches the "Operator commission, beneficiary payout... corruption that credits the wrong account" impact category. The beneficiary permanently loses claim to that specific commission tranche (no retry mechanism re-routes already-distributed funds), and the operator receives funds it was not entitled to receive directly — a wrong-recipient/value-redirection bug reachable by the unprivileged staker/operator without needing elevated privileges.

### Likelihood Explanation
The sequence requires only standard, permissionless operations available to any staker/operator pair: `set_beneficiary_for_operator`, `switch_operator` (staker-only, but staker is the legitimate/unprivileged owner of their own contract, not requiring any special role over the destination), and waiting for a lockup cycle to end before calling `distribute`. All of `switch_operator`, `request_commission_internal`, and `distribute` are entry functions callable by ordinary accounts. The bug is triggered by the ordering of unlocking commission right before switching operator, which is a plausible and even encouraged flow ("Allows staker to switch operator without going through the lengthy process to unstake").

### Recommendation
Store the intended final recipient address (resolved beneficiary, or a stable identifier not affected by later `switch_operator` calls) directly in the distribution pool entry at the time `add_distribution` is called in `request_commission_internal`, rather than deferring beneficiary resolution to `distribute_internal` based on the currently-keyed `operator`. Alternatively, force a full `distribute_internal`+`request_commission_internal` flush and lockup-aware settlement before `switch_operator` is allowed to proceed, ensuring no unresolved recipient-address-dependent entries survive the operator key change.

### Proof of Concept
1. `staker` creates a staking contract with `operator1`, commission_percentage = 10%. `operator1` calls `set_beneficiary_for_operator(operator1, beneficiary)`.
2. Stake pool earns rewards.
3. `staker` calls `staking_contract::switch_operator(staker, operator1, operator2, new_commission)`. Internally this calls `distribute_internal` (pays out anything already inactive) then `request_commission_internal(operator1, ...)`, which unlocks new commission and adds `add_distribution(operator1, staking_contract, operator1, commission_amount)` — recipient recorded as `operator1`'s address. The `StakingContract` is then moved to key `operator2` in the map.
4. Time passes until the stake pool's lockup expires (commission becomes `inactive`).
5. Anyone calls `staking_contract::distribute(staker_address, operator2)`. `distribute_internal` runs with `operator = operator2`. The pool's pending shareholder is `operator1`'s address; since `operator1 != operator2`, the `if (recipient == operator)` beneficiary-redirection branch is skipped, and `commission_amount` is deposited straight to `operator1`'s address instead of `beneficiary`.

Result: the beneficiary configured by `operator1` never receives the commission that was pending at the moment of the operator switch; `operator1` receives it directly, bypassing the beneficiary redirection invariant.

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L810-838)
```text
    public entry fun set_beneficiary_for_operator(
        operator: &signer, new_beneficiary: address
    ) acquires BeneficiaryForOperator {
        assert!(
            features::operator_beneficiary_change_enabled(),
            std::error::invalid_state(EOPERATOR_BENEFICIARY_CHANGE_NOT_SUPPORTED)
        );
        // The beneficiay address of an operator is stored under the operator's address.
        // So, the operator does not need to be validated with respect to a staking pool.
        let operator_addr = signer::address_of(operator);
        let old_beneficiary = beneficiary_for_operator(operator_addr);
        if (exists<BeneficiaryForOperator>(operator_addr)) {
            borrow_global_mut<BeneficiaryForOperator>(operator_addr).beneficiary_for_operator =
                new_beneficiary;
        } else {
            move_to(
                operator,
                BeneficiaryForOperator { beneficiary_for_operator: new_beneficiary }
            );
        };

        emit(
            SetBeneficiaryForOperator {
                operator: operator_addr,
                old_beneficiary,
                new_beneficiary
            }
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-901)
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
```
