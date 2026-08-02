## Finding: `set_beneficiary_for_operator` mutates the commission-recipient address without checkpointing already-accrued, unsynced commission

### Title
Operator can redirect not-yet-synchronized commission by switching beneficiary before a checkpoint - (File: `aptos-move/framework/aptos-framework/sources/delegation_pool.move`, also present in `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
The reported bug class is: a state-changing setter (`setEpochDuration`) mutates a parameter that gates an accounting/accrual boundary without first "closing the books" (`transitionEpoch`), so the result of a passed period depends on transaction ordering. The Aptos analog is `delegation_pool::set_beneficiary_for_operator` (and the equivalent function in `staking_contract.move`), which changes the address that receives operator commission **without first calling the checkpoint function that realizes pending commission** (`synchronize_delegation_pool` / `distribute_internal`). Sibling setters in the same modules — `set_operator`, `update_commission_percentage`, `delegate_voting_power`, `vote`, `enable_partial_governance_voting` — all explicitly checkpoint state first; `set_beneficiary_for_operator` does not.

### Finding Description
In `delegation_pool.move`: [1](#0-0) 

Compare to the neighboring `update_commission_percentage`, which explicitly comments and enforces a checkpoint before mutating state: [2](#0-1) 

and `set_operator`, which does the same: [3](#0-2) 

The actual crediting of accrued-but-unsynced commission happens only inside `synchronize_delegation_pool` (or transitively `distribute_internal` in `staking_contract.move`), which looks up the recipient via `beneficiary_for_operator(operator)` **at the moment the checkpoint runs**, not at the moment the reward accrued: [4](#0-3) 

Because `set_beneficiary_for_operator` never triggers this checkpoint itself, all commission that accrued between the last checkpoint and the beneficiary switch — which economically "belongs" to whoever was beneficiary during that accrual window — is instead paid out to whichever beneficiary is active the next time anyone (anyone can call `synchronize_delegation_pool`, it's a public entry function) triggers a sync. This is exactly the CashManager pattern: the same sequence of operations (accrue → change parameter → checkpoint) produces two different, ordering-dependent outcomes:
- Front-run: sync before beneficiary switch → old beneficiary is paid the pre-switch accrual, new beneficiary only gets post-switch accrual.
- Back-run: beneficiary switch before any sync → new beneficiary sweeps the entire unsynced accrual, including the portion that accrued while the old beneficiary held the role.

The identical structure exists in `staking_contract::set_beneficiary_for_operator`, which also mutates `BeneficiaryForOperator` directly with no prior `distribute_internal`/`request_commission_internal` call: [5](#0-4) 

### Impact Explanation
This lets an operator (who legitimately controls their own beneficiary setting) unilaterally decide, after the fact, which address captures commission that had already economically accrued under a previous beneficiary assignment — e.g. a revenue-share or custodial beneficiary relationship where the beneficiary address represents a different, non-operator-controlled party's claim to commission. By simply calling `set_beneficiary_for_operator` before any synchronization occurs (which the operator can trivially arrange since `synchronize_delegation_pool`/`distribute`/`distribute_internal` are otherwise only invoked lazily on other user actions), the operator can strand or redirect commission owed to the prior beneficiary. This is an accounting/checkpoint gap that credits the wrong account with value that should have been attributed to a different role-holder, matching the "Operator commission, beneficiary payout ... corruption that credits the wrong account" impact category.

That said, the practical severity is bounded: the value in question is the operator's own commission stream (not delegator principal or rewards — `synchronize_delegation_pool`'s delegator share updates are unaffected by the beneficiary), and the ability to exploit it is confined to the operator's own commission accrual window, requiring the operator (a role that is inherently privileged over its own commission) to act adversarially against its own designated beneficiary. It does not let an unprivileged third party steal funds from an owner/operator/delegator they don't control.

### Likelihood Explanation
High feasibility for the party who controls it (the operator), since no special timing, front-running, or race condition against another actor's transaction is even required — the operator can simply choose not to synchronize before switching beneficiaries. However, this only matters when the beneficiary is a distinct party from the operator with an expectation of continuous payout (e.g. custody/revenue-sharing arrangements), and it requires the "victim" to be the currently-set beneficiary — not a delegator or staker, whose principal/rewards remain unaffected.

### Recommendation
Mirror the pattern already used by `set_operator` and `update_commission_percentage`: call `synchronize_delegation_pool(pool_address)` (and the corresponding `distribute_internal`/`request_commission_internal` pair in `staking_contract.move`) at the start of `set_beneficiary_for_operator`, before reading `old_beneficiary` or mutating `BeneficiaryForOperator`, so that any commission accrued up to that point is paid to the outgoing beneficiary prior to the switch taking effect.

### Proof of Concept
Conceptual PoC (Move test, analogous to the CashManager PoC pattern) for `delegation_pool.move`:
1. Operator initializes a delegation pool and accrues several epochs of active/pending-inactive rewards, generating unsynced commission for `beneficiary_A` (the currently set beneficiary or the operator itself by default).
2. Operator calls `set_beneficiary_for_operator(operator, beneficiary_B)` directly — no prior `synchronize_delegation_pool` call.
3. Someone (anyone) calls `synchronize_delegation_pool(pool_address)`, e.g. as a side effect of a delegator calling `add_stake`/`unlock`/`vote`.
4. Assert: `beneficiary_B` receives shares for the entire commission window, including the epochs during which `beneficiary_A` was the recorded beneficiary — reproducing the same "case1 vs case2" ordering-dependent divergence shown in the original report's `test_bug_inconsistentOutputOf_setEpochDuration_Case1`/`Case2` tests, but for commission-recipient checkpoints instead of epoch duration.

Note: I was not able to find an existing framework test that specifically exercises this exact beneficiary/sync ordering scenario in the indexed code; the trace above is derived directly from reading `set_beneficiary_for_operator`, `update_commission_percentage`, and `synchronize_delegation_pool`, confirming the missing checkpoint by direct comparison with the sibling functions that do checkpoint. If the exact runtime numbers are needed, a Devin session with full repo/test access would be needed to write and run the concrete Move unit test.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1256-1266)
```text
    /// Allows an owner to change the operator of the underlying stake pool.
    public entry fun set_operator(
        owner: &signer,
        new_operator: address
    ) acquires DelegationPoolOwnership, DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage {
        let pool_address = get_owned_pool_address(signer::address_of(owner));
        // synchronize delegation and stake pools before any user operation
        // ensure the old operator is paid its uncommitted commission rewards
        synchronize_delegation_pool(pool_address);
        stake::set_operator(&retrieve_stake_pool_owner(borrow_global<DelegationPool>(pool_address)), new_operator);
    }
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

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1293-1313)
```text
    /// Allows an owner to update the commission percentage for the operator of the underlying stake pool.
    public entry fun update_commission_percentage(
        owner: &signer,
        new_commission_percentage: u64
    ) acquires DelegationPoolOwnership, DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage {
        assert!(new_commission_percentage <= MAX_FEE, error::invalid_argument(EINVALID_COMMISSION_PERCENTAGE));
        let owner_address = signer::address_of(owner);
        let pool_address = get_owned_pool_address(owner_address);
        assert!(
            operator_commission_percentage(pool_address) + MAX_COMMISSION_INCREASE >= new_commission_percentage,
            error::invalid_argument(ETOO_LARGE_COMMISSION_INCREASE)
        );
        assert!(
            stake::get_remaining_lockup_secs(pool_address) >= min_remaining_secs_for_commission_change(),
            error::invalid_state(ETOO_LATE_COMMISSION_CHANGE)
        );

        // synchronize delegation and stake pools before any user operation. this ensures:
        // (1) the operator is paid its uncommitted commission rewards with the old commission percentage, and
        // (2) any pending commission percentage change is applied before the new commission percentage is set.
        synchronize_delegation_pool(pool_address);
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L811-838)
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
