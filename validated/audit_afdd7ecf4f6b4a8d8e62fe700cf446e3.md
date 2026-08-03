## Analysis

I traced the flow through `switch_operator`, `request_commission_internal`, `add_distribution`, and `distribute_internal` in `staking_contract.move`.

**Key finding:** `distribute_internal`'s beneficiary redirection is keyed off the *current* `operator` parameter (the caller-supplied dictionary key), not off the actual identity that was recorded as `distribution_pool` shareholder: [1](#0-0) 

Meanwhile, `switch_operator` moves the `StakingContract` from the `old_operator` key to the `new_operator` key, but any distribution share that was recorded for `old_operator` (e.g. via `request_commission_internal`'s `add_distribution(operator, ..., recipient=operator, ...)`) is *not* re-keyed — it stays a plain shareholder entry equal to `old_operator`'s address inside the same `distribution_pool`: [2](#0-1) 

`StakingContract` itself has no persisted "operator" field — the operator identity is only ever the `SimpleMap` key or a function parameter passed in by the caller of `distribute`/`request_commission`/etc.

## Title
Post-switch commission bypasses old operator's beneficiary redirect in `staking_contract::distribute_internal` — ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Summary
When a staker calls `switch_operator_with_same_commission`/`switch_operator`, any commission already recorded as a pending `distribution_pool` share for `old_operator` (but not yet physically distributable, e.g. because stake had not yet fully unlocked) survives the move to the `new_operator` key unmodified. When that pending amount is later paid out via a subsequent, permissionless call to `distribute(staker, new_operator)`, `distribute_internal` compares the shareholder address (`old_operator`) against the *current* `operator` parameter (`new_operator`). Since they differ, the beneficiary-redirect branch `if (recipient == operator) { recipient = beneficiary_for_operator(operator) }` never triggers for that stale entry, so the funds are paid directly to `old_operator`'s raw account address instead of the beneficiary `old_operator` had configured via `set_beneficiary_for_operator`.

### Finding Description
1. `old_operator` calls `set_beneficiary_for_operator` to route commission payouts to a separate `beneficiary` address (a common security practice to isolate the validator/consensus key from the funds-receiving key): [3](#0-2) 
2. Commission accrues and `request_commission`/`unlock_stake` is called (by staker, operator, or beneficiary — permissionless w.r.t. role check), which calls `request_commission_internal`, adding a distribution share keyed to `old_operator`'s raw address via `add_distribution(operator, staking_contract, operator, commission_amount)`: [4](#0-3) 
   At this point the underlying stake may still be `pending_inactive` (lockup not yet expired), so this share is not yet physically paid out — `distribute_internal` short-circuits when `distribution_amount == 0`: [5](#0-4) 
3. Before that lockup expires, the staker (an unprivileged action limited to their own contract, not requiring operator/beneficiary permission) calls `switch_operator_with_same_commission(old_operator, new_operator)`. This moves the `StakingContract` record from key `old_operator` to key `new_operator`, but the pending share for `old_operator`'s address inside `distribution_pool` is untouched: [2](#0-1) 
4. Later, once the lockup expires, **anyone** (fully permissionless) calls `distribute(staker_address, new_operator)`: [6](#0-5) 
   This invokes `distribute_internal` with `operator = new_operator`. When it iterates shareholders and reaches the stale `old_operator` entry, the check `recipient == operator` (i.e. `old_operator == new_operator`) is false, so the beneficiary substitution is skipped and the coins are deposited straight to `old_operator`'s address rather than to the beneficiary `old_operator` configured.

This breaks the beneficiary/operator security boundary: the entire purpose of `set_beneficiary_for_operator` is to let an operator receive commission at an address different from (and presumably better secured than) its consensus/operator key. This bug silently reverts that protection for any commission that was pending-but-undistributed at the moment of an operator switch.

### Impact Explanation
Funds that should be routed to `old_operator`'s designated beneficiary are instead deposited to `old_operator`'s own account address. Because the design intent of the beneficiary feature is key isolation (e.g., operator/consensus keys are often considered higher-risk / more exposed than a cold beneficiary wallet), this bug re-exposes commission funds to whatever party controls the (potentially higher-risk) operator key, defeating the beneficiary security guarantee. It falls under the "beneficiary boundary" pivot in the review scope, and the misdirection can be triggered by any permissionless caller of `distribute`, without requiring staker/operator/beneficiary privilege for the final trigger step.

### Likelihood Explanation
This requires a specific but realistic ordering: a beneficiary is set, commission is requested while stake is still `pending_inactive` (very common, since `request_commission`/`unlock_stake` are typically called before the ~lockup period fully elapses), and a `switch_operator*` call happens before that pending amount is distributed. Operator switches (e.g. via CLI `aptos stake set-operator`, which calls `staking_contract_switch_operator_with_same_commission`) are a normal, supported operation, so this ordering is plausible in production without any attacker coordination beyond timing a permissionless `distribute` call.

### Recommendation
`distribute_internal` should not rely on comparing the shareholder address to the *current* `operator` parameter to decide beneficiary redirection. Instead, either:
- Re-key/settle all pending distributions for `old_operator` (forcing a full payout, including beneficiary redirection, using the still-correct `old_operator` context) before allowing `switch_operator` to reassign the map key, or
- Track distribution shares with an explicit "is-commission-for-operator-X" flag/tag rather than relying on address equality with a parameter that can change identity after a switch, so that beneficiary resolution always uses the operator address that was in effect at the time the commission was earned.

### Proof of Concept
Extend `test_staker_can_switch_operator_with_same_commission` (around [7](#0-6) ) as follows:
1. `setup_staking_contract(... operator_1 ..., 10)`.
2. `set_beneficiary_for_operator(operator_1, beneficiary_1_address)`.
3. Advance an epoch so rewards accrue, then call `request_commission(operator_1, staker_address, operator_1_address)` — this creates a distribution share for `operator_1_address` in `distribution_pool` while stake is `pending_inactive` (before `stake::fast_forward_to_unlock`).
4. Call `switch_operator_with_same_commission(staker, operator_1_address, operator_2_address)` — note the pending share for `operator_1_address` is carried over unmodified into the (now `operator_2`-keyed) `StakingContract`.
5. `stake::fast_forward_to_unlock(pool_address)`.
6. Call `distribute(staker_address, operator_2_address)`.
7. Assert `coin::balance<AptosCoin>(beneficiary_1_address) == 0` and that the commission amount instead landed in `coin::balance<AptosCoin>(operator_1_address)` — demonstrating the beneficiary redirect was bypassed for the stale pending distribution.

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L868-878)
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1660-1685)
```text
    public entry fun test_staker_can_switch_operator_with_same_commission(
        aptos_framework: &signer,
        staker: &signer,
        operator_1: &signer,
        operator_2: &signer
    ) acquires Store, BeneficiaryForOperator {
        setup_staking_contract(
            aptos_framework,
            staker,
            operator_1,
            INITIAL_BALANCE,
            10
        );
        let staker_address = signer::address_of(staker);
        let operator_1_address = signer::address_of(operator_1);
        let operator_2_address = signer::address_of(operator_2);

        // Switch operators.
        switch_operator_with_same_commission(
            staker, operator_1_address, operator_2_address
        );
        // The staking_contract should now be associated with operator 2 but with same commission rate.
        assert!(staking_contract_exists(staker_address, operator_2_address), 0);
        assert!(!staking_contract_exists(staker_address, operator_1_address), 1);
        assert!(commission_percentage(staker_address, operator_2_address) == 10, 2);
    }
```
