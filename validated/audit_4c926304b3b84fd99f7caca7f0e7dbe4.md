## Summary

The external report's core bug pattern is: **a safety guard (percentage cap + timing lockout) that exists for one channel of an operation is absent from a functionally-identical channel of the same operation**, letting a semi-trusted role extract value that the guard was designed to prevent, with no active approval needed.

The Aptos-native analog is in operator commission-rate changes across `delegation_pool` vs. `staking_contract`/`vesting`.

### Title
Vesting/staking_contract operator commission change bypasses delegation_pool's commission-increase and timing safeguards, letting the vesting admin redirect shareholder rewards to a colluding operator - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`delegation_pool::update_commission_percentage` enforces two protections before an operator's commission can be raised: the increase per change is capped by `MAX_COMMISSION_INCREASE`, and the change is blocked if the pool's remaining lockup is below `min_remaining_secs_for_commission_change()` (aborting with `ETOO_LATE_COMMISSION_CHANGE`), specifically to stop a pool owner/operator from grabbing an outsized share of rewards from delegators right before a lockup cycle ends. [1](#0-0) 

`staking_contract::switch_operator` (and its wrapper `switch_operator_with_same_commission`) implements the structurally identical action - reassigning a `StakingContract`'s `commission_percentage` - but only asserts `new_commission_percentage <= 100`. There is no increase cap and no lockup-remaining/timing check. [2](#0-1) 

`vesting::update_operator`, callable at any time by the vesting contract's `admin` with a caller-supplied `commission_percentage`, calls `staking_contract::switch_operator` directly with that value. [3](#0-2) 

### Finding Description
In `delegation_pool`, the pool owner does not solely bear the economic consequence of a commission increase - delegators do, because rewards accrued to the `active`/`pending_inactive` shares pools are split with the operator via `commission_percentage`. The module's authors clearly recognized this and added `MAX_COMMISSION_INCREASE` and the lockup-remaining check to stop the owner from raising commission unilaterally and immediately before payout.

In `vesting`, the underlying `staking_contract::StakingContract` plays the exact same economic role for the vesting contract's shareholders: `total_accumulated_rewards` and `accumulated_rewards` are computed directly from `staking_contract::staking_contract_amounts`, which subtracts `commission_amount` computed from `staking_contract.commission_percentage`. [4](#0-3) 
Shareholders in a `VestingContract` are structurally analogous to delegators in a `DelegationPool` - both are passive value-holders whose reward share is determined by a commission percentage they do not control. Yet the commission-change guard that protects delegators is entirely absent from the path shareholders depend on: the vesting `admin` can call `update_operator` at any time, with any `commission_percentage` up to 100, redirecting the vesting contract's operator (and hence the flow of all future accumulated rewards) instantly, with zero delay and zero increase cap. [5](#0-4) 

