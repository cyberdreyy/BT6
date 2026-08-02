No vulnerability found for this question.

**Analysis:**

The `beneficiary_for_operator` mapping is a resource keyed by the address that stores it, and it can only be written via `set_beneficiary_for_operator`, which requires the caller to be the signer of that exact address: [1](#0-0) 

Since `move_to(operator, BeneficiaryForOperator {...})` uses `signer::address_of(operator)` as the resource key, an unprivileged attacker cannot plant a `BeneficiaryForOperator` resource under an address they do not control (i.e., a "soon-to-be operator" address belonging to someone else). The only entity that can write this resource at address X is whoever holds the signing key for X — meaning the scenario described (an attacker pre-provisioning a victim's future operator address) requires the attacker to already control that address's private key, which is the address itself setting its own beneficiary, not a third party attack.

Both `get_stake` and `synchronize_delegation_pool` resolve the beneficiary dynamically via the pool's *current* operator, obtained live from `stake::get_operator(pool_address)`, not a stale or attacker-supplied value: [2](#0-1) [3](#0-2) 

The design is intentional and documented: the beneficiary mapping is global per-operator-address (not per-pool), and an operator may configure it independent of pool assignment: "So, the operator does not need to be validated with respect to a staking pool. An operator can set one beneficiary for delegation pools, not a separate one for each pool" [4](#0-3) . The same pattern and rationale exist in `staking_contract.move`'s `set_beneficiary_for_operator` [5](#0-4) .

Because writing the resource always requires signer authority over the target address, there is no unprivileged entrypoint that lets an attacker redirect another account's future operator commission — the account that later becomes operator is the only one who could have set its own beneficiary in advance, which is expected self-configuration, not exploitation.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L673-681)
```text
        if (delegator_address == beneficiary_for_operator(get_operator(pool_address))) {
            active += commission_active;
            // in-flight pending_inactive commission can coexist with already inactive withdrawal
            if (lockup_cycle_ended) {
                inactive += commission_pending_inactive
            } else {
                pending_inactive += commission_pending_inactive
            }
        };
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1268-1291)
```text
    /// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
    /// beneficiary. To ensure payment to the current beneficiary, one should first call `synchronize_delegation_pool`
    /// before switching the beneficiary. An operator can set one beneficiary for delegation pools, not a separate
    /// one for each pool.
    public entry fun set_beneficiary_for_operator(
        operator: &signer,
        new_beneficiary: address
    ) acquires BeneficiaryForOperator {
        // The beneficiay address of an operator is stored under the operator's address.
        // So, the operator does not need to be validated with respect to a staking pool.
        let operator_addr = signer::address_of(operator);
        let old_beneficiary = beneficiary_for_operator(operator_addr);
        if (exists<BeneficiaryForOperator>(operator_addr)) {
            borrow_global_mut<BeneficiaryForOperator>(operator_addr).beneficiary_for_operator = new_beneficiary;
        } else {
            move_to(operator, BeneficiaryForOperator { beneficiary_for_operator: new_beneficiary });
        };

        emit(SetBeneficiaryForOperator {
            operator: operator_addr,
            old_beneficiary,
            new_beneficiary,
        });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1949-1956)
```text
        // reward operator its commission out of uncommitted active rewards (`add_stake` fees already excluded)
        buy_in_active_shares(pool, beneficiary_for_operator(stake::get_operator(pool_address)), commission_active);
        // reward operator its commission out of uncommitted pending_inactive rewards
        buy_in_pending_inactive_shares(
            pool,
            beneficiary_for_operator(stake::get_operator(pool_address)),
            commission_pending_inactive
        );
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L810-838)
```text

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
