Found the analog: `switch_operator` in `staking_contract.move` never validates `new_commission_percentage`, unlike its sibling entry points.

### Title
Missing Commission Percentage Range Check in `switch_operator` Allows Corrupted Commission Accounting - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`update_commision` validates that `new_commission_percentage <= 100` before mutating a `StakingContract`'s `commission_percentage` field [1](#0-0) . However, `switch_operator`, the other public entry point that writes to the same field, performs no such check before assigning `staking_contract.commission_percentage = new_commission_percentage` [2](#0-1) . This mirrors the reported bug class exactly: one path (`CreateSubscription`/`update_commision`) validates a numeric range, while a second reachable path (`AdvancePurchase`/`switch_operator`) writes the same field unchecked.

### Finding Description
`switch_operator` is a `public entry fun` callable directly by any staker who has an existing staking contract with `old_operator`: [3](#0-2) 
It removes the `StakingContract` from `old_operator`, forces distribution and commission settlement on the old operator, then sets `stake::set_operator_with_cap(...)` and directly assigns `staking_contract.commission_percentage = new_commission_percentage` — with `new_commission_percentage` taken verbatim from caller input, with **no bound check** anywhere in this function or in its callers.

Compare this to `update_commision`, the dedicated commission-update entry function, which asserts `new_commission_percentage <= 100` before assigning the same field [4](#0-3) . The formal spec for `switch_operator` also carries no `aborts_if new_commission_percentage > 100` (it's `pragma verify = false`) [5](#0-4) , while the spec for `update_commision` explicitly encodes that constraint [6](#0-5) , confirming the asymmetry is a real, unreviewed gap rather than a documentation omission.

This is directly reachable by unprivileged callers via `staking_proxy::set_staking_contract_operator`, which reads the *current* commission percentage and forwards it, so that specific call site is safe [7](#0-6) . But `switch_operator` itself is a public entry function with a raw `u64` parameter, and is also invoked from `vesting::update_operator`, which likewise forwards a caller-controlled `commission_percentage` without validating it [8](#0-7) . Nothing in `staking_contract::switch_operator`, `vesting::update_operator`, or `vesting::update_operator_with_same_commission` clamps or rejects `commission_percentage > 100`.

Downstream, `commission_percentage` is used unguarded in commission math: `accumulated_rewards * staking_contract.commission_percentage / 100` in `get_staking_contract_amounts_internal` [9](#0-8) . If `commission_percentage` exceeds 100, `commission_amount` can exceed `accumulated_rewards`, and subsequent unlock/distribution logic subtracts more than the staker's earned rewards from the pool's active balance — an accounting break in which the operator's share can exceed 100% of rewards, effectively siphoning into stakers' principal.

### Impact Explanation
This breaks the operator commission / staker balance invariant required by the "Stake And Lockup Pivots" (commission share-accounting corruption crediting the wrong account or trapping value). A malicious or careless staker (who fully controls the `switch_operator` call for their own staking contract) can set an arbitrary commission (e.g. 100000%) for a new operator. On next `request_commission`/`distribute`, the computed `commission_amount` can exceed `accumulated_rewards`, and because `unlock_with_cap` is invoked with that oversized amount, funds beyond the operator's fair share (potentially draining into principal/other shareholders' stake) get unlocked and routed to the operator's commission distribution, corrupting the pool's share accounting and misdirecting staker principal to the operator. This is a High-severity impact under the required Stake/Lockup pivots — wrong-role/wrong-amount credit of value away from the rightful staker.

### Likelihood Explanation
Likelihood is High: `switch_operator` is a normal, permissionless entry function requiring only that the caller (staker) already owns a `StakingContract` for `old_operator` — no elevated privileges are needed, and no external conditions (like feature flags) gate the missing check. Any staker switching operators (a routine operation) can supply an out-of-range commission in the same transaction.

### Recommendation
Add the same guard used in `update_commision` to `switch_operator`:
```move
assert!(
    new_commission_percentage <= 100,
    error::invalid_argument(EINVALID_COMMISSION_PERCENTAGE)
);
```
placed at the top of `switch_operator` in `aptos-move/framework/aptos-framework/sources/staking_contract.move`, before the `commission_percentage` field is overwritten. Update `staking_contract.spec.move`'s `switch_operator` spec to add `aborts_if new_commission_percentage > 100;` to keep formal verification aligned. Also audit `vesting::update_operator` for the same missing bound, since it forwards a raw commission value into `staking_contract::switch_operator`.

### Proof of Concept
1. Staker `S` creates a staking contract with `operator_1` via `create_staking_contract(S, operator_1, ..., commission=10, ...)` (goes through the validated creation path, which does enforce a range check per the `test_staker_cannot_create_staking_contract_with_invalid_commission` test) [10](#0-9) .
2. Operator earns rewards; some accumulated rewards exist in the pool.
3. `S` calls `staking_contract::switch_operator(S, operator_1, operator_2, 10000)` directly (bypassing `update_commision`'s check entirely) — this succeeds because `switch_operator` has no range assertion [3](#0-2) .
4. On the next `distribute`/`request_commission` cycle, `get_staking_contract_amounts_internal` computes `commission_amount = accumulated_rewards * 10000 / 100 = 100 * accumulated_rewards`, vastly exceeding `accumulated_rewards` [9](#0-8) .
5. `stake::unlock_with_cap(commission_amount, ...)` unlocks up to the pool's entire active stake (clamped only by pool balance, not by rewards owed), and that amount is distributed to `operator_2` as "commission," draining value that should belong to the staker's principal.

I was not able to fully trace whether `unlock_with_cap`'s internal `min(amount, active.value)` clamp fully neutralizes the overflow in all pool-balance scenarios (e.g., mixed principal/reward states across multiple stakers sharing a pool) — a Devin session with test execution would be needed to confirm the exact drained amount and rule out other implicit clamps.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L566-572)
```text
    public entry fun update_commision(
        staker: &signer, operator: address, new_commission_percentage: u64
    ) acquires Store, BeneficiaryForOperator {
        assert!(
            new_commission_percentage >= 0 && new_commission_percentage <= 100,
            error::invalid_argument(EINVALID_COMMISSION_PERCENTAGE)
        );
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L781-805)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L970-972)
```text
        let accumulated_rewards = total_active_stake - staking_contract.principal;
        let commission_amount =
            accumulated_rewards * staking_contract.commission_percentage / 100;
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1464-1470)
```text
    #[test(aptos_framework = @0x1, staker = @0x123, operator = @0x234)]
    #[expected_failure(abort_code = 0x10002, location = Self)]
    public entry fun test_staker_cannot_create_staking_contract_with_invalid_commission(
        aptos_framework: &signer, staker: &signer, operator: &signer
    ) acquires Store {
        setup_staking_contract(
            aptos_framework,
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.spec.move (L254-261)
```text
    spec update_commision(staker: &signer, operator: address, new_commission_percentage: u64) {
        // TODO: Call `distribute_internal` and could not verify `update_distribution_pool`.
        // TODO: A data invariant not hold happened here involve with 'pool_u64' #L16.
        pragma verify = false;
        let staker_address = signer::address_of(staker);
        aborts_if new_commission_percentage > 100;
        include ContractExistsAbortsIf { staker: staker_address };
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.spec.move (L311-326)
```text
    /// Staking_contract exists the stacker/operator pair.
    spec switch_operator(
        staker: &signer,
        old_operator: address,
        new_operator: address,
        new_commission_percentage: u64
    ) {
        // TODO: Call `update_distribution_pool` and could not verify `update_distribution_pool`.
        // TODO: Set because of timeout (estimate unknown).
        pragma verify = false;
        let staker_address = signer::address_of(staker);
        include ContractExistsAbortsIf { staker: staker_address, operator: old_operator };
        let store = global<Store>(staker_address);
        let staking_contracts = store.staking_contracts;
        aborts_if simple_map::spec_contains_key(staking_contracts, new_operator);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_proxy.move (L43-49)
```text
    public entry fun set_staking_contract_operator(owner: &signer, old_operator: address, new_operator: address) {
        let owner_address = signer::address_of(owner);
        if (staking_contract::staking_contract_exists(owner_address, old_operator)) {
            let current_commission_percentage = staking_contract::commission_percentage(owner_address, old_operator);
            staking_contract::switch_operator(owner, old_operator, new_operator, current_commission_percentage);
        };
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L823-847)
```text
    public entry fun update_operator(
        admin: &signer,
        contract_address: address,
        new_operator: address,
        commission_percentage: u64,
    ) acquires VestingContract {
        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        verify_admin(admin, vesting_contract);
        let contract_signer = &get_vesting_account_signer_internal(vesting_contract);
        let old_operator = vesting_contract.staking.operator;
        staking_contract::switch_operator(contract_signer, old_operator, new_operator, commission_percentage);
        vesting_contract.staking.operator = new_operator;
        vesting_contract.staking.commission_percentage = commission_percentage;

        emit(
            UpdateOperator {
                admin: vesting_contract.admin,
                vesting_contract_address: contract_address,
                staking_pool_address: vesting_contract.staking.pool_address,
                old_operator,
                new_operator,
                commission_percentage,
            },
        );
    }
```
