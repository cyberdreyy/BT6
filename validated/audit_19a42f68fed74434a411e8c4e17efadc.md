No vulnerability found for this question.

**Analysis:**

The premise assumes that `distribute()` could execute "mid-flight" while `switch_operator()` is between its `staking_contracts.remove(&old_operator)` and re-add step, in a separate transaction. This is not possible under Aptos's execution model: each Move transaction (including entry functions like `switch_operator` and `distribute`) executes atomically to completion (or aborts entirely) before any other transaction can observe intermediate state. Aptos's parallel execution engine (Block-STM) guarantees the final result is always equivalent to some serial ordering of transactions — there is no way for another transaction to read a partially-mutated `Store` resource mid-removal.

Given atomicity, only two serial orderings are possible:

1. `distribute()` runs before `switch_operator()`: it operates on the current `StakingContract` under `old_operator`, correctly distributing any already-inactive stake using the pre-switch `commission_percentage`.
2. `distribute()` runs after `switch_operator()`: at that point the `old_operator` key no longer exists in `staking_contracts` (it was removed and the same struct re-added under `new_operator`), so `assert_staking_contract_exists(staker, old_operator)` aborts with `ENO_STAKING_CONTRACT_FOUND_FOR_STAKER`. There is no window where `distribute(staker, old_operator)` can succeed against a "stale" entry after the switch.

Moreover, `switch_operator` itself already enforces the exact invariant the proof idea wants to test: before re-keying the `StakingContract` to `new_operator` and updating `commission_percentage`, it explicitly calls `distribute_internal` and `request_commission_internal` for `old_operator` [1](#0-0) . This guarantees old-operator commission is settled at the old commission rate before the new rate takes effect and before the map key changes — matching what the submission's own "proof idea" wants to assert, meaning the invariant already holds by construction.

The `distribute` entry function is intentionally unrestricted to staker/operator only because it merely pays out already-unlocked (inactive) funds according to the pre-existing distribution-pool shares recorded at the time of the last `unlock`/`switch_operator`/`request_commission` call [2](#0-1) ; it does not read or depend on any operator identity beyond the `staking_contract` entry it is passed, and that entry is keyed and looked up atomically by `assert_staking_contract_exists`/`borrow_mut` in the same transaction [3](#0-2) .

Since Move/Aptos transaction atomicity precludes the described interleaving, and `switch_operator` already force-settles old-operator commission before re-keying, there is no race condition that misroutes commission between old and new operators.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L783-800)
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L922-930)
```text
    /// Assert that a staking_contract exists for the staker/operator pair.
    fun assert_staking_contract_exists(
        staker: address, operator: address
    ) acquires Store {
        assert!(
            exists<Store>(staker),
            error::not_found(ENO_STAKING_CONTRACT_FOUND_FOR_STAKER)
        );
        let staking_contracts = &borrow_global<Store>(staker).staking_contracts;
```
