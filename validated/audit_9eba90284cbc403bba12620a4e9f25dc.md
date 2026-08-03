No vulnerability found for this question.

**Reasoning:**

The premise of the report — that an operator can call `set_beneficiary_for_operator` *before* `feature::operator_beneficiary_change_enabled` to "bypass the assert" — is not supported by the code. The very first line of the function aborts the transaction if the feature is disabled: [1](#0-0) 

There is no code path that lets the call succeed while the feature is disabled; the `assert!` unconditionally reverts the entire transaction, so nothing is "pre-staged." There's no bypass to exploit.

Separately, even granting the feature is enabled, the mechanism as designed does not constitute a hijack:

1. `BeneficiaryForOperator` is a resource stored under the operator's own address, not scoped to any particular staking pool — this is explicitly documented: "An operator can set one beneficiary for staking contract pools, not a separate one for each pool" (see the SDK builder doc comment). [2](#0-1) 
2. The lookup happens dynamically at distribution time, not at "request time" as the proof idea assumes — `distribute_internal` calls `beneficiary_for_operator(operator)` fresh each time commission is paid out, and only redirects the share that would otherwise go to the operator (`recipient == operator`): [3](#0-2) 
3. Only the operator's own address (`signer::address_of(operator)`) can set its beneficiary — it is self-authorized and does not require the operator to already have a staking contract: [4](#0-3) 
4. It only ever redirects commission that belongs to the operator itself, not the staker's principal or rewards. A staker still chooses to pair with that operator address when creating a `StakingContract`; nothing forces an unwilling staker into a relationship, and no value belonging to any other party is diverted.

Since no unprivileged input redirects value away from its rightful owner, and the described "bypass" doesn't actually work given the `assert!` guard, this does not meet the required-impact bar (no change to who can withdraw, earn, or recover stake/commission beyond the operator's own legitimately-designated share).

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L811-817)
```text
        operator: &signer, new_beneficiary: address
    ) acquires BeneficiaryForOperator {
        assert!(
            features::operator_beneficiary_change_enabled(),
            std::error::invalid_state(EOPERATOR_BENEFICIARY_CHANGE_NOT_SUPPORTED)
        );
        // The beneficiay address of an operator is stored under the operator's address.
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L819-829)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L895-898)
```text
            // If the recipient is the operator, send the commission to the beneficiary instead.
            if (recipient == operator) {
                recipient = beneficiary_for_operator(operator);
            };
```

**File:** aptos-move/framework/cached-packages/src/aptos_framework_sdk_builder.rs (L5042-5045)
```rust
/// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
/// beneficiary. To ensures payment to the current beneficiary, one should first call `distribute` before switching
/// the beneficiary. An operator can set one beneficiary for staking contract pools, not a separate one for each pool.
pub fn staking_contract_set_beneficiary_for_operator(
```
