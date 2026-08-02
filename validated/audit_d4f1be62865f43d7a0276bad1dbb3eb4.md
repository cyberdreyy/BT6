### Title
Beneficiary front-running redirects already-earned but undistributed operator commission - ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Summary
`staking_contract::request_commission` accrues an operator's unpaid commission as *shares* in a `pool_u64` distribution pool keyed by the `operator` address, not by the beneficiary address. The actual recipient of those shares' value is only resolved at `distribute_internal` time, by looking up `beneficiary_for_operator(operator)` *at the moment of distribution*, rather than the beneficiary that was in effect when the commission was earned/requested. `set_beneficiary_for_operator` lets the operator change this mapping at any time with no restriction and without forcing a prior distribution. [1](#0-0) 

### Finding Description
When commission is requested, shares in `staking_contract.distribution_pool` are bought under the shareholder key `operator` (see `request_commission_internal`/`update_distribution_pool`, invoked from `request_commission`). [2](#0-1) 

Later, when `distribute_internal` actually redeems those shares and pays out coins, it resolves the payment address dynamically:

```
if (recipient == operator) {
    recipient = beneficiary_for_operator(operator);
};
``` [3](#0-2) 

The beneficiary lookup is not snapshotted when the commission accrued or when `request_commission` was called — it is re-read at whatever `distribute()` call finally executes. Meanwhile, `set_beneficiary_for_operator` can be called by the operator at any time, unconditionally overwriting `BeneficiaryForOperator.beneficiary_for_operator`, with no check that any pending commission has first been distributed to the current beneficiary: [1](#0-0) 

The doc comment on the SDK builder even acknowledges this exact race condition as a known caveat rather than a code-enforced guarantee: "To ensures payment to the current beneficiary, one should first call `distribute` before switching the beneficiary." [4](#0-3) 

This is a direct structural analog of the `_approve` "replace instead of increase/lock" bug in the report: value that has already accrued to party A (old beneficiary) is redirected to party B (new beneficiary) purely because a mapping was replaced instead of being resolved atomically at accrual time, and there is no code path that forces a flush/distribution before the mapping is changed.

### Impact Explanation
An operator can withhold calling `distribute()`, let commission accumulate as shares tied to their address, then call `set_beneficiary_for_operator` to redirect the entire unpaid commission balance to a new address before it is ever paid to the previously designated beneficiary. This directly corrupts the commission payout accounting: the same accrued value is credited to the wrong account (the new beneficiary) at the expense of the old beneficiary, who has no way to force payment before the switch takes effect (they can only call `distribute`/`request_commission` themselves if they happen to notice the switch is imminent, but there is no atomicity or lock preventing the operator from front-running that call). This matches the "Operator commission, beneficiary payout... corruption that credits the wrong account or traps value" required impact category.

### Likelihood Explanation
Likelihood is moderate-to-high for adversarial operators specifically, since:
- The operator role already legitimately controls `set_beneficiary_for_operator` — no privilege escalation is needed.
- The action is a single unconditional entry function call requiring no special timing precision beyond calling it before any external party triggers `distribute`.
- The old beneficiary has no on-chain guarantee of being paid before a switch; they must proactively race the operator's transaction, which the operator controls the timing of.

This is a self-serving griefing/theft vector against a beneficiary who is owed commission (e.g., a fee-sharing partner, a delegated pool manager, or a compromised/former operator's contracted beneficiary) rather than a fully unprivileged third party, so it should be weighed as impacting the beneficiary/operator trust relationship rather than an arbitrary attacker draining unrelated user funds.

### Recommendation
Enforce a mandatory `distribute_internal` call (flushing any pending commission to the *current* beneficiary) inside `set_beneficiary_for_operator` before the `beneficiary_for_operator` mapping is updated, analogous to how `update_commision` already calls `distribute_internal` before changing state: [5](#0-4) 
Alternatively, snapshot/resolve the beneficiary at the time shares are bought (`request_commission_internal`) rather than at distribution time, so a later beneficiary change cannot retroactively redirect already-requested commission.

### Proof of Concept
1. Operator O has `commission_percentage > 0` and an active staking contract with staker S, current beneficiary B1 (default, `beneficiary_for_operator(O) == O` if unset, or explicitly set to B1).
2. Rewards accrue on the stake pool over several epochs, all owed to O (payable to B1).
3. O calls `staking_contract::request_commission` — this buys shares in `distribution_pool` under shareholder key `O` for the full pending commission amount but does **not** yet transfer coins to B1 (funds remain "pending_inactive"/pending until lockup expiry and an actual `distribute` call). [2](#0-1) 
4. Before anyone calls `distribute()`, O calls `set_beneficiary_for_operator(O, B2)`, unconditionally overwriting the beneficiary mapping with no distribution check. [1](#0-0) 
5. Once the lockup expires and `distribute()` (callable by anyone) is finally invoked, `distribute_internal` resolves `recipient = beneficiary_for_operator(O)`, which now returns B2, and pays B2 the full commission amount that had accrued while B1 was the designated beneficiary. [6](#0-5) 
6. B1 never receives the commission that had already accrued to them under the pre-switch beneficiary designation.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L580-592)
```text
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

```

**File:** aptos-move/framework/cached-packages/src/aptos_framework_sdk_builder.rs (L1113-1118)
```rust
    /// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
    /// beneficiary. To ensures payment to the current beneficiary, one should first call `distribute` before switching
    /// the beneficiary. An operator can set one beneficiary for staking contract pools, not a separate one for each pool.
    StakingContractSetBeneficiaryForOperator {
        new_beneficiary: AccountAddress,
    },
```
