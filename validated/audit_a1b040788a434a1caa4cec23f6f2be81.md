Based on tracing `distribute`/`distribute_internal`/`switch_operator` in `staking_contract.move`, the specific mechanism hypothesized in the question — that unpaid pre-switch commission gets paid to the **new** operator's beneficiary — does not occur. Instead, I found a related but distinct misrouting bug: pre-switch commission bypasses the **old** operator's own beneficiary and is paid to the old operator's raw address.

### Title
Stale pre-switch commission distribution bypasses old operator's `beneficiary_for_operator` and pays the raw operator address - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`switch_operator` forces `request_commission_internal(old_operator, ...)` before moving the `StakingContract` to the `new_operator` key [1](#0-0) . That call records a distribution-pool share keyed to `old_operator` for any commission accrued since the pool's stake became unlockable but not yet withdrawn [2](#0-1) . When this share is later paid out via a subsequent `distribute(staker, new_operator)` call, `distribute_internal` only substitutes the beneficiary when `recipient == operator`, where `operator` is the *current* operator parameter (`new_operator`), not the address that actually owns the share (`old_operator`) [3](#0-2) . Consequently, the old operator's commission is paid directly to `old_operator`'s address instead of `beneficiary_for_operator(old_operator)`.

### Finding Description
1. Staker calls `switch_operator(old_operator, new_operator, ...)`. This forces `distribute_internal` (settling any already-inactive funds correctly through old operator's beneficiary) and then `request_commission_internal`, which unlocks any newly accrued commission and records it as a new distribution-pool share under the key `old_operator` [4](#0-3) .
2. The `StakingContract` struct (including its `distribution_pool`) is then reassigned to the `new_operator` key in the `Store` map [5](#0-4) .
3. Any unprivileged caller can later call the permissionless `distribute(staker, new_operator)` entry function [6](#0-5) .
4. Inside `distribute_internal`, the loop iterates all shareholders including the stale `old_operator` entry. The beneficiary substitution check `if (recipient == operator)` compares against the function's `operator` parameter (`new_operator`), which never equals `old_operator`, so the substitution is skipped and the payment goes to `old_operator`'s raw address rather than `beneficiary_for_operator(old_operator)` [3](#0-2) .

### Impact Explanation
If the old operator had configured a `beneficiary_for_operator` (via `set_beneficiary_for_operator`) to receive commission payouts, any commission accrued but not yet distributed/withdrawn at the moment of an operator switch is silently redirected to the old operator's own account address instead of their configured beneficiary. This breaks the beneficiary/operator payout boundary the feature is designed to preserve [7](#0-6) .

### Likelihood Explanation
This triggers under normal usage whenever a staker switches operators without first calling `distribute` to flush all pending commission, and the old operator has a beneficiary configured. The final `distribute` call that surfaces the bug is fully permissionless and requires no special privilege.

### Recommendation
In `distribute_internal`, the beneficiary substitution should be based on the recipient's own beneficiary mapping rather than comparing to the currently-active `operator` parameter, e.g. `if (exists<BeneficiaryForOperator>(recipient)) { recipient = beneficiary_for_operator(recipient); }`, so that stale shares recorded under `old_operator` are also correctly redirected to that operator's beneficiary.

### Proof of Concept
1. `create_staking_contract(staker, operator1, ..., commission=10)`; `operator1` joins validator set; `set_beneficiary_for_operator(operator1, beneficiary1)`.
2. Advance an epoch so rewards/commission accrue for `operator1`, but do **not** call `distribute` or `request_commission` yet.
3. Call `switch_operator(staker, operator1, operator2, new_commission)`. This internally calls `request_commission_internal(operator1, ...)`, recording a distribution share keyed to `operator1` for the newly accrued commission.
4. Fast-forward past the lockup period so the unlocked commission becomes withdrawable.
5. Call `distribute(staker, operator2)` (unprivileged, permissionless call).
6. Assert `coin::balance<AptosCoin>(operator1_address)` increased by the commission amount, while `coin::balance<AptosCoin>(beneficiary1_address)` remains unchanged — demonstrating the pre-switch commission bypassed `operator1`'s configured beneficiary and paid `operator1` directly. [8](#0-7)  shows the existing test suite already exercises a *pre-distributed* switch scenario (where commission is flushed via `distribute` before the switch, so no stale share exists), which is why this stale-share/beneficiary-bypass path is not currently covered by tests.

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L807-810)
```text
    /// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
    /// beneficiary. To ensures payment to the current beneficiary, one should first call `distribute` before switching
    /// the beneficiary. An operator can set one beneficiary for staking contract pools, not a separate one for each pool.
    public entry fun set_beneficiary_for_operator(
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1794-1822)
```text
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
```
