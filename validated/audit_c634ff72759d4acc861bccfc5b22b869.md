No vulnerability found for this question.

**Analysis:**

`request_commission` enforces a strict authorization check before performing any state changes: [1](#0-0) 

The caller must be exactly the `staker`, the `operator`, or `beneficiary_for_operator(operator)` — where `beneficiary_for_operator` resolves strictly against the `BeneficiaryForOperator` resource stored *under the operator's own account address*: [2](#0-1) 

There is no "identity collision" possible here: `BeneficiaryForOperator` is a `key` resource that can only be created via `move_to(operator, ...)` inside `set_beneficiary_for_operator`, which is signer-gated to the operator calling it for their own address: [3](#0-2) 

An attacker cannot set a beneficiary for an "unrelated operator" — they can only set a beneficiary resource under their own account, and `beneficiary_for_operator(operator)` always reads the resource at the specific `operator` address passed into `request_commission`, not the attacker's address. So the only way `account_addr == beneficiary_for_operator(operator)` succeeds is if the real operator previously and legitimately designated `account_addr` as their beneficiary.

Additionally, `request_commission` only ever mutates the `StakingContract` entry keyed by `(staker, operator)` inside that staker's `Store.staking_contracts` map: [4](#0-3) 

and `add_distribution`/`update_distribution_pool` inside `request_commission_internal` operate strictly on that same struct's `distribution_pool`, crediting the `operator` recipient: [5](#0-4) 

There is no code path where calling `request_commission` for one `(staker, operator)` pair touches a different pair's `distribution_pool`, and no path where an unrelated/unprivileged caller can satisfy the authorization check without already controlling the staker, operator, or a beneficiary role that the real operator explicitly granted. This is a permissioned entry function (not "permissionless-looking" as claimed once the assert is read), and the existing role check and accounting scoping already block the described attack path.

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L607-617)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L619-634)
```text
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L637-657)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L810-829)
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
```
