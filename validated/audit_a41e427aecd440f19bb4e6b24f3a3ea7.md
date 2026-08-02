## Finding [1](#0-0) 

### Title
Commission distribution recorded during `switch_operator` bypasses the outgoing operator's beneficiary redirect - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::switch_operator` re-keys a staker's `StakingContract` from `old_operator` to `new_operator`, but before doing so it calls `request_commission_internal` for `old_operator`, which records a *new*, not-yet-distributed commission entry in the `distribution_pool` under `recipient = old_operator`. Because this pending entry now lives inside a `StakingContract` indexed by `new_operator`, the later `distribute_internal(staker, new_operator, ...)` call compares `recipient == operator` using `new_operator`, which never matches `old_operator`. The beneficiary redirect (`beneficiary_for_operator`) is therefore silently skipped for this entry, and the payout goes straight to `old_operator`'s own account instead of the beneficiary `old_operator` configured via `set_beneficiary_for_operator`.

### Finding Description
`switch_operator` performs the following sequence: [2](#0-1) 

1. It removes the `StakingContract` from the map keyed by `old_operator`.
2. `distribute_internal(staker_address, old_operator, &mut staking_contract)` flushes any already-recorded distributions — at this point `operator == old_operator` so any existing entry with `recipient == old_operator` is correctly redirected to `beneficiary_for_operator(old_operator)`.
3. `request_commission_internal(old_operator, &mut staking_contract)` computes commission owed to `old_operator` for stake gains since the last recorded principal, and calls `add_distribution(old_operator, staking_contract, old_operator, commission_amount)`, which stores a **new** shareholder entry keyed `old_operator` in `distribution_pool`, per: [3](#0-2) 

This commission is only *requested* (unlocked), not yet withdrawable — it will only be paid out on a future `distribute()`/`distribute_internal()` call.

4. The stake pool's operator is switched (`stake::set_operator_with_cap`), and the same `StakingContract` (still carrying the pending `old_operator`-keyed distribution) is reinserted into the map **under the key `new_operator`**.

When the funds finally become withdrawable and someone calls `distribute(staker, new_operator)`, `distribute_internal` is invoked with `operator = new_operator`: [4](#0-3) 

The loop iterates the pool's shareholders, finds `recipient = old_operator`, and checks `if (recipient == operator)`. Since `operator` is now `new_operator`, this comparison is always false for the stale entry, so the beneficiary substitution `recipient = beneficiary_for_operator(operator)` never triggers for `old_operator`'s pending commission. The coins are deposited directly to `old_operator`'s address instead of to the beneficiary address `old_operator` configured via `set_beneficiary_for_operator`: [5](#0-4) 

This is exactly the class of bug described in the external report: a check (“redirect commission to the current beneficiary”) is correctly performed relative to context that existed *at request time* (`operator == old_operator` when the entry was created), but the settlement code (`distribute_internal`) re-validates/re-derives that context using a *different, now-current* variable (`new_operator`) rather than the one the entry was actually created under, silently breaking the beneficiary invariant.

### Impact Explanation
This breaks the beneficiary/claim-right invariant for operator commission flows: an operator who has deliberately configured a `beneficiary_for_operator` (e.g., for custody, compliance, or key-separation reasons) will have one commission tranche silently paid to their own operator address instead, whenever a staker calls `switch_operator`/`switch_operator_with_same_commission` while there is unpaid commission accrued. This falls under "operator commission... beneficiary payout... corruption that credits the wrong account" from the required impact list — the beneficiary permanently and non-recoverably loses claim to that tranche of commission, with no code path to reconcile it afterward (the distribution_pool entry is fully redeemed and removed in that single `distribute_internal` call).

### Likelihood Explanation
`switch_operator` and `switch_operator_with_same_commission` are unprivileged, staker-callable `public entry` functions requiring no special role beyond being the staker who owns the `StakingContract`, and `set_beneficiary_for_operator` is unprivileged operator self-service. The trigger condition (calling `switch_operator` while commission has accrued since the last distribution) is common and easily reachable by any staker without any protocol- or admin-level cooperation, and can even be intentionally engineered by the staker (or in collusion with a new operator) to strip an old operator's beneficiary of a commission payment.

### Recommendation
**Short-term:** In `distribute_internal`, compare `recipient` against the *actual operator address that owns the distribution entry* rather than the caller-supplied `operator` parameter — e.g., look up `beneficiary_for_operator(recipient)` unconditionally for every payout (or persist the operator address alongside each distribution entry) instead of relying on `recipient == operator`.

**Long-term:** Document and test the `switch_operator` + pending-distribution interaction explicitly, and add unit tests asserting that beneficiary redirection still applies to commission requested for `old_operator` even after the contract has been reassigned to `new_operator`.

### Proof of Concept
1. Staker creates a staking contract with `operator = A`, non-zero commission.
2. `A` calls `set_beneficiary_for_operator(A_signer, beneficiary_B)`.
3. Stake pool earns rewards (`stake::end_epoch()`), so unpaid commission accrues to `A`.
4. Staker calls `switch_operator(staker, A, C, new_commission)`. Internally this calls `request_commission_internal(A, ...)`, adding a distribution entry keyed `A` inside the `StakingContract` now stored under key `C`.
5. Fast-forward past lockup (`stake::fast_forward_to_unlock`).
6. Anyone calls `distribute(staker_address, C)`. `distribute_internal` runs with `operator = C`; the stale entry `recipient = A` never equals `C`, so `beneficiary_for_operator(A)` (i.e., `B`) is never consulted, and the commission coins are deposited directly to `A` instead of `B`.

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L762-805)
```text
    public entry fun switch_operator(
        staker: &signer,
        old_operator: address,
        new_operator: address,
        new_commission_percentage: u64
    ) acquires Store, BeneficiaryForOperator {
        let staker_address = signer::address_of(staker);
        assert_staking_contract_exists(staker_address, old_operator);

        assert!(
            new_commission_percentage <= 100,
            error::invalid_argument(EINVALID_COMMISSION_PERCENTAGE)
        );
        // Merging two existing staking contracts is too complex as we'd need to merge two separate stake pools.
        let store = borrow_global_mut<Store>(staker_address);
        let staking_contracts = &mut store.staking_contracts;
        assert!(
            !staking_contracts.contains_key(&new_operator),
            error::invalid_state(ECANT_MERGE_STAKING_CONTRACTS)
        );

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
