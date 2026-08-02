## Title
Malicious operator can permanently DoS a staker's `unlock_stake`/`switch_operator` by pointing `beneficiary_for_operator` at an address that opted out of direct coin transfers - (`File: aptos-move/framework/aptos-framework/sources/staking_contract.move`)

## Summary
`staking_contract::distribute_internal` pays outstanding commission to `beneficiary_for_operator(operator)` via `aptos_account::deposit_coins`, which **reverts** if the recipient account is unregistered for `AptosCoin` and has opted out of unsolicited direct transfers. [1](#0-0) 
`set_beneficiary_for_operator` lets the operator set this address to anything, with no registration check. [2](#0-1) 
`distribute_internal` is force-invoked inside `unlock_stake` and `switch_operator`/`request_commission`, all of which are the *staker's* own operations, so an unpayable beneficiary blocks the staker (the victim), not just the operator.

## Finding Description
`aptos_account::deposit_coins` reverts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` when the target is not registered for the coin and `can_receive_direct_coin_transfers` is false (an opt-out any account can set for itself): [3](#0-2) 

`distribute_internal` unconditionally routes the operator's commission share to `beneficiary_for_operator(operator)` and calls `aptos_account::deposit_coins` on it: [1](#0-0) 

`set_beneficiary_for_operator` sets this address with zero validation — no check that the address exists, is registered for `AptosCoin`, or accepts direct transfers: [2](#0-1) 

This is the exact same bug class as the reported PredyPool issue: a party-controlled "recipient" address is set to one that reliably reverts on receipt of funds, which is embedded inside a state-transition that other, unprivileged, honest parties depend on. Notably, the vesting module already recognizes and mitigates this exact failure mode for its analogous `set_beneficiary` function, with an explicit comment stating the intent: [4](#0-3) 
That same guard (`assert_account_is_registered_for_apt`) is absent from `staking_contract::set_beneficiary_for_operator`, indicating an inconsistent/missing fix.

The forced-distribution call sites make this reach the staker's own critical operations:
- `unlock_stake` (staker-invoked) force-distributes before processing the staker's unlock request: [5](#0-4) 
- `switch_operator` (staker-invoked, used to fire a malicious/underperforming operator) also force-distributes before reassigning the operator: [6](#0-5) 
- `request_commission` (staker/operator/beneficiary-invoked) does the same: [7](#0-6) 
- The permissionless `distribute` entry function is directly blocked as well: [8](#0-7) 

## Impact Explanation
A malicious or compromised operator (a normal, permissionless, untrusted role a staker delegates stake to — not a privileged/admin role) can call `set_beneficiary_for_operator` with an address that has no `AptosCoin` `CoinStore` and has disabled `allow_arbitrary_coin_transfers`. As long as there is any nonzero unlocked commission owed to the operator, every subsequent call to `distribute`, `request_commission`, `unlock_stake`, and `switch_operator` for that staking contract will abort, because `distribute_internal` is unconditionally forced before those operations complete.

This traps the **staker's own stake**: the staker cannot unlock their principal (`unlock_stake`) nor fire the malicious operator (`switch_operator`) while any commission remains pending distribution — which the operator can perpetually ensure simply by staying in the validator set and accruing more rewards/commission each epoch. This is a stronger analog than the original report (which only self-DoS'd the attacker's own liquidation): here an unprivileged operator can strand a staker's funds and prevent them from ever removing the operator, a permanent denial of withdrawal/reactivation rights rather than a temporary one. This satisfies the "permanent lock or non-recoverable loss of claim rights" and "wrong-role control" impact bars for stake flows.

## Likelihood Explanation
High. Setting up an unregistered/opted-out address is trivial and requires no special privilege — any operator can create a fresh unregistered account (or use an existing account that called `set_allow_direct_coin_transfers(false)` before ever registering `CoinStore<AptosCoin>`), then call `set_beneficiary_for_operator` once `features::operator_beneficiary_change_enabled()` is on. No collusion with the staker or governance is required, and the attack persists automatically as long as commission keeps accruing.

## Recommendation
Add the same guard used in `vesting::set_beneficiary` to `staking_contract::set_beneficiary_for_operator`: require `aptos_account::assert_account_is_registered_for_apt(new_beneficiary)` (or equivalently check `coin::is_account_registered<AptosCoin>` / `can_receive_direct_coin_transfers`) before allowing the beneficiary to be set. Additionally, consider making `distribute_internal`'s per-recipient payout resilient to a single failing transfer (e.g., skip/queue a failed payout rather than reverting the whole distribution), so that one poisoned recipient cannot block distributions to all other shareholders or block the staker's unrelated `unlock_stake`/`switch_operator` calls.

## Proof of Concept
1. Staker creates a staking contract with `create_staking_contract(staker, operator, voter, amount, commission_percentage, ...)`, operator joins the validator set.
2. Operator calls `staking_contract::set_beneficiary_for_operator(operator, evil_addr)` where `evil_addr` is a brand-new address that has never called `coin::register<AptosCoin>` and has called `aptos_account::set_allow_direct_coin_transfers(&evil_signer, false)` (or simply never registered and default opt-out applies once the feature is toggled off for it).
3. Epochs pass; the stake pool accrues rewards, so `commission_amount > 0`.
4. Staker calls `staking_contract::unlock_stake(staker, operator, amount)` to withdraw part of their principal.
   - Internally this calls `distribute_internal`, which computes `distribution_amount > 0`, loops to the operator's share, resolves `recipient = beneficiary_for_operator(operator) = evil_addr`, and calls `aptos_account::deposit_coins(evil_addr, ...)`.
   - Because `evil_addr` is unregistered and rejects direct transfers, this call aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`, reverting the entire `unlock_stake` transaction.
5. The staker also cannot call `switch_operator` to remove the malicious operator, for the same reason (forced `distribute_internal` in the switch path).
6. The staker's principal remains locked to the operator indefinitely, with no owner-side function to bypass the forced distribution step. [9](#0-8)

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L676-720)
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L842-853)
```text
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
