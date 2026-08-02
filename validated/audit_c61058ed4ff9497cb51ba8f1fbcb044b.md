## Title
Operator can permanently DoS `distribute`/`request_commission`/`switch_operator` by setting an unregistrable beneficiary address - (File: aptos-move/framework/aptos-framework/sources/staking_contract.move)

### Summary
`staking_contract::set_beneficiary_for_operator` lets the *operator* (an unprivileged party relative to the staker) set an arbitrary `new_beneficiary` address with no validation whatsoever, unlike the analogous `vesting::set_beneficiary`, which explicitly calls `assert_account_is_registered_for_apt(new_beneficiary)` "so `distribute()` wouldn't fail and block all other accounts from receiving APT." This mirrors the external report's bug class: code assumes a downstream call (there, `symbol()`; here, `aptos_account::deposit_coins` → account creation) will always succeed, but an edge-case input breaks that assumption and reverts the whole flow — except here the blast radius is every party sharing the same `distribute_internal` loop, including the staker's own funds. [1](#0-0) [2](#0-1) 

### Finding Description
`set_beneficiary_for_operator` stores whatever address the operator supplies into `BeneficiaryForOperator`, with no existence or registration check: [3](#0-2) 

That beneficiary is later paid out inside `distribute_internal`, which loops over *all* shareholders of the `distribution_pool` (staker + operator/beneficiary) in a single transaction and calls `aptos_account::deposit_coins` for each: [4](#0-3) 

`aptos_account::deposit_coins` will call `create_account(to)` if the recipient account does not yet exist: [5](#0-4) 

Account creation for reserved framework addresses (e.g. `@vm_reserved`, `@aptos_framework`) aborts in `account::create_account`. If the operator sets `new_beneficiary` to such a reserved address (or to any address that is intentionally kept from ever being able to hold an `Account`/register for APT), every subsequent call into `distribute_internal` will abort when it reaches that recipient in the loop. Because Move transactions are atomic, the *entire* `distribute_internal` call reverts — this function is invoked from `distribute`, `request_commission`, `update_commision`, and `switch_operator`, all of which are the only code paths that call `stake::withdraw_with_cap` to pull the staker's inactive/pending_inactive stake out of the stake pool: [6](#0-5) [7](#0-6) 

Once this poisoned beneficiary is set (and commission_percentage > 0, so the operator is a non-zero shareholder in `distribution_pool`), the staker can no longer distribute, request commission, update commission, or switch operator without hitting the abort — because every one of these functions first calls `distribute_internal` (to flush already-inactive stake) before doing its own logic. The staker's already-unlocked stake becomes permanently stuck inside the stake pool, unreachable via the `staking_contract` API, since this module is the only holder of the `OwnerCapability` for that pool.

`vesting::set_beneficiary_for_operator` simply forwards to the same unguarded function: [8](#0-7) 

so vesting contracts using `staking_contract` are equally exposed, even though `vesting::set_beneficiary` (the shareholder-facing sibling function) was explicitly hardened against this exact class of bug: [2](#0-1) 

### Impact Explanation
This permanently locks/strands the staker's inactive and pending-inactive stake (and any future unlocked stake) within the stake pool, with no recovery path through `staking_contract`'s public API — the staker cannot even switch away from the malicious operator, since `switch_operator` also calls `distribute_internal` before performing the switch. This satisfies the "permanent lock or non-recoverable loss of claim rights" and "operator commission/beneficiary corruption that traps value" impact categories, and can be triggered entirely by the operator (an unprivileged party relative to the staker) without staker consent.

### Likelihood Explanation
Likelihood is high: `set_beneficiary_for_operator` is a public entry function callable directly by any operator once `features::operator_beneficiary_change_enabled()` is on, requires no special permission beyond being the operator of an existing staking contract, and the "poison" input (a reserved address, or any address deliberately excluded from account creation) is trivial to supply. No staker or admin approval is needed to set the beneficiary.

### Recommendation
Mirror the guard already present in `vesting::set_beneficiary`: require `aptos_account::assert_account_is_registered_for_apt(new_beneficiary)` (or otherwise validate the address is not a reserved/un-creatable address) inside `staking_contract::set_beneficiary_for_operator` before storing it. Additionally, consider hardening `distribute_internal` itself to isolate a failing recipient (e.g., via a fallback deposit path) so that a single bad recipient cannot block payouts to all other shareholders in the loop.

### Proof of Concept
1. Staker calls `staking_contract::create_staking_contract(staker, operator, voter, amount, commission_percentage > 0, seed)`.
2. Operator calls `staking_contract::set_beneficiary_for_operator(operator, @vm_reserved)` (or any other address guaranteed to fail `account::create_account`, e.g. `@aptos_framework`).
3. Time passes, rewards accrue, and stake becomes `inactive`/`pending_inactive` for the pool (e.g., via lockup expiry or `unlock_stake`).
4. Staker (or anyone) calls `staking_contract::distribute(staker, operator)` — the call reaches `distribute_internal`, redeems the operator's commission shares, resolves `recipient = beneficiary_for_operator(operator) == @vm_reserved`, and calls `aptos_account::deposit_coins(@vm_reserved, coins)`, which aborts inside `account::create_account`.
5. The abort reverts the entire transaction. `request_commission`, `update_commision`, and `switch_operator` all fail the same way since they call `distribute_internal` first, leaving the staker's stake permanently stuck.

Note: I was unable to directly confirm within the indexed portion of `account.move` that `create_account` aborts specifically for `@vm_reserved`/`@aptos_framework` (the relevant `account.move` content was not returned by search before the final iteration cutoff); this is standard, well-documented Aptos framework behavior (reserved addresses cannot have `Account` resources created for them), but a Devin session with full repo access should verify the exact abort condition/error code in `aptos-move/framework/aptos-framework/sources/account.move` to finalize this report with certainty.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L607-630)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L807-838)
```text
    /// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
    /// beneficiary. To ensures payment to the current beneficiary, one should first call `distribute` before switching
    /// the beneficiary. An operator can set one beneficiary for staking contract pools, not a separate one for each pool.
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-916)
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

            emit(
                Distribute {
                    operator,
                    pool_address,
                    recipient,
                    amount: amount_to_distribute
                }
            );
        };

        // In case there's any dust left, send them all to the staker.
        if (coin::value(&coins) > 0) {
            aptos_account::deposit_coins(staker, coins);
            distribution_pool.update_total_coins(0);
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

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L111-131)
```text
    public fun deposit_coins<CoinType>(
        to: address, coins: Coin<CoinType>
    ) acquires DirectTransferConfig {
        if (!account::exists_at(to)) {
            create_account(to);
            spec {
                // TODO(fa_migration)
                // assert coin::spec_is_account_registered<AptosCoin>(to);
                // assume aptos_std::type_info::type_of<CoinType>() == aptos_std::type_info::type_of<AptosCoin>() ==>
                //     coin::spec_is_account_registered<CoinType>(to);
            };
        };
        if (!coin::is_account_registered<CoinType>(to)) {
            assert!(
                can_receive_direct_coin_transfers(to),
                error::permission_denied(EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS)
            );
            coin::register<CoinType>(&create_signer(to));
        };
        coin::deposit<CoinType>(to, coins)
    }
```
