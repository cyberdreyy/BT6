## Finding: `staking_contract::set_beneficiary_for_operator` retroactively redirects already-requested but undistributed commission to the new beneficiary

This is the local Aptos-native analog to the `V3Vault.setReserveFactor()` bug class: a permissionless/owner-adjacent state parameter (here, the operator's `beneficiary_for_operator`) is changed without first settling ("distributing") the state that was accrued/committed under the *old* value, so the new value is applied retroactively to already-committed funds.

### Finding Description

`staking_contract::request_commission_internal` unlocks the operator's commission and books it into the staking contract's `distribution_pool` keyed by the **operator's address**, not the beneficiary: [1](#0-0) 

The actual beneficiary is only resolved when funds are finally paid out in `distribute_internal`, and it is resolved using whatever `beneficiary_for_operator(operator)` returns **at distribution time**, not at the time the commission was requested/committed: [2](#0-1) 

`set_beneficiary_for_operator` lets the operator change this mapping at any time, and does **not** force a `distribute()` (i.e., does not settle any already-requested-but-not-yet-paid commission) before applying the change. The function's own doc comment acknowledges the retroactive effect and pushes the burden onto the caller to manually call `distribute()` first: [3](#0-2) 

`distribute()` itself is explicitly permissionless — "not restricted to just the staker or operator" — so anyone can trigger the final payout at any time: [4](#0-3) 

### Impact Explanation

Because the distribution pool stores the pending payout under `operator` rather than under the beneficiary that was active when the commission was requested, any commission that has already been unlocked (via `request_commission`, `unlock_stake`, or `switch_operator`) under beneficiary **A**, but not yet paid out (still waiting for lockup to expire / for someone to call `distribute`), will be paid to whichever address is `beneficiary_for_operator(operator)` **at the moment `distribute()` executes** — which can be a different address **B** if the operator called `set_beneficiary_for_operator(B)` in between. This silently redirects funds that were already earned/committed to A, without A's consent and without touching A's own keys — a beneficiary-payout corruption that credits the wrong account, matching the "operator commission / beneficiary payout ... corruption that credits the wrong account" impact category.

### Likelihood Explanation

The operator fully controls the timing of `set_beneficiary_for_operator`, and `distribute()` is callable by anyone (including the operator itself right after the beneficiary swap), so the sequence (request commission → swap beneficiary → call distribute) is trivially executable by the operator alone with no cooperation needed from any other privileged role. The precondition (some already-unlocked but undistributed commission existing) is normal operational state whenever a staking contract has any nonzero commission percentage and periodic unlocks.

### Recommendation
Resolve and lock in the beneficiary at the moment commission is requested (store the resolved beneficiary address directly in the distribution entry, e.g. call `add_distribution` with `beneficiary_for_operator(operator)` instead of `operator`), or force a `distribute_internal` call inside `set_beneficiary_for_operator` before the beneficiary mapping is updated, analogous to updating global interest before changing the reserve factor.

### Proof of Concept
1. Staker creates a staking contract with `operator` and nonzero `commission_percentage`; operator's beneficiary is set to `A` via `set_beneficiary_for_operator(A)`.
2. Stake pool earns rewards; `request_commission` (or `unlock_stake`) is called, which runs `request_commission_internal`, unlocking commission and calling `add_distribution(operator, staking_contract, operator, commission_amount)` — booked under `operator`, not `A` [5](#0-4) .
3. Before the stake pool's lockup expires and before anyone calls `distribute`, the operator calls `set_beneficiary_for_operator(B)`.
4. Once the lockup ends, anyone calls `staking_contract::distribute(staker, operator)`. In `distribute_internal`, since `recipient == operator`, it resolves `recipient = beneficiary_for_operator(operator)`, which is now `B` [6](#0-5) , and the entire committed commission (including the portion earned/committed while beneficiary was `A`) is paid to `B` instead of `A`.

### Citations

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
