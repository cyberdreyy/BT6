## Title
Stale distribution-pool shares recorded under a former operator bypass beneficiary redirection after `switch_operator` - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

## Summary
`distribute_internal` redirects a commission payout to the operator's beneficiary only when the shareholder key stored in `distribution_pool` equals the *current* `operator` argument passed in. Because `switch_operator`/`switch_operator_with_same_commission` re-key the `StakingContract` entry in the `Store.staking_contracts` map from `old_operator` to `new_operator` while leaving any not-yet-inactive commission shares in `distribution_pool` recorded under the address `old_operator`, a subsequent `distribute()` call (made with the new operator as the lookup key) will compare `recipient == operator` where `recipient == old_operator` and `operator == new_operator`. The equality fails, so the code falls through to paying `old_operator`'s address directly instead of calling `beneficiary_for_operator(old_operator)`.

## Finding Description
`request_commission_internal` records commission shares in `distribution_pool` keyed by `operator` at the time the commission is requested: [1](#0-0) 

`switch_operator` calls `distribute_internal` and `request_commission_internal` for the *old* operator before changing the map key from `old_operator` to `new_operator`: [2](#0-1) 

Commission requested here becomes an `unlock_with_cap` request; it is not `inactive` yet, so `distribute_internal`'s earlier call cannot pay it out (it only pays already-`inactive`/`pending_inactive` stake). This leaves a `distribution_pool` share entry keyed on `old_operator` still pending inside the (now re-keyed) `StakingContract` under `new_operator`.

When the stake later becomes inactive and anyone calls `distribute(staker, new_operator)`, `distribute_internal` iterates every shareholder in `distribution_pool`, including the stale `old_operator` entry, and decides the payout recipient purely by comparing to the `operator` parameter passed into the function (which is now `new_operator`): [3](#0-2) 

Since `recipient` (`old_operator`) is not equal to `operator` (`new_operator`), the `beneficiary_for_operator(operator)` redirection branch is skipped and the payout is sent straight to `old_operator`'s address instead of `beneficiary_for_operator(old_operator)`.

This is the same bug class as the report: a snapshot/accounting entry created under one identity's state ("old operator, with beneficiary X set at the time") is later resolved using the *current* identity/state (the new operator key) rather than the state that was actually in effect when the entry was created, so the wrong address is paid.

## Impact Explanation
If `old_operator` had configured a beneficiary via `set_beneficiary_for_operator` [4](#0-3)  before being switched out, the commission legitimately owed to that beneficiary is instead paid directly to the (former) operator's own address once the operator is switched and the stale distribution finally becomes withdrawable. This corrupts the intended beneficiary payout routing: value is credited to the wrong account (the ex-operator instead of its designated beneficiary), a "Operator commission, beneficiary payout ... corruption that credits the wrong account" impact directly in the required-impacts list.

## Likelihood Explanation
Requires only unprivileged, expected usage: a staker calling `switch_operator`/`switch_operator_with_same_commission` while the old operator has unclaimed, unlocking commission and has previously set a beneficiary — both of which are normal, permissionless operations exposed as public entry functions [5](#0-4) . `distribute` itself is callable by anyone [6](#0-5) , so no coordination from the beneficiary or old operator is needed to trigger the misrouted payout once the unlock period elapses.

## Recommendation
Track, per distribution-pool entry, which operator (and its beneficiary at commission-request time) each shareholder record belongs to — e.g., record the beneficiary address itself as the distribution recipient at the time `request_commission_internal` runs, instead of the operator's address, or resolve `beneficiary_for_operator` for every payee that is a known/former operator address rather than only for the one matching the currently passed-in `operator` parameter.

## Proof of Concept
1. Staker creates a staking contract with `operator1`, `commission_percentage > 0`.
2. `operator1` calls `set_beneficiary_for_operator(beneficiary1)`.
3. Rewards accrue; staker or operator calls actions that leave uncollected commission such that `request_commission_internal` records a `distribution_pool` share under `operator1` and issues `stake::unlock_with_cap` (not yet inactive).
4. Staker calls `switch_operator(staker, operator1, operator2, commission_percentage)` — this re-keys the `StakingContract` to `operator2` in `Store.staking_contracts`, but the pending unlocking commission share stays recorded under `operator1` inside `distribution_pool`.
5. Time passes until the lockup expires and the previously-unlocking commission becomes `inactive`.
6. Anyone calls `distribute(staker_address, operator2)`. `distribute_internal` iterates `distribution_pool` shareholders, finds the `operator1` entry, checks `recipient == operator` (`operator1 == operator2` → false), and pays the commission coins directly to `operator1`'s address instead of `beneficiary1`.

Note: I could not run the Move test suite in this environment to empirically confirm exact numeric outcomes; the trace above is based on static reading of `distribute_internal`, `switch_operator`, and `request_commission_internal` in this repository's `staking_contract.move`.

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L746-759)
```text
    public entry fun switch_operator_with_same_commission(
        staker: &signer, old_operator: address, new_operator: address
    ) acquires Store, BeneficiaryForOperator {
        let staker_address = signer::address_of(staker);
        assert_staking_contract_exists(staker_address, old_operator);

        let commission_percentage = commission_percentage(staker_address, old_operator);
        switch_operator(
            staker,
            old_operator,
            new_operator,
            commission_percentage
        );
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L840-853)
```text
    /// Allow anyone to distribute already unlocked funds. This does not affect reward compounding and therefore does
    /// not need to be restricted to just the staker or operator.
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
