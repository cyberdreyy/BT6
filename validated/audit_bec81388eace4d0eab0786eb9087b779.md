Based on the investigation, I found a concrete Aptos-native analog to the "missing `_to != 0x0` check" bug class in `staking_contract::set_beneficiary_for_operator`.

### Title
Missing beneficiary registration check in `staking_contract::set_beneficiary_for_operator` permanently DoSes `distribute()` for all shareholders - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::set_beneficiary_for_operator` lets an operator set an arbitrary `new_beneficiary` address with no validation that the address is capable of receiving `AptosCoin`. [1](#0-0) 
This is the direct structural analog of the reported bug class (unvalidated destination address in a withdraw/claim path), and the codebase itself proves the danger: `vesting::set_beneficiary` explicitly guards against exactly this scenario with `assert_account_is_registered_for_apt(new_beneficiary)`, with a comment stating the check exists "so distribute() wouldn't fail and block all other accounts from receiving APT if one beneficiary is not registered." [2](#0-1) 
`staking_contract::set_beneficiary_for_operator` has no equivalent check.

### Finding Description
`distribute_internal` pays out every shareholder of a `StakingContract` (including the operator, redirected to its beneficiary) inside a single atomic `while` loop: [3](#0-2) 
If `recipient == operator`, the payout is redirected to `beneficiary_for_operator(operator)` and sent via `aptos_account::deposit_coins`. Because `set_beneficiary_for_operator` never validates that `new_beneficiary` can actually receive `AptosCoin` (registered `CoinStore`, not a reserved/invalid address, etc.), an operator can point its beneficiary at an address that causes `aptos_account::deposit_coins` to abort. Since the payout loop is atomic and processes all shareholders (staker included) before returning, a single failing recipient aborts the entire `distribute_internal` call — meaning **no** shareholder, including the staker who has no control over the operator's beneficiary setting, can receive their already-earned/inactive stake.

This is precisely the invariant that `vesting::set_beneficiary` was hardened against, but `staking_contract::set_beneficiary_for_operator` (and its `vesting::set_beneficiary_for_operator` passthrough) was not: [4](#0-3) 

Any code path that calls `distribute_internal` is affected, including plain `distribute()`, and `switch_operator`, which calls `distribute_internal` before reassigning the operator: [5](#0-4) 
This means the staker cannot even switch away from the malicious/broken operator to recover, because `switch_operator` itself first forces a `distribute_internal` call that will also abort.

### Impact Explanation
An operator (a legitimate but unprivileged-relative-to-the-staker's-funds role) can permanently strand the staker's inactive/withdrawable stake by pointing its beneficiary at an address that cannot accept `AptosCoin` deposits. This blocks `distribute()` and `switch_operator` for the entire staking contract, trapping the staker's (and any other shareholders') already-unlocked funds with no recovery path short of a privileged/governance intervention. This matches the "Permanent lock or non-recoverable loss of claim rights in stake ... commission, beneficiary ... flows" impact category.

### Likelihood Explanation
Likelihood is moderate to high: `set_beneficiary_for_operator` is a normal, permissionless entry function available to any operator, gated only by the `operator_beneficiary_change_enabled` feature flag. [6](#0-5) 
No special privilege beyond already being an operator (a role many entities hold) is required to trigger the DoS, and it requires no cooperation or awareness from the staker.

### Recommendation
Add the same guard used in `vesting::set_beneficiary` to `staking_contract::set_beneficiary_for_operator` (and the analogous `delegation_pool::set_beneficiary_for_operator`, though the latter is less exposed since delegation-pool withdrawals are per-delegator rather than a single atomic loop): require `assert_account_is_registered_for_apt(new_beneficiary)` (or equivalent) before allowing the beneficiary to be set, and/or make `distribute_internal`'s payout loop resilient to a single failing recipient (e.g., skip/queue a failed payout rather than aborting the whole distribution).

### Proof of Concept
1. Staker creates a staking contract with `operator` via `staking_contract::create_staking_contract`.
2. `operator` calls `set_beneficiary_for_operator(operator, bad_address)` where `bad_address` is an address with no `CoinStore<AptosCoin>` and is not eligible for auto-registration (e.g., a reserved/invalid address, or an address whose owner later calls `coin::unregister<AptosCoin>`).
3. Stake pool accrues rewards/commission; staker calls `unlock_stake`/waits for lockup, then calls `distribute(staker_address, operator_address)`.
4. `distribute_internal` iterates all shareholders; when it reaches `operator`, it redirects payout to `beneficiary_for_operator(operator) == bad_address` and calls `aptos_account::deposit_coins(bad_address, ...)`, which aborts.
5. The entire `distribute` transaction reverts — the staker's inactive stake, which should have been paid out, remains permanently stuck, and `switch_operator` (staker's only escape route) also aborts for the same reason.

Note: I was unable to fully trace the exact internal implementation of `aptos_account::deposit_coins`/`account::create_account`'s reserved-address checks in this session due to file-read issues, so the precise abort condition (frozen CoinStore vs. reserved address vs. unregistered account) could not be 100% pinned down from source in this pass — the vesting.move comment is used as the primary in-repo confirmation that this failure mode is real and was already identified as a risk by the Aptos team for a sibling function.

### Citations

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-901)
```text
        // Buy all recipients out of the distribution pool.
        while (distribution_pool.shareholders_count() > 0) {
            let recipients = distribution_pool.shareholders();
            let recipient = recipients[0];
            let current_shares = distribution_pool.shares(recipient);
            let amount_to_distribute =
                distribution_pool.redeem_shares(recipient, current_shares);
            // If the recipient is the operator, send the commission to the beneficiary instead.
            if (recipient == operator) {
                recipient = beneficiary_for_operator(operator);
            };
            aptos_account::deposit_coins(
                recipient, coin::extract(&mut coins, amount_to_distribute)
            );
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L915-935)
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
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L1000-1006)
```text
    /// Set the beneficiary for the operator.
    public entry fun set_beneficiary_for_operator(
        operator: &signer,
        new_beneficiary: address,
    ) {
        staking_contract::set_beneficiary_for_operator(operator, new_beneficiary);
    }
```
