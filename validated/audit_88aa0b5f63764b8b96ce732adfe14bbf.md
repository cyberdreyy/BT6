No vulnerability found for this question.

**Analysis:**

`set_beneficiary_for_operator` in `staking_contract.move` stores the `BeneficiaryForOperator` resource under the **operator's own address**, keyed solely by that operator address [1](#0-0) . This is documented as intentional design: "An operator can set one beneficiary for staking contract pools, not a separate one for each pool" [2](#0-1) .

In `distribute_internal`, the redirect to a beneficiary only happens `if (recipient == operator)`, i.e., only for the commission share attributed to that specific `operator` address that is actually registered on that particular staker's `StakingContract` [3](#0-2) . The `operator` parameter passed into `distribute_internal` comes from the specific staker's own `Store.staking_contracts` map entry, populated only when that staker created a contract with that operator via `create_staking_contract` or `switch_operator` [4](#0-3) .

For the proof idea to work, the attacker would need to already be the registered `operator` on the *other* staker's contract — but the scenario explicitly states "two stakers under distinct operators." If the operators are distinct addresses, calling `set_beneficiary_for_operator` as `operator1`'s signer only writes `BeneficiaryForOperator` under `operator1`'s address, and `distribute` for the second staker's contract looks up `beneficiary_for_operator(operator2)`, which is unaffected since `operator2 != operator1` [5](#0-4) . No cross-staker redirection is possible unless the same address is genuinely the operator on both contracts, which is a role granted by each staker independently and not something an unprivileged attacker can forge.

This falls under the excluded category: the finding assumes the attacker already possesses the operator role for the affected contract, which the review bounds explicitly reject ("Reject anything that assumes the attacker already owns the pool, operator role, or governance authority").

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L362-368)
```text
    public fun beneficiary_for_operator(operator: address): address acquires BeneficiaryForOperator {
        if (exists<BeneficiaryForOperator>(operator)) {
            return borrow_global<BeneficiaryForOperator>(operator).beneficiary_for_operator
        } else {
            operator
        }
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L856-900)
```text
    fun distribute_internal(
        staker: address,
        operator: address,
        staking_contract: &mut StakingContract,
    ) acquires BeneficiaryForOperator {
        let pool_address = staking_contract.pool_address;
        // Create the Staker resource if it doesn't exist to backfill the Staker resource for each pool.
        if (!exists<Staker>(pool_address)) {
            let pool_signer =
                &account::create_signer_with_capability(&staking_contract.signer_cap);
            move_to(pool_signer, Staker { staker });
        };
        let (_, inactive, _, pending_inactive) = stake::get_stake(pool_address);
        let total_potential_withdrawable = inactive + pending_inactive;
        let coins =
            stake::withdraw_with_cap(
                &staking_contract.owner_cap, total_potential_withdrawable
            );
        let distribution_amount = coin::value(&coins);
        if (distribution_amount == 0) {
            coin::destroy_zero(coins);
            return
        };

        let distribution_pool = &mut staking_contract.distribution_pool;
        update_distribution_pool(
            distribution_pool,
            distribution_amount,
            operator,
            staking_contract.commission_percentage
        );

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
