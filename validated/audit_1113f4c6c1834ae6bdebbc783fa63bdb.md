## Title
Beneficiary bypass on operator switch: pending commission for the outgoing operator is paid directly to the old operator's address instead of their registered beneficiary once `distribute` is later called under the new operator's key - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

## Summary
The premise in the submitted question (that `staking_contract::distribute_internal` can run "unsynchronized" mid-transaction between operator swap and commission accounting) is not exploitable: `staking_contract::switch_operator` performs `distribute_internal` and `request_commission_internal` for the old operator synchronously, before flipping the operator key, all within a single atomic Move transaction, so there is no interleaving window for an external `distribute` call. Accrued rewards are correctly split by resetting `staking_contract.principal` before `stake::set_operator_with_cap` runs.

However, tracing the same code path surfaces a real, distinct, reproducible beneficiary-routing bug: `switch_operator`'s own `request_commission_internal` call (which happens after `distribute_internal` but before the operator key is switched) leaves a *new*, unflushed commission share in the `distribution_pool`, tagged to the address of the **old** operator. When that share later becomes withdrawable and an unprivileged caller invokes `staking_contract::distribute` (or `vesting::distribute`, which is documented and designed to be permissionless), `distribute_internal` is invoked with `operator` set to the *current* (new) operator. Its beneficiary-redirect check compares the stored share's recipient (`old_operator`) to that current `operator` (`new_operator`), which never matches, so `beneficiary_for_operator(old_operator)` is never consulted and the commission is paid straight to the old operator's own account instead of the beneficiary address the old operator had configured via `set_beneficiary_for_operator`.

