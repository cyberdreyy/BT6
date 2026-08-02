### Title
Operator can permanently trap staker's unlock/withdraw funds in `staking_contract` by making commission deposit un-payable - ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Summary
`staking_contract::distribute_internal` pays out every shareholder (staker and operator) in a single un-guarded loop using `aptos_account::deposit_coins`. `deposit_coins` aborts if the recipient's `CoinStore` is frozen, or if the recipient is unregistered for `AptosCoin` and has explicitly disabled `allow_arbitrary_coin_transfers` via `aptos_account::set_allow_direct_coin_transfers(false)`. Since `distribute_internal` is invoked at the very start of `unlock_stake`, `switch_operator`, and `request_commission` (to flush any already-inactive stake before doing the requested operation), an operator who makes their own commission payout revert can permanently block the staker from unlocking, withdrawing, or switching away from them — this is the same failure class as the reported ETH-bridge bug (a single failing leg of a distribution aborts the whole transaction and strands other parties' funds), but here it applies to Aptos stake rather than bridged ETH. [1](#0-0) 

### Finding Description
`distribute_internal` iterates `distribution_pool.shareholders()` and calls `aptos_account::deposit_coins(recipient, ...)` for each recipient, redirecting the operator's commission share to `beneficiary_for_operator(operator)`: [2](#0-1) 

`aptos_account::deposit_coins` aborts if the target's `CoinStore` is frozen, or — for an unregistered target — if `can_receive_direct_coin_transfers` returns false (i.e., the account explicitly disabled arbitrary transfers): [3](#0-2) 

Every stake-mutating entry function for a staking contract calls `distribute_internal` first, unconditionally, before performing its own logic:
- `unlock_stake` (staker withdrawal path) calls `distribute_internal` then `request_commission_internal` then does the withdrawal.
- `switch_operator` calls `distribute_internal`/`request_commission_internal` before re-pointing the pool to a new operator.
- `request_commission` calls `distribute_internal` before unlocking new commission. [4](#0-3) [5](#0-4) [6](#0-5) 

If the `distribution_pool` currently owns an outstanding (already-inactive) share for the operator — which happens naturally any time commission has been unlocked and the lockup has since expired — then `distribute_internal`'s loop will attempt to pay the operator first (list order is not staker-controlled) and abort if that payment fails. Because the operator fully controls their own account state (they can flip `allow_arbitrary_coin_transfers` to `false` via `aptos_account::set_allow_direct_coin_transfers` and simply never register for `AptosCoin`), the operator can deterministically make their own commission-payout leg always fail. Note the codebase is *aware* of exactly this bug class: `vesting::set_beneficiary` explicitly checks `assert_account_is_registered_for_apt(new_beneficiary)` "so distribute() wouldn't fail and block all other accounts from receiving APT" — but no equivalent check exists in `staking_contract` for the operator address at `create_staking_contract`, `switch_operator`, or for `set_beneficiary_for_operator`'s `new_beneficiary`: [7](#0-6) [8](#0-7) 

### Impact Explanation
Since `distribute_internal` reverts the entire transaction on the failing recipient rather than skipping it, an operator who deliberately renders their own commission-deposit un-payable can:
- Block the staker from ever calling `unlock_stake` successfully (staker's stake, and pending inactive commission, become permanently stuck in the stake pool tied to that operator).
- Block `switch_operator`, preventing the staker from ever leaving the malicious operator.
- Block `request_commission` from anyone, keeping the pool's accounting frozen indefinitely.

This traps the staker's (and, if used through `vesting.move`, the vesting shareholders') stake with no unprivileged recovery path, since the staker cannot force-register or unfreeze the operator's account. This matches "Permanent lock or non-recoverable loss of claim rights in stake... commission... flows" and "Operator commission... corruption that... traps value."

### Likelihood Explanation
The precondition is straightforward and fully within an operator's own privileges (no special role beyond being the counter-party operator, which is the normal, expected relationship in `staking_contract`): call `aptos_account::set_allow_direct_coin_transfers(false)` on their own account and ensure they remain unregistered for `AptosCoin`. From then on, any accrual of unlocked commission for that operator (which happens automatically as rewards accrue and lockups expire) causes every subsequent `distribute`, `unlock_stake`, `switch_operator`, or `request_commission` call touching that pool to abort. This requires no complex timing and is entirely attacker-controlled once they are chosen as operator.

### Recommendation
Mirror the `vesting.move` mitigation in `staking_contract.move`:
- Require `assert_account_is_registered_for_apt`-style validation for the operator (and `new_beneficiary` in `set_beneficiary_for_operator`) at `create_staking_contract` and `switch_operator`/`switch_operator_with_same_commission` time.
- Make `distribute_internal`'s payout loop resilient to a single failing recipient (e.g., catch/skip a failed deposit and retain the shares/coins for later retry) instead of aborting the whole distribution and blocking unrelated stakers.

### Proof of Concept
Conceptual PoC (would need to be executed via a Devin session using the Move test harness in `staking_contract.move`'s test module):
1. Staker calls `create_staking_contract(staker, operator, voter, amount, commission_percentage=10, ...)`.
2. Operator calls `aptos_account::set_allow_direct_coin_transfers(operator_signer, false)` (and never calls `coin::register<AptosCoin>`).
3. Advance epochs so the stake pool earns rewards; call `request_commission` to unlock the operator's 10% commission (adds an operator share to `distribution_pool`), then fast-forward past the lockup so that commission is withdrawable.
4. Staker calls `unlock_stake(staker, operator, amount)`. Trace: `unlock_stake` → `distribute_internal` → loop reaches the operator recipient → `aptos_account::deposit_coins(operator, ...)` → `!coin::is_account_registered` is true and `can_receive_direct_coin_transfers(operator)` is false → `assert!` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
5. Result: the entire `unlock_stake` transaction reverts; the staker cannot withdraw stake, and the same abort recurs for every future `unlock_stake`/`switch_operator`/`request_commission` call as long as the operator keeps direct transfers disabled and unregistered — reproducing the "funds stuck due to an un-payable leg of a multi-party transfer" bug class from the external report, applied to native Aptos stake instead of bridged ETH.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L607-635)
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

        request_commission_internal(
            operator,
            staking_contract,
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L678-719)
```text
    public entry fun unlock_stake(
        staker: &signer, operator: address, amount: u64
    ) acquires Store, BeneficiaryForOperator {
        // Short-circuit if amount is 0.
        if (amount == 0) return;

        let staker_address = signer::address_of(staker);
        assert_staking_contract_exists(staker_address, operator);

        let store = borrow_global_mut<Store>(staker_address);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);

        // Force distribution of any already inactive stake.
        distribute_internal(
            staker_address,
            operator,
            staking_contract,
        );

        // For simplicity, we request commission to be paid out first. This avoids having to ensure to staker doesn't
        // withdraw into the commission portion.
        let commission_paid =
            request_commission_internal(
                operator,
                staking_contract,
            );

        // If there's less active stake remaining than the amount requested (potentially due to commission),
        // only withdraw up to the active amount.
        let (active, _, _, _) = stake::get_stake(staking_contract.pool_address);
        if (active < amount) {
            amount = active;
        };
        staking_contract.principal -= amount;

        // Record a distribution for the staker.
        add_distribution(
            operator,
            staking_contract,
            staker_address,
            amount,
        );
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L762-805)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-920)
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
        } else {
            coin::destroy_zero(coins);
        }
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
