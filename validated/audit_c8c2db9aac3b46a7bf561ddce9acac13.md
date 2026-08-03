No vulnerability found for this question.

**Reasoning:** The premise of the question is self-defeating. `set_beneficiary_for_operator` takes `operator: &signer` and uses `signer::address_of(operator)` to determine whose `BeneficiaryForOperator` resource to write/update [1](#0-0) . In Move, a `signer` capability can only be produced by the actual transaction sender for that address — there is no "race" that lets an unprivileged attacker who is neither the operator nor staker forge or hijack the operator's signer to call this function. The question's own proof idea concedes this: "attacker calls `set_beneficiary_for_operator` (expected to fail due to auth)" — meaning the attacker's call is expected to abort/fail, so there is no window in which the attacker can plant an unauthorized beneficiary value.

In `distribute_internal`, the beneficiary substitution merely reads whatever value is currently stored via `beneficiary_for_operator(operator)` [2](#0-1) , which can only ever have been set by the operator itself. `distribute` being publicly callable by anyone is intentional and documented ("Allow anyone to distribute already unlocked funds... does not need to be restricted to just the staker or operator") [3](#0-2) , but that only triggers redistribution to the legitimately-configured beneficiary/staker/operator addresses recorded in the `distribution_pool` — it does not let the caller redirect funds to an address of their choosing.

Since the role check (signer-based authorization on `operator_addr`) already blocks the attacker from ever influencing `BeneficiaryForOperator`, and `distribute`'s permissionless nature does not itself allow redirection of recipients, this does not meet the standard of an unprivileged input changing who can withdraw/earn/recover stake.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L810-829)
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L895-898)
```text
            // If the recipient is the operator, send the commission to the beneficiary instead.
            if (recipient == operator) {
                recipient = beneficiary_for_operator(operator);
            };
```