## Finding Description
`switch_operator` sequence: [1](#0-0) 

1. `distribute_internal(staker_address, old_operator, &mut staking_contract)` flushes any already-inactive funds. Because `operator == old_operator` here, any pending shares tagged `old_operator` are correctly redirected to `beneficiary_for_operator(old_operator)`.
2. `request_commission_internal(old_operator, &mut staking_contract)` is called *after* that flush. It computes the commission owed up to now and calls `add_distribution(operator=old_operator, ..., recipient=old_operator, commission_amount)`, buying new shares under the `old_operator` key in the pool, and unlocks that amount via `stake::unlock_with_cap`. [2](#0-1) 
3. Only after this does `stake::set_operator_with_cap(&staking_contract.owner_cap, new_operator)` run and the `StakingContract` gets re-indexed under `new_operator` in the `Store.staking_contracts` map. [3](#0-2) 

The newly bought share (step 2) is never flushed before the key swap - it can only become withdrawable once the stake pool's lockup cycle elapses, which happens after the operator switch transaction has completed.

The bug is in the beneficiary-redirect check inside `distribute_internal`, which uses the *current* `operator` parameter (looked up via the caller-supplied key, which after a switch is `new_operator`) rather than the share's actual recipient identity: [4](#0-3) 

```move
while (distribution_pool.shareholders_count() > 0) {
    let recipients = distribution_pool.shareholders();
    let recipient = recipients[0];
    ...
    // If the recipient is the operator, send the commission to the beneficiary instead.
    if (recipient == operator) {
        recipient = beneficiary_for_operator(operator);
    };
    aptos_account::deposit_coins(recipient, coin::extract(&mut coins, amount_to_distribute));
```

Because `recipient` (the stored shareholder address, `old_operator`) is compared against the function's `operator` parameter (which reflects the *current* operator, `new_operator`), the comparison always fails for the old operator's leftover share once the operator has changed. The payout therefore lands directly at `old_operator`'s own account address, silently skipping the `beneficiary_for_operator` indirection the old operator had configured.

`distribute` is explicitly documented as permissionless, so any unrelated unprivileged account can trigger this once the funds unlock: [5](#0-4) 

The same defect is reachable via the vesting flow, since `vesting::update_operator` calls `staking_contract::switch_operator` directly and `vesting::distribute` calls the underlying `staking_contract::distribute`: [6](#0-5) 

The existing regression tests do not cover this scenario because they always call `distribute` to fully flush all pending shares *before* invoking `switch_operator`, so the leftover request-commission share created inside `switch_operator` itself is never exercised against a later `distribute` call under the new operator's key: [7](#0-6) [8](#0-7) 

## Impact Explanation
Operator commission that the old operator explicitly routed to a beneficiary address (e.g., a cold wallet, multisig, or a separate revenue-collection account) instead gets deposited to the old operator's own (potentially hot, less-trusted) key once the operator is switched and the pending commission unlocks. This corrupts the intended payout routing for that residual commission slice and can happen purely from a routine, permissionless `distribute()` call by anyone - matching the reviewed concern about commission/beneficiary payouts being misrouted to the wrong account across an operator switch.

## Likelihood Explanation
This is not a narrow race condition; it is deterministic. It triggers whenever: (1) the old operator has a `beneficiary_for_operator` configured, (2) the old operator has any unpaid/accrued commission at the moment `switch_operator` (or `vesting::update_operator`) is called, and (3) anyone later calls `distribute` (or `vesting::distribute`) once that residual commission becomes inactive/withdrawable. All of these are common, expected operational conditions (beneficiaries are a documented feature specifically for validators, and operator switches happen routinely for both direct `staking_contract` and `vesting` pools).

## Recommendation
Fix the beneficiary-redirect check in `distribute_internal` to compare the share's `recipient` against the operator identity recorded at the time the share was created (or simply always attempt `beneficiary_for_operator(recipient)` when `recipient` corresponds to any operator that ever held this staking contract, e.g., by tracking a `BeneficiaryForOperator`-eligible recipient set independent of the *current* `operator` parameter). Alternatively, have `switch_operator` immediately request+finalize (or force a final flush after the lockup, or store the recipient's beneficiary snapshot at share-creation time) so no residual commission crosses an operator boundary un-redirected.

## Proof of Concept
1. Staker creates a `staking_contract` (or `vesting` contract) with `operator1`, 10% commission.
2. `operator1` calls `set_beneficiary_for_operator(operator1, beneficiary1)`.
3. Stake pool earns rewards (`stake::end_epoch()`), accruing unpaid commission for `operator1`.
4. Staker calls `staking_contract::switch_operator(staker, operator1, operator2, commission)` (or `vesting::update_operator`). Internally this calls `request_commission_internal(operator1, ...)`, buying a new share tagged `operator1` in the distribution pool, then switches the map key to `operator2`.
5. Advance past the lockup so the newly unlocked commission becomes `inactive` (`stake::fast_forward_to_unlock`).
6. Any unrelated unprivileged account calls `staking_contract::distribute(staker, operator2)` (or `vesting::distribute(contract_address)`).
7. Assert: the commission share tagged `operator1` is deposited to `operator1`'s own account, not to `beneficiary1`, i.e. `coin::balance<AptosCoin>(beneficiary1)` does not increase while `coin::balance<AptosCoin>(operator1)` does - demonstrating the beneficiary bypass for the residual pre-switch commission.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L637-661)
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1784-1801)
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
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L823-847)
```text
    public entry fun update_operator(
        admin: &signer,
        contract_address: address,
        new_operator: address,
        commission_percentage: u64,
    ) acquires VestingContract {
        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        verify_admin(admin, vesting_contract);
        let contract_signer = &get_vesting_account_signer_internal(vesting_contract);
        let old_operator = vesting_contract.staking.operator;
        staking_contract::switch_operator(contract_signer, old_operator, new_operator, commission_percentage);
        vesting_contract.staking.operator = new_operator;
        vesting_contract.staking.commission_percentage = commission_percentage;

        emit(
            UpdateOperator {
                admin: vesting_contract.admin,
                vesting_contract_address: contract_address,
                staking_pool_address: vesting_contract.staking.pool_address,
                old_operator,
                new_operator,
                commission_percentage,
            },
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L1684-1693)
```text
        // Distribute the commission to the operator.
        distribute(contract_address);

        // Assert that the beneficiary receives the expected commission.
        assert!(coin::balance<AptosCoin>(operator_address1) == 0, 1);
        assert!(coin::balance<AptosCoin>(beneficiary_address) == expected_commission, 1);
        let old_beneficiay_balance = coin::balance<AptosCoin>(beneficiary_address);

        // switch operator to operator2. The rewards should go to operator2 not to the beneficiay of operator1.
        update_operator(admin, contract_address, operator_address2, 10);
```
