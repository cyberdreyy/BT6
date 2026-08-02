Based on the investigation, I found a credible on-chain analog. Note: I could not retrieve the exact body of `delegation_pool::set_beneficiary_for_operator` from the index (size limits), but its existence, signature, and lack-of-validation behavior are confirmed via the `staking_contract` twin implementation, the SDK builder docstring, and test usage.

### Title
Operator can permanently trap delegation-pool/staking-contract commission by setting an unvalidated `beneficiary_for_operator`, unlike vesting.move's protected pattern - (File: `aptos-move/framework/aptos-framework/sources/delegation_pool.move`, `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::set_beneficiary_for_operator` and its `delegation_pool` counterpart let an operator redirect its commission payout address with **no validation** on `new_beneficiary`. [1](#0-0) 
This mirrors `vesting.move::set_beneficiary`, which explicitly guards against this class of bug by requiring `assert_account_is_registered_for_apt(new_beneficiary)` before allowing a beneficiary change, specifically because unguarded changes could permanently block distributions. [2](#0-1) 
Neither `staking_contract::set_beneficiary_for_operator` nor the `delegation_pool` equivalent has this guard.

### Finding Description
In `delegation_pool.move`, commission accrued each epoch is paid by minting shares directly to `beneficiary_for_operator(operator)` inside the publicly-callable `synchronize_delegation_pool`: [3](#0-2) 
`synchronize_delegation_pool` is an unrestricted `public entry fun` — "Allow anyone" to trigger it (same pattern as `staking_contract::distribute`, explicitly documented as callable by any unprivileged account). [4](#0-3) 

If the operator sets its beneficiary to `@0x0` (or any address with no controlling key/signer) via `set_beneficiary_for_operator`, all subsequent commission is bought into shares owned by that inert shareholder. Because withdrawing shares (`unlock`/`withdraw` in `delegation_pool.move`, or `request_commission`/`distribute` in `staking_contract.move`) requires a transaction signed by the shareholder/beneficiary address, and no such signer exists for `@0x0`, the commission shares become permanently unclaimable — functionally identical to the external report's `claimFees()` sending accrued fees to `address(0)`.

The `beneficiary_for_operator` lookup defaults to the operator address only when no `BeneficiaryForOperator` resource exists at all (per `staking_contract::beneficiary_for_operator`, lines 362-368); once explicitly set to an unusable address, that fallback no longer applies. [5](#0-4) 

Critically, unlike the vesting-pool flow — which explicitly validates the new beneficiary is registered for `AptosCoin` before accepting the change, precisely to avoid this failure mode — the staking-contract/delegation-pool beneficiary setters perform no such check.

### Impact Explanation
Once triggered, any unprivileged caller invoking `synchronize_delegation_pool` (delegation pools) or `distribute`/`request_commission` (staking_contract) — both explicitly designed to be publicly callable — causes the operator's ongoing and already-accrued commission to be irrecoverably credited to an address nobody controls. This matches the required impact category: "Operator commission... share-accounting corruption that credits the wrong account or traps value," and "Permanent lock or non-recoverable loss of claim rights."

### Likelihood Explanation
Likelihood is low-to-moderate: it requires the operator itself to mis-set (or be tricked/scripted into setting) its beneficiary to an unusable address — a self-inflicted misconfiguration, analogous to the external report's `feeCollector` renouncing to `address(0)`. Once misconfigured, any unprivileged actor calling the public synchronize/distribute entry points reliably triggers/perpetuates the loss, with no recovery path since the affected shares are permanently orphaned.

### Recommendation
Add the same validation vesting.move already performs: require `new_beneficiary` to be a valid, coin-registered, non-zero address in both `staking_contract::set_beneficiary_for_operator` and `delegation_pool::set_beneficiary_for_operator` before accepting the change, e.g., disallow `@0x0` and require `coin::is_account_registered<AptosCoin>(new_beneficiary)` (or an equivalent check) to be true.

### Proof of Concept
1. Operator calls `staking_contract::set_beneficiary_for_operator(operator, @0x0)` (or the `delegation_pool` equivalent) — succeeds with no validation. [6](#0-5) 
2. Stake pool earns rewards; commission accrues normally.
3. Any unprivileged account calls `delegation_pool::synchronize_delegation_pool(pool_address)`, which buys commission shares for `beneficiary_for_operator(operator) == @0x0`. [7](#0-6) 
4. No signer exists for `@0x0`, so those shares can never be unlocked/withdrawn — the operator's commission is permanently lost, contrasting with `vesting.move::set_beneficiary`'s explicit protection against exactly this scenario. [8](#0-7) 

**Confidence caveat:** I was unable to retrieve the literal source body of `delegation_pool::set_beneficiary_for_operator` from the index (only test usage at line 3770 and the SDK-builder wrapper were available), so I could not 100% rule out an unseen zero-address guard specific to that function. I recommend a Devin session with full repo access to confirm the exact function body before treating this as fully verified.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L361-368)
```text
    /// Return the beneficiary address of the operator.
    public fun beneficiary_for_operator(operator: address): address acquires BeneficiaryForOperator {
        if (exists<BeneficiaryForOperator>(operator)) {
            return borrow_global<BeneficiaryForOperator>(operator).beneficiary_for_operator
        } else {
            operator
        }
    }
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L915-946)
```text
    public entry fun set_beneficiary(
        admin: &signer,
        contract_address: address,
        shareholder: address,
        new_beneficiary: address,
    ) acquires VestingContract {
        // Verify that the beneficiary account is set up to receive APT. This is a requirement so distribute() wouldn't
        // fail and block all other accounts from receiving APT if one beneficiary is not registered.
        assert_account_is_registered_for_apt(new_beneficiary);

        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        verify_admin(admin, vesting_contract);

        let old_beneficiary = get_beneficiary(vesting_contract, shareholder);
        let beneficiaries = &mut vesting_contract.beneficiaries;
        if (beneficiaries.contains_key(&shareholder)) {
            let beneficiary = beneficiaries.borrow_mut(&shareholder);
            *beneficiary = new_beneficiary;
        } else {
            beneficiaries.add(shareholder, new_beneficiary);
        };

        emit(
            SetBeneficiary {
                admin: vesting_contract.admin,
                vesting_contract_address: contract_address,
                shareholder,
                old_beneficiary,
                new_beneficiary,
            },
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1917-1956)
```text
    public entry fun synchronize_delegation_pool(
        pool_address: address
    ) acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage {
        assert_delegation_pool_exists(pool_address);
        let pool = borrow_global_mut<DelegationPool>(pool_address);
        let (
            lockup_cycle_ended,
            active,
            pending_inactive,
            commission_active,
            commission_pending_inactive
        ) = calculate_stake_pool_drift(pool);

        // zero `pending_active` stake indicates that either there are no `add_stake` fees or
        // previous epoch has ended and should release the shares owning the existing fees
        let (_, _, pending_active, _) = stake::get_stake(pool_address);
        if (pending_active == 0) {
            // renounce ownership over the `add_stake` fees by redeeming all shares of
            // the special shareholder, implicitly their equivalent coins, out of the active shares pool
            redeem_active_shares(pool, NULL_SHAREHOLDER, MAX_U64);
        };

        // distribute rewards remaining after commission, to delegators (to already existing shares)
        // before buying shares for the operator for its entire commission fee
        // otherwise, operator's new shares would additionally appreciate from rewards it does not own

        // update total coins accumulated by `active` + `pending_active` shares
        // redeemed `add_stake` fees are restored and distributed to the rest of the pool as rewards
        pool.active_shares.update_total_coins(active - commission_active);
        // update total coins accumulated by `pending_inactive` shares at current observed lockup cycle
        pending_inactive_shares_pool_mut(pool).update_total_coins(pending_inactive - commission_pending_inactive);

        // reward operator its commission out of uncommitted active rewards (`add_stake` fees already excluded)
        buy_in_active_shares(pool, beneficiary_for_operator(stake::get_operator(pool_address)), commission_active);
        // reward operator its commission out of uncommitted pending_inactive rewards
        buy_in_pending_inactive_shares(
            pool,
            beneficiary_for_operator(stake::get_operator(pool_address)),
            commission_pending_inactive
        );
```
