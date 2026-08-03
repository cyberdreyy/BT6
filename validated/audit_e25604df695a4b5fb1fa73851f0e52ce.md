## Finding

The vulnerability is real, but it lives in `staking_contract.move` (not `types/src/vm/code.rs`, which appears to be a mislabeled path in the question).

### Title
Stale beneficiary resolution in `staking_contract::distribute_internal` lets in-flight commission bypass old operator's configured beneficiary after an operator switch - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`switch_operator`/`switch_operator_with_same_commission` unlock any accrued-but-not-yet-inactive commission for the old operator and record it as a pending distribution share keyed by the old operator's address, then immediately re-key the `StakingContract` under the new operator. Because `distribute_internal`'s beneficiary check compares the share's recipient address against the *currently passed-in* `operator` parameter (which, post-switch, is always the new operator), that pending share for the old operator never matches, and the beneficiary redirection is silently skipped when the funds finally settle.

### Finding Description
In `switch_operator` [1](#0-0) , before the operator key is swapped, the code calls `request_commission_internal(old_operator, &mut staking_contract)`. If there are unpaid rewards, this adds a distribution share under `old_operator`'s address via `add_distribution(operator, staking_contract, operator, commission_amount)` and calls `stake::unlock_with_cap`, which only moves the commission from `active` to `pending_inactive` — it is not yet withdrawable/inactive [2](#0-1) .

Immediately afterward, the `StakingContract` record is removed from the map under `old_operator` and re-inserted under `new_operator` [3](#0-2) . The pending share for `old_operator`, however, still lives inside `staking_contract.distribution_pool`.

Later, once the stake pool's lockup expires, this commission becomes inactive. `distribute` is a permissionless entry function ("Allow anyone to distribute already unlocked funds") that anyone can call with `(staker, new_operator)`, since that's the only valid key in the map now [4](#0-3) . Inside `distribute_internal`, the beneficiary substitution logic is:
```
if (recipient == operator) {
    recipient = beneficiary_for_operator(operator);
};
``` [5](#0-4) 

Here `recipient` is `old_operator` (from the stale share) while `operator` is the function parameter, which is now `new_operator`. The equality fails, so the code never substitutes `beneficiary_for_operator(old_operator)` — the coins are deposited directly to `old_operator`'s raw address, completely bypassing whatever beneficiary `old_operator` had configured via `set_beneficiary_for_operator` [6](#0-5) .

This differs from `delegation_pool.move`, which avoids this class of bug entirely by resolving `beneficiary_for_operator(stake::get_operator(pool_address))` and buying shares directly in the beneficiary's name at synchronization time, rather than deferring the beneficiary check to a later `recipient == operator` comparison [7](#0-6) . Only `staking_contract.move`'s distribution-pool design has this stale-key comparison flaw.

### Impact Explanation
Any account whose operator has configured a beneficiary (e.g., to route commission to a cold-storage/custody account rather than the hot operator key) will have in-flight commission redirected to the operator's own raw address instead, for exactly the amount that was unlocked-but-not-yet-inactive at the moment of an operator switch. This is triggerable by any unprivileged account via the permissionless `distribute` entry function, requiring no special role — matching the "beneficiary-update paths must not redirect value" pivot in scope. The existing test suite (`test_operator_can_set_beneficiary`) only exercises the case where all pending distributions are flushed via an explicit `distribute` call immediately before the switch, so it never triggers this stale-share window [8](#0-7) .

### Likelihood Explanation
This requires no attacker privilege beyond calling the public `distribute` function, and the vulnerable window (a `request_commission_internal` call inside `switch_operator` that unlocks new commission not yet flushed by a prior `distribute`) is a normal, likely occurrence any time a staker switches operators without first manually draining all pending commission down to zero. The affected value is bounded by the commission accrued between the last `distribute`/`request_commission` call and the `switch_operator` call.

### Recommendation
In `distribute_internal`, resolve the beneficiary based on the recipient's own registered `BeneficiaryForOperator` mapping rather than comparing `recipient == operator`, e.g. check `if (exists<BeneficiaryForOperator>(recipient)) { recipient = beneficiary_for_operator(recipient); }`, or record the beneficiary address (resolved at request-commission time) directly as the distribution recipient instead of the operator's raw address, mirroring `delegation_pool.move`'s approach.

### Proof of Concept
1. `staker` creates a staking contract with `operator_1`, commission 10%, and stake pool joins the validator set.
2. `operator_1` calls `set_beneficiary_for_operator(operator_1, beneficiary_1)`.
3. Epochs pass generating rewards, but `distribute`/`request_commission` are NOT called yet for the newly accrued rewards.
4. `staker` calls `switch_operator_with_same_commission(staker, operator_1, operator_2)`. Internally this calls `request_commission_internal(operator_1, ...)`, which unlocks the just-accrued commission and records a pending share for `operator_1` — but it is only `pending_inactive`, not yet distributable.
5. Fast-forward until the stake pool's lockup expires.
6. Any unprivileged third-party account calls `staking_contract::distribute(staker_address, operator_2)` (permissionless).
7. Assert: the commission that accrued to `operator_1` before the switch is deposited into `operator_1`'s own account balance, NOT `beneficiary_1`'s balance — violating the expectation that pre-switch commission should be paid to the OLD operator's beneficiary.

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-898)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1784-1823)
```text
        distribute(staker_address, operator1_address);
        let operator_balance = coin::balance<AptosCoin>(operator1_address);
        let beneficiary_balance = coin::balance<AptosCoin>(beneficiary_address);
        let expected_operator_balance = INITIAL_BALANCE;
        let expected_beneficiary_balance = expected_commission_1;
        assert!(operator_balance == expected_operator_balance, operator_balance);
        assert!(beneficiary_balance == expected_beneficiary_balance, beneficiary_balance);
        stake::assert_stake_pool(pool_address, new_balance, 0, 0, 0);
        assert_no_pending_distributions(staker_address, operator1_address);

        // switch operator to operator2. The rewards should go to operator2 not to the beneficiay of operator1.
        let old_beneficiay_balance = beneficiary_balance;
        switch_operator(
            staker,
            operator1_address,
            operator2_address,
            10
        );

        stake::end_epoch();
        let (_, accumulated_rewards, _) =
            staking_contract_amounts(staker_address, operator2_address);

        let expected_commission = accumulated_rewards / 10;

        // Request commission.
        request_commission(operator2, staker_address, operator2_address);
        // Unlocks the commission.
        stake::fast_forward_to_unlock(pool_address);
        expected_commission = with_rewards(expected_commission);

        // Distribute the commission to the operator.
        distribute(staker_address, operator2_address);

        // Assert that the rewards go to operator2, and the balance of the operator1's beneficiay remains the same.
        assert!(coin::balance<AptosCoin>(operator2_address) >= expected_commission, 1);
        assert!(
            coin::balance<AptosCoin>(beneficiary_address) == old_beneficiay_balance, 1
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1951-1974)
```text
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

        emit(DistributeCommission {
            pool_address,
            operator: stake::get_operator(pool_address),
            beneficiary: beneficiary_for_operator(stake::get_operator(pool_address)),
            commission_active,
            commission_pending_inactive,
        });
```
