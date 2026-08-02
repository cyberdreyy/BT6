## Finding

### Title
Operator can permanently brick `staking_contract::distribute_internal`, freezing the staker's unlocked stake, by setting a beneficiary that rejects direct coin transfers - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
The reported Buyout bug is a single-recipient-blocks-everyone pattern: paying out one uncooperative address inside a loop reverts the whole payout and freezes the contract for every other participant. The same pattern exists in `staking_contract::distribute_internal`, which iterates over *all* shareholders of a `distribution_pool` (staker + operator/beneficiary) in one atomic `while` loop and aborts the entire transaction if any single `aptos_account::deposit_coins` call fails.

### Finding Description
`distribute_internal` drains the pool's withdrawable stake and then loops over every shareholder, redeeming their shares and depositing coins to them one at a time: [1](#0-0) 

The recipient for the operator's distribution is resolved via `beneficiary_for_operator(operator)`, which any operator can set unilaterally with `set_beneficiary_for_operator`: [2](#0-1) 

`aptos_account::deposit_coins` will abort if the recipient account already exists but has explicitly opted out of unregistered direct coin transfers (`set_allow_direct_coin_transfers(false)`) and is not registered for `AptosCoin`: [3](#0-2) 

Because Move aborts revert all state changes atomically, if the beneficiary address reverts the deposit, the entire `distribute_internal` call fails — not just the operator's share. Since `while (distribution_pool.shareholders_count() > 0)` processes every remaining shareholder (staker included) in a single transaction, one poisoned recipient blocks payout to everyone else in the same distribution queue.

`distribute_internal` is not only reachable via the permissionless `distribute` entry function, but is also invoked as a mandatory first step of `request_commission` and `unlock_stake` (both are documented as forcing "distribution of any already inactive stake" before proceeding): [4](#0-3) [5](#0-4) 

So once an operator sets a hostile beneficiary and any commission becomes payable, `distribute`, `request_commission`, and `unlock_stake` for that staker/operator pair all revert forever, since none of them can get past the loop.

### Impact Explanation
This traps the staker's already-unlocked stake (inactive/pending_inactive coins withdrawn from the underlying stake pool via `stake::withdraw_with_cap`) inside the `staking_contract` module indefinitely — the staker can never call `unlock_stake`, `request_commission`, or `distribute` successfully for that operator once the malicious beneficiary is set and a distribution is pending. There is no alternate withdrawal path once a distribution is queued for a poisoned beneficiary, matching the "Permanent lock or non-recoverable loss of claim rights" and "Operator commission/beneficiary ... traps value" impact categories for this scan. The staker's only escape is `switch_operator`, but that itself calls `distribute_internal` first via the same code path (per the module's design), so it is equally blocked while the hostile distribution is outstanding.

### Likelihood Explanation
The operator is not a privileged party from the staker's perspective — this scenario is reachable by any operator who accepted a staking contract, which is a normal, permissionless configuration in the staking_contract flow. Setting `allow_arbitrary_coin_transfers = false` on the beneficiary account and never registering it for `AptosCoin` is a simple, cheap, one-time setup requiring no special privilege, and `set_beneficiary_for_operator` is callable directly by the operator.

### Recommendation
Do not let a single failing recipient abort the whole distribution loop. Wrap each `aptos_account::deposit_coins` call so failures are caught/skipped (e.g., check `coin::is_account_registered`/`can_receive_direct_coin_transfers` before attempting deposit and, if it would fail, hold the shares in an escrow/pending state the recipient can later claim), similar to the buyout report's recommended mitigation of persisting failed-payout state and exposing a separate withdraw function instead of doing a single all-or-nothing deposit loop.

### Proof of Concept
1. Staker creates a staking contract with `operator` (`create_staking_contract`).
2. `operator` calls `aptos_account::set_allow_direct_coin_transfers(operator_beneficiary_signer, false)` on an account they control that already exists but is not registered for `AptosCoin`.
3. `operator` calls `staking_contract::set_beneficiary_for_operator(operator, beneficiary_addr)` (requires `operator_beneficiary_change_enabled` feature, no other privilege needed) — see `set_beneficiary_for_operator` at lines 807-838.
4. Stake pool earns rewards; `operator` or `staker` calls `request_commission`, which internally calls `distribute_internal` and then `add_distribution(operator, ...)`, queuing a distribution entry that resolves to `beneficiary_addr` on the next `distribute_internal` run.
5. Once that commission becomes withdrawable (lockup ends) and someone calls `distribute` (or `unlock_stake`/`request_commission` again), `distribute_internal`'s loop reaches `beneficiary_addr`, calls `aptos_account::deposit_coins`, which hits the `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` assert in `aptos_account.move` line 126, and the whole transaction reverts.
6. All subsequent calls to `distribute`, `unlock_stake`, and `request_commission` for this staker/operator pair now revert permanently, freezing the staker's unlocked stake.

### Citations

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L676-690)
```text
    /// Staker can call this to request withdrawal of part or all of their staking_contract.
    /// This also triggers paying commission to the operator for accounting simplicity.
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L807-838)
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-911)
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
```

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L109-131)
```text
    /// Convenient function to deposit a custom CoinType into a recipient account that might not exist.
    /// This would create the recipient account first and register it to receive the CoinType, before transferring.
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
