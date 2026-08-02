## Analysis Result

Tracing the stake-lockup analog through `staking_contract.move`, I found a concrete, code-provable beneficiary-redirection bug in the operator-switch flow, independent of the original HyperCore report but matching its "value gets stuck / delivered to the wrong place" pattern.

### Title
Beneficiary redirection is bypassed for commission distributions pending at the time of `switch_operator` - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
When a staker calls `switch_operator`/`switch_operator_with_same_commission`, the outgoing operator's outstanding commission is queued via `request_commission_internal`, which records the distribution recipient as the **old operator's address** at that moment [1](#0-0) . This entry is not paid out immediately — it stays in the `distribution_pool` until a later `distribute_internal` call. However, `distribute_internal`'s beneficiary-redirect check compares the *stored recipient* against the *currently supplied `operator` parameter*, which by then is the **new** operator (because the staking contract has been moved to the new operator's key in `Store`) [2](#0-1) . Since `old_operator != new_operator`, the `if (recipient == operator)` beneficiary-redirect branch never triggers, and the commission is deposited directly to the old operator's account instead of the beneficiary address that operator had configured via `set_beneficiary_for_operator` [3](#0-2) .

### Finding Description
- `switch_operator` executes, in order: `distribute_internal` (flushes any already-pending distributions correctly, since `operator` still equals `old_operator` at that call) [4](#0-3) , then `request_commission_internal(old_operator, &mut staking_contract)` which adds a *new*, unflushed distribution entry keyed to `old_operator` [5](#0-4) , and finally moves the `StakingContract` from the `old_operator` key to the `new_operator` key in the staker's `Store` [6](#0-5) .
- The next time anyone calls `distribute(staker, new_operator)` (a permissionless, unprivileged entry function — "Allow anyone to distribute already unlocked funds") [7](#0-6) , `distribute_internal` is invoked with `operator = new_operator`. Inside the payout loop, the stale distribution-pool entry's `recipient` is still `old_operator`, so `recipient == operator` (`old_operator == new_operator`) is false, and the beneficiary substitution line `recipient = beneficiary_for_operator(operator)` is skipped [2](#0-1) .
- Result: funds intended (per the old operator's own `BeneficiaryForOperator` configuration) to land at the beneficiary address instead land directly at the old operator's own account address.

### Impact Explanation
This is an accounting/routing corruption in the operator-commission payout path: it silently overrides an operator's own `set_beneficiary_for_operator` configuration for exactly the commission tranche that was in-flight at the moment of an operator switch. If the beneficiary is a separate contract or account responsible for further splitting/distributing commission (e.g., a revenue-sharing multisig or a downstream payout contract expected by staking-service customers), those downstream parties are silently deprived of funds that are instead retained by the outgoing operator. This matches the "beneficiary payout corruption that credits the wrong account" impact category.

### Likelihood Explanation
High: `switch_operator` / `switch_operator_with_same_commission` and `distribute` are both plain, permissionless entry functions used in normal operation — no admin/governance privilege is required, and the sequence (set beneficiary → accrue rewards → staker switches operator → anyone calls `distribute`) is a completely ordinary usage pattern, not an edge case requiring adversarial setup.

### Recommendation
Capture and pin the beneficiary-eligible recipient at `add_distribution` time (i.e., resolve `beneficiary_for_operator(operator)` when the commission distribution entry is created, not later based on the then-current `operator` parameter passed into `distribute_internal`), or store the original operator address per distribution entry so the redirect check in `distribute_internal` compares against the operator that actually earned the commission rather than the caller-supplied "current operator" of the staking contract.

### Proof of Concept
1. Staker creates a staking contract with `operator1`, `commission_percentage = 10`.
2. `operator1` calls `set_beneficiary_for_operator(operator1, beneficiary)` [8](#0-7) .
3. Epoch passes, rewards accrue.
4. Staker calls `switch_operator(staker, operator1, operator2, new_commission)`. Internally this calls `request_commission_internal(operator1, ...)`, adding a distribution entry `{recipient: operator1, amount: commission}` to the pool, then re-keys the `StakingContract` under `operator2` [9](#0-8) .
5. After lockup expiry, anyone calls `distribute(staker, operator2)`. `distribute_internal(staker, operator2, staking_contract)` runs; the stored `recipient` is `operator1`, but `operator` param is `operator2`, so `recipient == operator` is false and the payout goes directly to `operator1`'s account instead of `beneficiary` [2](#0-1) .
6. Compare with the existing test `test_staker_can_switch_operator_with_beneficiary`, which only avoids this by calling `distribute()` to flush *before* calling `switch_operator` [10](#0-9)  — demonstrating that the ordering matters and is not defended against when `switch_operator` and `distribute` are combined without an intervening explicit `distribute` call, which is not enforced anywhere in the code.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L637-657)
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L807-838)
```text
    /// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
    /// beneficiary. To ensures payment to the current beneficiary, one should first call `distribute` before switching
    /// the beneficiary. An operator can set one beneficiary for staking contract pools, not a separate one for each pool.
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-902)
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1774-1801)
```text
        // Both original stake and operator commissions have received rewards.
        expected_commission_1 = with_rewards(expected_commission_1);
        new_balance = with_rewards(new_balance);
        stake::assert_stake_pool(
            pool_address,
            new_balance,
            expected_commission_1,
            0,
            0
        );
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
```