### Impact Explanation
A vesting `admin` who is compromised, colludes with an operator, or is simply hostile can call `update_operator(admin, contract_address, colluding_operator, 100)` immediately before a lockup cycle ends and rewards become withdrawable, capturing up to 100% of `accumulated_rewards` for all shareholders in that vesting contract via `staking_contract::request_commission` → `distribute`. This corrupts the commission accounting to credit the wrong account (the colluding operator instead of the shareholders' `remaining_grant`/reward share), matching the "Operator commission ... corruption that credits the wrong account" impact class. Because there is no `MAX_COMMISSION_INCREASE`-style cap, the jump can go from 0% to 100% in a single call, and because there is no lockup-timing gate, it can be timed to land immediately before rewards vest, maximizing extraction exactly as `delegation_pool`'s guard was designed to prevent.

### Likelihood Explanation
`admin` is a role already recognized in the codebase as needing bounding on its unilateral power - the module already restricts other admin actions (e.g., `set_beneficiary` requires the new beneficiary to be registered for APT to avoid griefing distribution) and documents that shareholders rely on staking rewards flowing correctly. No governance vote, quorum, or delay is required for `update_operator`; it is a single admin-signed entry function callable at any time. The economic incentive (capturing 100% of accrued rewards from all shareholders in one call) is straightforward, and unlike the `delegation_pool` case there are no on-chain guardrails at all to slow or cap it.

### Recommendation
Apply the same protections `delegation_pool::update_commission_percentage` uses to `staking_contract::switch_operator`/`update_operator`:
1. Cap the per-call commission increase (e.g., via a `MAX_COMMISSION_INCREASE`-equivalent constant) relative to the staking contract's current `commission_percentage`.
2. Reject the change if the underlying stake pool's remaining lockup is below a minimum threshold, mirroring `ETOO_LATE_COMMISSION_CHANGE`.
3. Alternatively (or additionally), have `vesting::update_operator` apply commission changes only "effective after next lockup cycle" the way `delegation_pool` defers via `NextCommissionPercentage`, rather than applying it synchronously to the live `StakingContract`.

### Proof of Concept
Conceptual sequence (not executed against the indexed code - would need to be validated in a Move test harness):
1. Create a vesting contract with shareholders and `admin`; operator1 has `commission_percentage = 0`. `update_operator(admin, contract_address, operator1, 0)`. [6](#0-5) 
2. Let the stake pool accumulate significant rewards via `stake::end_epoch()` calls while commission stays at 0%, so `remaining_grant`/`accumulated_rewards` grow.
3. Immediately before the lockup expires (no timing restriction exists), admin calls `update_operator(admin, contract_address, colluding_operator, 100)`, instantly raising commission to 100% with no per-cycle cap check (contrast with `delegation_pool::update_commission_percentage`, which would revert here under `ETOO_LARGE_COMMISSION_INCREASE`/`ETOO_LATE_COMMISSION_CHANGE`). [2](#0-1) 
4. `staking_contract::request_commission`/`distribute` then pays the entire accumulated reward pool to `colluding_operator`, leaving shareholders with none of the rewards they were owed. [7](#0-6) 

**Caveat:** I was unable to locate the exact source line defining `MAX_COMMISSION_INCREASE` and `min_remaining_secs_for_commission_change` in `delegation_pool.move` within the indexed content (grep confirmed their existence in that file but line ranges were not returned before the tool budget was exhausted), so I cannot cite their exact numeric values. This does not affect the core finding - the absence of any equivalent check in `staking_contract::switch_operator` is directly confirmed in the code shown above - but a full verification of the specific cap/timing constants would benefit from a direct file read in a follow-up session.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1293-1308)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L603-674)
```text
    /// Unlock commission amount from the stake pool. Operator needs to wait for the amount to become withdrawable
    /// at the end of the stake pool's lockup period before they can actually can withdraw_commission.
    ///
    /// Only staker, operator or beneficiary can call this.
    public entry fun request_commission(
        account: &signer, staker: address, operator: address
    ) acquires Store, BeneficiaryForOperator {
        let account_addr = signer::address_of(account);
        assert!(
            account_addr == staker
                || account_addr == operator
                || account_addr == beneficiary_for_operator(operator),
            error::unauthenticated(ENOT_STAKER_OR_OPERATOR_OR_BENEFICIARY)
        );
        assert_staking_contract_exists(staker, operator);

        let store = borrow_global_mut<Store>(staker);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);
        // Short-circuit if zero commission.
        if (staking_contract.commission_percentage == 0) { return };

        // Force distribution of any already inactive stake.
        distribute_internal(
            staker,
            operator,
            staking_contract,
        );

        request_commission_internal(
            operator,
            staking_contract,
        );
    }

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L761-805)
```text
    /// Allows staker to switch operator without going through the lenghthy process to unstake.
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L451-458)
```text
    public fun total_accumulated_rewards(vesting_contract_address: address): u64 acquires VestingContract {
        assert_active_vesting_contract(vesting_contract_address);

        let vesting_contract = borrow_global<VestingContract>(vesting_contract_address);
        let (total_active_stake, _, commission_amount) =
            staking_contract::staking_contract_amounts(vesting_contract_address, vesting_contract.staking.operator);
        total_active_stake - vesting_contract.remaining_grant - commission_amount
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L823-841)
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
```
