## Finding: Staking Contract Commission Distribution Can Be Permanently Blocked, Locking Staker Funds

The external ZetaChain report is about a missing lower-bound check that let a single malformed transaction get permanently stuck, blocking all subsequent transactions in the same queue (nonce). The Aptos analog is a missing **recipient-registration check** in `staking_contract::set_beneficiary_for_operator`, which lets an operator brick the shared distribution path used by `distribute()`, blocking withdrawal of the *staker's own* funds indefinitely.

### Title
Missing beneficiary-registration check in `staking_contract::set_beneficiary_for_operator` permanently blocks commission/stake distribution - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::distribute_internal` pays out **every** shareholder (staker + operator/beneficiary) of a pool in a single atomic loop using `aptos_account::deposit_coins`. If any single recipient cannot accept the deposit, the whole function aborts, reverting the withdrawal for *all* other shareholders as well. `vesting.move`'s `set_beneficiary` was hardened against exactly this scenario with an explicit `assert_account_is_registered_for_apt(new_beneficiary)` check [1](#0-0) , but the analogous `staking_contract::set_beneficiary_for_operator` has no such check [2](#0-1) .

### Finding Description
`distribute_internal` withdraws all withdrawable stake pool coins in one shot and then loops over every shareholder in the pool's `distribution_pool`, calling `aptos_account::deposit_coins` for each one, including redirecting the operator's payout to `beneficiary_for_operator(operator)`: [3](#0-2) 

`aptos_account::deposit_coins` only auto-registers a recipient for `CoinType` if `can_receive_direct_coin_transfers` returns true; otherwise it aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`: [4](#0-3) 

An operator fully controls their own beneficiary address via `set_beneficiary_for_operator`, and this function performs no registration/eligibility check on `new_beneficiary` before persisting it: [5](#0-4) 

Because `distribute_internal` is called from every path that moves value in a staking contract — `distribute`, `request_commission`, `update_commision`, and `switch_operator`/`switch_operator_with_same_commission` — all of them abort as soon as the beneficiary payout fails: [6](#0-5) [7](#0-6) [8](#0-7) 

Aborts in Move revert all state changes, so the withdrawal from the stake pool (`stake::withdraw_with_cap`) is undone every time and the funds remain forever stuck as `inactive`/`pending_inactive` in the underlying stake pool, unreachable through any code path, since `distribute()` is "open to anyone" but always fails the same way: [9](#0-8) 

The comment on `vesting::set_beneficiary` explicitly documents the exact concern that is unaddressed here: *"Verify that the beneficiary account is set up to receive APT. This is a requirement so distribute() wouldn't fail and block all other accounts from receiving APT if one beneficiary is not registered."* [1](#0-0)  — this fix was never applied to `staking_contract::set_beneficiary_for_operator`, which any operator can call unprivileged for their own role.

### Impact Explanation
An operator (an unprivileged party who does not already control the staker's stake) can:
1. Point their beneficiary at an address that has disabled `allow_arbitrary_coin_transfers` (via `aptos_account::set_allow_direct_coin_transfers(false)`) and is not already registered for `AptosCoin`.
2. This permanently disables `distribute()`, `request_commission()`, `update_commision()`, and `switch_operator()` for that staker-operator pair.
3. The staker's own principal and rewards, once unlocked and inactive on the stake pool, can never be withdrawn to the staker's account — they are permanently stranded, since the only code path that releases funds (`distribute_internal`) always reverts.

This matches the "Stake And Lockup Gate" criteria: permanent, non-recoverable loss of claim rights over stake/commission balances not owned by the attacker (the staker's funds), caused entirely by an unprivileged operator action.

### Likelihood Explanation
Likelihood is high: any operator in any staking contract can trigger this unilaterally with two ordinary, permissionless transactions (disable direct transfers on a controlled/fresh account, then call `set_beneficiary_for_operator`). No governance or privileged role is required, and there is no recovery path once triggered — the staker cannot bypass `distribute_internal` to reclaim their own funds.

### Recommendation
Add the same guard used in `vesting::set_beneficiary` to `staking_contract::set_beneficiary_for_operator`, requiring `new_beneficiary` to already be registered/able to receive `AptosCoin` (e.g., `aptos_account::assert_account_is_registered_for_apt(new_beneficiary)`) before persisting the change. Additionally, consider making `distribute_internal` resilient to a single failing recipient (e.g., by paying other shareholders first and separately handling/skip-and-retry for a stuck beneficiary) so that one bad recipient cannot block the entire pool's distribution.

### Proof of Concept
1. Staker creates a staking contract with `operator` and some commission percentage via `create_staking_contract`.
2. `operator` creates/controls an address `B` that either never registers for `AptosCoin` or is a fresh resource-style account, and calls `aptos_account::set_allow_direct_coin_transfers(false)` for `B` (or simply leaves a freshly generated account unregistered and not opted-in).
3. `operator` calls `staking_contract::set_beneficiary_for_operator(operator, B)` — succeeds with no validation.
4. Stake pool accrues rewards; staker calls `unlock_stake`/waits for lockup to expire so funds become inactive.
5. Any party calls `staking_contract::distribute(staker, operator)` (or `request_commission`, `update_commision`, `switch_operator`). `distribute_internal` withdraws the inactive coins, then in its shareholder loop tries `aptos_account::deposit_coins(B, ...)` for the operator's commission share, which aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
6. The entire transaction reverts; the staker's portion of the inactive stake is never paid out, and every future call to any of these entry functions fails identically, permanently.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L915-924)
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

```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L564-601)
```text
    /// Convenience function to allow a staker to update the commission percentage paid to the operator.
    /// TODO: fix the typo in function name. commision -> commission
    public entry fun update_commision(
        staker: &signer, operator: address, new_commission_percentage: u64
    ) acquires Store, BeneficiaryForOperator {
        assert!(
            new_commission_percentage >= 0 && new_commission_percentage <= 100,
            error::invalid_argument(EINVALID_COMMISSION_PERCENTAGE)
        );

        let staker_address = signer::address_of(staker);
        assert!(
            exists<Store>(staker_address),
            error::not_found(ENO_STAKING_CONTRACT_FOUND_FOR_STAKER)
        );

        let store = borrow_global_mut<Store>(staker_address);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);
        distribute_internal(
            staker_address,
            operator,
            staking_contract,
        );
        request_commission_internal(
            operator,
            staking_contract,
        );
        let old_commission_percentage = staking_contract.commission_percentage;
        staking_contract.commission_percentage = new_commission_percentage;
        emit(
            UpdateCommission {
                staker: staker_address,
                operator,
                old_commission_percentage,
                new_commission_percentage
            }
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L607-629)
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-900)
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
