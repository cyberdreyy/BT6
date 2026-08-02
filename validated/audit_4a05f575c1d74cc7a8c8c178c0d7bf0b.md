## Finding

### Title
Stale operator-keyed distribution entries bypass `beneficiary_for_operator` after `switch_operator` — commission is paid to the ex-operator's own address instead of their registered beneficiary - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`distribute_internal` only redirects a payout to `beneficiary_for_operator(operator)` when the distribution-pool `recipient == operator`, where `operator` is the *current* operator address passed in by the caller. However, `switch_operator` records a pending commission distribution keyed to the **old** operator's address (via `request_commission_internal` → `add_distribution`) right before rekeying the `StakingContract` under the new operator. Any later `distribute`/`unlock_stake`/`request_commission` call is invoked with the *new* operator as the `operator` parameter, so the stale entry keyed to the old operator never matches `recipient == operator`, and the beneficiary redirect is silently skipped.

### Finding Description
- `switch_operator` first settles the old operator's owed rewards, then requests final commission for the old operator, then moves the `StakingContract` under the new operator key: [1](#0-0) 

- `request_commission_internal` books the old operator's pending commission into the shared `distribution_pool` keyed by the operator address that was passed in (the *old* operator at that call site): [2](#0-1) 

- The commission bookkeeping and the actual coin payout are decoupled: `distribute_internal` (called later, e.g. from a subsequent `distribute`, `unlock_stake`, or `request_commission` invocation with the **current/new** operator) iterates over every shareholder recorded in `distribution_pool`, and only substitutes the beneficiary when the recipient equals the operator argument passed to `distribute_internal` for that call: [3](#0-2) 

Since the stale entry's key is the *old* operator's address but the `operator` argument on the later call is the *new* operator, `recipient == operator` is false, so `beneficiary_for_operator(operator)` is never consulted for that entry — the payout goes straight to the old operator's account address instead of the beneficiary address the old operator configured via `set_beneficiary_for_operator`: [4](#0-3) 

Note the funds are not actually withdrawn from the stake pool until the lockup expires and enough time passes for the requested commission to become `inactive`; only then does `distribute_internal` process (and mis-route) this stale entry.

### Impact Explanation
This falls under the explicitly accepted impact category "Operator commission, beneficiary payout, or share-accounting corruption that credits the wrong account or traps value." An operator who has designated a beneficiary (e.g., for compliance, custody, or payroll-splitting reasons) to receive their commission will have that specific chunk of commission — the one requested at the moment they are switched out — silently redirected to their own default account instead of the beneficiary they configured, breaking the beneficiary invariant that `set_beneficiary_for_operator` is supposed to guarantee. This is triggered purely by the staker calling `switch_operator`/`switch_operator_with_same_commission`, a normal unprivileged staking-contract owner action, with no admin/governance intervention needed.

### Likelihood Explanation
Any staker who owns a `staking_contract` can call `switch_operator` at any time (their own contract, not requiring special privilege), and doing so is a documented, commonly-used flow (e.g. for changing validator operators). The bug triggers deterministically whenever: (1) the old operator has set a beneficiary, (2) the staker switches operators while commission is still owed, and (3) the pool later processes a payout (which happens automatically once stake becomes inactive). No attacker collusion is required beyond ordinary contract operation.

### Recommendation
`distribute_internal` should not rely on comparing `recipient == operator` (current operator) to decide whether to redirect to a beneficiary. Instead, every payout recipient in the distribution pool should be checked against `beneficiary_for_operator` if that recipient is (or ever was) recorded as an operator-type distribution, e.g. by tagging distribution entries with an explicit "is operator commission" flag at `add_distribution` time and resolving the beneficiary based on the *recorded* operator address, not the operator parameter of the call that happens to trigger the flush.

### Proof of Concept
1. Staker creates a `staking_contract` with `operator_A`, and `operator_A` accrues rewards.
2. `operator_A` calls `set_beneficiary_for_operator(beneficiary_X)`.
3. Staker calls `switch_operator(staker, operator_A, operator_B, new_commission)` before `operator_A`'s outstanding commission has been distributed. This calls `request_commission_internal(operator_A, ...)`, which books `commission_amount` into `distribution_pool` keyed to `operator_A`, and then rekeys the `StakingContract` under `operator_B`.
4. Time passes until the pending commission becomes `inactive` on the stake pool.
5. Anyone calls `distribute(staker, operator_B)` (or the staker calls `unlock_stake`/`request_commission` for `operator_B`). This runs `distribute_internal(staker, operator_B, staking_contract)`, which iterates the `distribution_pool` and finds the stale `operator_A`-keyed entry. Since `operator_A != operator_B`, the redirect check fails and the commission is paid directly to `operator_A`'s address instead of `beneficiary_X`.

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
