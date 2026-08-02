Found a concrete Aptos-native analog in `staking_contract.move`. The bug: pending commission owed to a *former* operator can bypass that operator's registered beneficiary and be paid to the operator's raw address once the staking contract is reassigned to a new operator.

### Title
Commission owed to a removed/switched operator bypasses their registered beneficiary after `switch_operator` - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`switch_operator` reassigns a `StakingContract` (including its `distribution_pool`) from `old_operator` to `new_operator`, but any commission that was already queued for `old_operator` in the `distribution_pool` (via `request_commission_internal`/`add_distribution`) remains keyed to `old_operator`'s address inside the same pool object that is now stored under the `new_operator` key. When that queued commission finally becomes withdrawable and `distribute()` is called, `distribute_internal` only redirects a payout to a beneficiary when the shareholder address equals the *current* `operator` parameter. Since the current operator is now `new_operator`, the stale entry for `old_operator` never matches, so the beneficiary redirection set via `set_beneficiary_for_operator` is silently skipped, and funds are deposited straight to `old_operator`'s raw account address instead.

### Finding Description
`switch_operator` at [1](#0-0)  removes the `StakingContract` struct keyed by `old_operator`, force-distributes already-inactive funds, then calls `request_commission_internal` which queues the *current epoch's* commission as a new distribution entry for `old_operator` via `add_distribution` (which calls `distribution_pool.buy_in(recipient, coins_amount)`), and finally re-inserts the very same `StakingContract` (same `distribution_pool`) under the `new_operator` key.

`request_commission_internal` only unlocks the commission from the stake pool (`stake::unlock_with_cap`); the coins do not become actually withdrawable until the stake pool's lockup expires [2](#0-1) . This creates a real window in which the `distribution_pool` still holds an un-redeemed share for `old_operator` while the whole struct is now addressed by `new_operator`.

When `distribute()`/`distribute_internal` is eventually invoked (by anyone, since it's a public entry function open to all callers) it iterates all shareholders and only substitutes the beneficiary when the shareholder equals the `operator` argument passed in — which is always the *current* operator key used to look up the `Store`: [3](#0-2) 

Because `old_operator != new_operator`, this check fails for the stale entry, and `aptos_account::deposit_coins` sends the commission directly to `old_operator`'s address, completely bypassing the `BeneficiaryForOperator` mapping that `old_operator` had explicitly configured with `set_beneficiary_for_operator` [4](#0-3) .

This is the direct analog of the external report's bug class: a role (operator) that has been removed/replaced from the governance/control position (here, replaced by `new_operator`) still has value routed to its raw address instead of the properly-configured claim destination (beneficiary), because the removal path doesn't reconcile the pending value/claim-rights state tied to the old role.

### Impact Explanation
`set_beneficiary_for_operator` exists specifically so operator commission can be redirected away from the operator's signing address (e.g., to a colder custody wallet, a multisig, or a different entity entirely). A common real-world trigger for `switch_operator` is exactly a security incident — the staker replacing an operator whose key may be compromised or who is being removed for misbehavior. In that scenario, this bug ensures that any commission still in flight for the removed operator is paid to the very operator address the staker is trying to cut off, instead of the safe beneficiary address. This is a wrong-account credit of commission/value that the beneficiary mechanism was designed to prevent, directly matching the "Operator commission, beneficiary payout ... corruption that credits the wrong account" impact category.

### Likelihood Explanation
This triggers under normal usage whenever: (1) an operator has unpaid/queued commission (common — any active staking contract accrues commission over time), and (2) the staker calls `switch_operator` (or `switch_operator_with_same_commission`, which calls `switch_operator` internally) before that commission is distributed. `distribute()` is a fully permissionless public entry function, so anyone can trigger the mis-routed payout once the lockup expires. No special privileges are needed beyond the staker's own authority to switch operators, which is a routine and expected action (including as an incident-response action against a bad operator).

### Recommendation
Before reassigning the `StakingContract` to `new_operator` in `switch_operator`, fully settle (distribute) all pending distribution-pool shares owed to `old_operator`, or snapshot/route them to `beneficiary_for_operator(old_operator)` at switch time rather than leaving them to be resolved later under the new operator's key. Alternatively, `distribute_internal` should check `beneficiary_for_operator` lookups against every shareholder that is an operator address (tracked historically), not just the currently active `operator` parameter, e.g. by recording which shareholder addresses in the pool are "operator" commission recipients independent of the current key.

### Proof of Concept
1. Staker creates a staking contract with `operator1` and commission `10%`, staking contract accrues rewards over an epoch.
2. `operator1` calls `set_beneficiary_for_operator(operator1, beneficiary1)` to route commission to `beneficiary1` [5](#0-4) .
3. Staker calls `switch_operator(staker, operator1, operator2, new_commission)`. Internally: `distribute_internal` pays out any already-inactive funds correctly to `beneficiary1` (since `operator` param is still `operator1` at that point), then `request_commission_internal(operator1, staking_contract)` queues the *current* epoch's unlocked-but-not-yet-inactive commission as a share for `operator1` in the `distribution_pool`, and the whole `StakingContract` struct (with that pending share) is moved to the `operator2` key [1](#0-0) .
4. Fast-forward past the stake pool lockup so the queued commission becomes actually withdrawable/inactive.
5. Anyone calls `distribute(staker_address, operator2)`. Inside `distribute_internal`, the shareholder `operator1` is redeemed, but since `recipient (operator1) != operator (operator2)`, the beneficiary substitution is skipped, and the commission is deposited directly to `operator1`'s address instead of `beneficiary1` [3](#0-2) .
6. Result: `operator1` (the removed/replaced operator) receives commission at its own address despite having configured a beneficiary, and despite no longer being the active operator on the pool.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L637-674)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L783-804)
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
