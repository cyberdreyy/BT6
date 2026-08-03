No vulnerability found for this question.

**Analysis:**

The described behavior in `staking_contract.move` is explicitly documented, intentional design, not an exploitable flaw reachable by an unprivileged attacker:

1. `distribute` is deliberately unauthenticated by design — the doc comment states "Allow anyone to distribute already unlocked funds. This does not affect reward compounding and therefore does not need to be restricted to just the staker or operator." [1](#0-0) . Calling `distribute` only triggers payout of already-recorded distribution shares in `distribution_pool` — the caller receives nothing themselves unless they already hold a recorded share.

2. Resolving `beneficiary_for_operator(operator)` at distribution time (not accrual time) is also explicitly documented and intentional. The doc comment on `set_beneficiary_for_operator` states: "Any existing unpaid commission rewards will be paid to the new beneficiary. To ensure payment to the current beneficiary, one should first call `distribute` before switching the beneficiary." [2](#0-1)  This is confirmed in `distribute_internal`, where the recipient substitution happens only for the entry recorded under the `operator` key: `if (recipient == operator) { recipient = beneficiary_for_operator(operator); }` [3](#0-2) .

3. Critically, `set_beneficiary_for_operator` can only be invoked by the operator itself (`signer::address_of(operator)`), so the "new beneficiary" is always an address the operator has chosen to designate — it is not an unprivileged attacker gaining unauthorized routing power. [4](#0-3)  The scenario in the question requires the operator to have already switched beneficiaries (a privileged operator action), which the Decision Standard explicitly excludes: "Reject anything that assumes the attacker already owns the pool, operator role, or governance authority."

4. The existing unit test `test_operator_can_set_beneficiary` confirms this exact flow works as documented: commission accrued while beneficiary A was set is correctly still attributed and payable, and switching beneficiaries only affects payouts of not-yet-distributed commission going forward, per design. [5](#0-4) 

Since the recipient of any redirected funds is always an address the operator itself designated, and any third party calling `distribute` cannot redirect funds to themselves or any arbitrary address, this does not meet the bar of "unprivileged input changes who can withdraw... commission" — it is a documented tradeoff requiring the operator's own privileged action to trigger, not an attacker-controlled privilege escalation.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L807-829)
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L895-898)
```text
            // If the recipient is the operator, send the commission to the beneficiary instead.
            if (recipient == operator) {
                recipient = beneficiary_for_operator(operator);
            };
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1741-1793)
```text
        // Set beneficiary.
        set_beneficiary_for_operator(operator1, beneficiary_address);
        assert!(beneficiary_for_operator(operator1_address) == beneficiary_address, 0);

        // Fast forward to generate rewards.
        stake::end_epoch();
        let new_balance = with_rewards(INITIAL_BALANCE);
        stake::assert_stake_pool(pool_address, new_balance, 0, 0, 0);

        // Operator claims 10% of rewards so far as commissions.
        let expected_commission_1 =
            (new_balance - last_recorded_principal(staker_address, operator1_address))
                / 10;
        new_balance -= expected_commission_1;
        request_commission(operator1, staker_address, operator1_address);
        stake::assert_stake_pool(
            pool_address,
            new_balance,
            0,
            0,
            expected_commission_1
        );
        assert!(
            last_recorded_principal(staker_address, operator1_address) == new_balance, 0
        );
        assert_distribution(
            staker_address,
            operator1_address,
            operator1_address,
            expected_commission_1
        );
        stake::fast_forward_to_unlock(pool_address);

        // Both original stake and operator commissions have received rewards.
        expected_commission_1 = with_rewards(expected_commission_1);
        new_balance = with_rewards(new_balance);
        stake::assert_stake_pool(
            pool_address,
            new_balance,
            expected_commission_1,
            0,
            0
        );
        distribute(staker_address, operator1_address);
        let operator_balance = coin::balance<AptosCoin>(operator1_address);
        let beneficiary_balance = coin::balance<AptosCoin>(beneficiary_address);
        let expected_operator_balance = INITIAL_BALANCE;
        let expected_beneficiary_balance = expected_commission_1;
        assert!(operator_balance == expected_operator_balance, operator_balance);
        assert!(beneficiary_balance == expected_beneficiary_balance, beneficiary_balance);
        stake::assert_stake_pool(pool_address, new_balance, 0, 0, 0);
        assert_no_pending_distributions(staker_address, operator1_address);

```
