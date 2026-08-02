No vulnerability found for this question.

**Analysis**: `synchronize_delegation_pool` is a `public entry fun` deliberately designed to be permissionless — it takes only a `pool_address` and no signer, and can be called by any account, matching the module's documented design: "at any interaction with the delegation pool, a process of synchronization to the underlying stake pool is executed" [1](#0-0) . The entry function itself has no ownership/operator gating [2](#0-1) .

The commission is routed via `buy_in_active_shares(pool, beneficiary_for_operator(stake::get_operator(pool_address)), commission_active)`, which reads whatever beneficiary is *currently* set at the moment synchronization runs [3](#0-2) . If an unrelated caller triggers `synchronize_delegation_pool` before the operator calls `set_beneficiary_for_operator`, the commission accrued *up to that point* is correctly credited to the beneficiary who was in effect during that accrual period (the old beneficiary) — this is not a misdirection of funds, it is the correct settlement of rewards earned under the previous beneficiary configuration.

This exact timing sensitivity is explicitly acknowledged in the codebase's own documentation for the beneficiary-change flow: "To ensure payment to the current beneficiary, one should first call `synchronize_delegation_pool` before switching the beneficiary" [4](#0-3) , and the equivalent doc-comment on the delegation-pool SDK builder [5](#0-4) . This confirms the behavior is a known, documented characteristic of the design (operators are instructed to sync before switching beneficiaries if they want precise control over which beneficiary captures pending commission), not an unintended invariant violation. `set_beneficiary_for_operator` also correctly captures `old_beneficiary` at call time for event purposes and does not retroactively alter already-synchronized commission [6](#0-5) .

Since a permissionless caller merely accelerates a synchronization that would have happened anyway (at the next epoch boundary or next pool interaction) and routes commission to the beneficiary legitimately in effect for the stake period being settled, no unprivileged party gains the ability to redirect value to an account it controls, nor does any owner/operator/beneficiary lose rightfully-earned commission — they only lose the ability to *time* the exact beneficiary snapshot, which the framework already documents as the operator's own responsibility to manage via ordering (`synchronize_delegation_pool` before `set_beneficiary_for_operator`).

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L23-26)
```text
As stake-state transitions and rewards are computed only at the stake pool level, the delegation pool
gets outdated. To mitigate this, at any interaction with the delegation pool, a process of synchronization
to the underlying stake pool is executed before the requested operation itself.

```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1917-1921)
```text
    public entry fun synchronize_delegation_pool(
        pool_address: address
    ) acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage {
        assert_delegation_pool_exists(pool_address);
        let pool = borrow_global_mut<DelegationPool>(pool_address);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1949-1951)
```text
        // reward operator its commission out of uncommitted active rewards (`add_stake` fees already excluded)
        buy_in_active_shares(pool, beneficiary_for_operator(stake::get_operator(pool_address)), commission_active);

```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L807-809)
```text
    /// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
    /// beneficiary. To ensures payment to the current beneficiary, one should first call `distribute` before switching
    /// the beneficiary. An operator can set one beneficiary for staking contract pools, not a separate one for each pool.
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

**File:** aptos-move/framework/cached-packages/src/aptos_framework_sdk_builder.rs (L523-526)
```rust
    /// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
    /// beneficiary. To ensure payment to the current beneficiary, one should first call `synchronize_delegation_pool`
    /// before switching the beneficiary. An operator can set one beneficiary for delegation pools, not a separate
    /// one for each pool.
```
