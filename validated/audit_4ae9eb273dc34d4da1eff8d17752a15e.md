### Title
Old operator's beneficiary designation is silently bypassed after `switch_operator`, misdirecting pending commission to the raw operator address - (`File: aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::distribute_internal` only redirects a payout to `beneficiary_for_operator(operator)` when the distribution recipient equals the *current* `operator` parameter passed into the function [1](#0-0) . When a staker calls `switch_operator`, any commission still owed to the *old* operator is preserved as a pending share in the same `distribution_pool`, but keyed under the old operator's raw address, while the `StakingContract` entry itself is now indexed by the *new* operator [2](#0-1) . Because `distribute_internal` is later invoked with the new operator's address as its `operator` argument, the equality check `recipient == operator` fails for the old operator's pending share, so the funds are sent directly to the old operator's account instead of to the beneficiary the old operator had configured via `set_beneficiary_for_operator`.

### Finding Description
`set_beneficiary_for_operator` lets an operator designate a beneficiary address to receive all commission payouts [3](#0-2) . The payout logic that is supposed to honor this designation lives in `distribute_internal`:

```
if (recipient == operator) {
    recipient = beneficiary_for_operator(operator);
};
``` [4](#0-3) 

This check compares the distribution-pool `recipient` against the single `operator` argument that the caller of `distribute_internal` supplies — which is always the operator *currently* associated with the `StakingContract` record (see call sites in `distribute`, `unlock_stake`, `request_commission_internal`) [5](#0-4) .

When the staker calls `switch_operator`, unpaid commission owed to the outgoing operator is not immediately paid out — it is recorded as a share in the pool under the old operator's address, while the `StakingContract` struct (and hence every future `distribute_internal` call) is now keyed and parameterized by the *new* operator. This exact behavior is demonstrated in the test suite, where after `switch_operator`, a pending distribution keyed to `operator_1_address` exists inside the staking contract now associated with `operator_2_address`, and `distribute()` (called with `operator_2_address`) pays `operator_1_address` directly [2](#0-1) [6](#0-5) .

Since `distribute()` is a public entry function documented as callable by *anyone* ("Allow anyone to distribute already unlocked funds") [7](#0-6) , any unprivileged account can trigger this payout path. If the old operator had previously called `set_beneficiary_for_operator` to route commission to a separate custodial/beneficiary address, any commission still pending at the moment `switch_operator` is executed will bypass that beneficiary redirection entirely and land in the old operator's own address instead, once `distribute()` fires — silently breaking the beneficiary invariant the operator explicitly configured.

### Impact Explanation
This corrupts commission-payout routing: value that the (former) operator explicitly designated to go to a beneficiary account is instead credited to the raw operator address, violating the "operator commission... payout... corrupted that credits the wrong account" impact category. Depending on why the beneficiary was configured (e.g., a validator custodian whose operator hot key has no independent claim rights, a smart-contract beneficiary, or compliance-driven payout routing), this can result in commission being paid to an account the operator no longer controls or intends to use, effectively stranding or misdirecting a portion of the staking rewards.

### Likelihood Explanation
The path requires no special privilege beyond a staker performing a routine `switch_operator` call (a normal, supported operation) while there exists unpaid commission for the outgoing operator, followed by anyone invoking `distribute()`. Both preconditions are common in real reward-sharing/validator arrangements where operators are periodically rotated and beneficiaries are used for payout segregation, making this a realistic, low-effort trigger.

### Recommendation
`distribute_internal`'s beneficiary check should not rely solely on comparing `recipient` to the *currently associated* operator. Instead, before redirecting, look up whether `recipient` itself has a `BeneficiaryForOperator` entry (i.e., check `beneficiary_for_operator(recipient)` unconditionally for any recipient that could be an operator address, or track/preserve the original operator identity per distribution share) so that stale/old-operator shares are still routed to their configured beneficiary regardless of which operator is currently active on the contract.

### Proof of Concept
1. Staker creates a staking contract with `operator_1`, commission 10%.
2. `operator_1` calls `set_beneficiary_for_operator(beneficiary_addr)`.
3. Stake pool accrues rewards (epoch passes) but operator_1 does **not** call `request_commission`/`distribute` yet.
4. Staker calls `switch_operator(staker, operator_1, operator_2, new_commission)`. Internally, the unpaid commission is added as a distribution share keyed to `operator_1` inside the staking contract, which is now indexed under `operator_2` [2](#0-1) .
5. Once the pending stake becomes withdrawable, anyone calls `distribute(staker_address, operator_2_address)`.
6. Inside `distribute_internal`, `operator` == `operator_2_address`; the pending share's `recipient` == `operator_1_address`, so `recipient == operator` is false and the beneficiary redirection is skipped [1](#0-0) .
7. Funds are deposited directly to `operator_1_address`, not to `beneficiary_addr`, contradicting the beneficiary configuration set in step 2.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L811-838)
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1561-1590)
```text
        // Switch operators.
        switch_operator(
            staker,
            operator_1_address,
            operator_2_address,
            20
        );
        // The staking_contract is now associated with operator 2 but there should be a pending distribution of unpaid
        // commission to operator 1.
        let new_balance = with_rewards(INITIAL_BALANCE);
        let commission_for_operator_1 = (new_balance - INITIAL_BALANCE) / 10;
        assert_distribution(
            staker_address,
            operator_2_address,
            operator_1_address,
            commission_for_operator_1
        );
        // Unpaid commission should be unlocked from the stake pool.
        new_balance -= commission_for_operator_1;
        stake::assert_stake_pool(
            pool_address,
            new_balance,
            0,
            0,
            commission_for_operator_1
        );
        assert!(
            last_recorded_principal(staker_address, operator_2_address) == new_balance,
            0
        );
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1607-1624)
```text
        // Verify that when commissions are withdrawn, previous pending distribution to operator 1 also happens.
        // Then new commission of 20% is paid to operator 2.
        let commission_for_operator_2 =
            (new_balance - last_recorded_principal(staker_address, operator_2_address))
                / 5;
        new_balance -= commission_for_operator_2;
        request_commission(operator_2, staker_address, operator_2_address);
        assert_distribution(
            staker_address,
            operator_2_address,
            operator_2_address,
            commission_for_operator_2
        );
        let operator_1_balance = coin::balance<AptosCoin>(operator_1_address);
        assert!(
            operator_1_balance == INITIAL_BALANCE + commission_for_operator_1,
            operator_1_balance
        );
```
