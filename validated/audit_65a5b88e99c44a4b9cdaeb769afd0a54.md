**No vulnerability found for this question.**

Findings from investigation:

1. **`vesting::update_operator` requires the vesting contract admin's signer.** It calls `verify_admin(admin, vesting_contract)` (referenced throughout `vesting.move`, e.g. in `admin_withdraw` and `update_voter`) before mutating operator state [1](#0-0) . There is no public wrapper that allows an unprivileged, non-admin account to invoke it — `staking_proxy::set_vesting_contract_operator` also requires the `owner` signer and only iterates the owner's own vesting contracts [2](#0-1) .

2. **The underlying `staking_contract::switch_operator` explicitly settles outstanding commission for the old operator before migrating the pool**, rather than stranding it. It force-distributes any inactive stake and calls `request_commission_internal` for the *old_operator* prior to changing `stake::set_operator_with_cap` and inserting the new `staking_contract` entry keyed by `new_operator`: [3](#0-2) . This means any commission accrued while the old operator was active is queued for payout to that operator (or their beneficiary), not lost or reattributed to the new operator.

3. **The unit tests already validate exactly the scenario described in the proof idea** (switching operator twice and confirming the old operator/beneficiary can still receive commission accrued while active), and they pass — the old operator's beneficiary receives the expected commission after `update_operator`/`switch_operator`, while the new operator's rewards are separately commissioned: [4](#0-3) , and the corresponding `staking_contract.move` test asserts the pending distribution to `operator_1` is preserved and unlocked from the stake pool after `switch_operator` [5](#0-4) .

Because (a) the entrypoint is gated by the admin/staker signer rather than reachable by an unprivileged account, and (b) the accounting invariant already forces commission distribution/reattribution to the correct (old) operator before the migration, this does not meet the Decision Standard requiring an unprivileged-input path that stops accrued commission from being recoverable by its rightful operator.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L797-806)
```text
    public entry fun admin_withdraw(admin: &signer, contract_address: address) acquires VestingContract {
        let vesting_contract = borrow_global<VestingContract>(contract_address);
        assert!(
            vesting_contract.state == VESTING_POOL_TERMINATED,
            error::invalid_state(EVESTING_CONTRACT_STILL_ACTIVE)
        );

        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        verify_admin(admin, vesting_contract);
        let coins = withdraw_stake(vesting_contract, contract_address);
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L1690-1713)
```text
        let old_beneficiay_balance = coin::balance<AptosCoin>(beneficiary_address);

        // switch operator to operator2. The rewards should go to operator2 not to the beneficiay of operator1.
        update_operator(admin, contract_address, operator_address2, 10);

        stake::end_epoch();
        let (_, accumulated_rewards, _) = staking_contract::staking_contract_amounts(contract_address,
            operator_address2
        );

        let expected_commission = accumulated_rewards / 10;

        // Request commission.
        staking_contract::request_commission(operator2, contract_address, operator_address2);
        // Unlocks the commission.
        stake::fast_forward_to_unlock(stake_pool_address);
        expected_commission = with_rewards(expected_commission);

        // Distribute the commission to the operator.
        distribute(contract_address);

        // Assert that the rewards go to operator2, and the balance of the operator1's beneficiay remains the same.
        assert!(coin::balance<AptosCoin>(operator_address2) >= expected_commission, 1);
        assert!(coin::balance<AptosCoin>(beneficiary_address) == old_beneficiay_balance, 1);
```

**File:** aptos-move/framework/aptos-framework/sources/staking_proxy.move (L31-41)
```text
    public entry fun set_vesting_contract_operator(owner: &signer, old_operator: address, new_operator: address) {
        let owner_address = signer::address_of(owner);
        let vesting_contracts = &vesting::vesting_contracts(owner_address);
        vesting_contracts.for_each_ref(|vesting_contract| {
            let vesting_contract = *vesting_contract;
            if (vesting::operator(vesting_contract) == old_operator) {
                let current_commission_percentage = vesting::operator_commission_percentage(vesting_contract);
                vesting::update_operator(owner, vesting_contract, new_operator, current_commission_percentage);
            };
        });
    }
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1561-1590)
```text
        // Switch operators.
        switch_operator(
            staker,
            operator_1_address,
            operator_2_address,
            20
        );
        // The staking_contract is now associated with operator 2 but there should be a pending distribution of unpaid
        // commission to operator 1.
        let new_balance = with_rewards(INITIAL_BALANCE);
        let commission_for_operator_1 = (new_balance - INITIAL_BALANCE) / 10;
        assert_distribution(
            staker_address,
            operator_2_address,
            operator_1_address,
            commission_for_operator_1
        );
        // Unpaid commission should be unlocked from the stake pool.
        new_balance -= commission_for_operator_1;
        stake::assert_stake_pool(
            pool_address,
            new_balance,
            0,
            0,
            commission_for_operator_1
        );
        assert!(
            last_recorded_principal(staker_address, operator_2_address) == new_balance,
            0
        );
```
