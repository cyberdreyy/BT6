Based on the code I traced in `staking_contract.move`, I found a concrete beneficiary-redirect bug tied to operator switching. I was not able to fully verify the exact timing semantics of `stake::withdraw_with_cap` for `pending_inactive` stake before lockup expiry (the relevant `stake.move` function bodies were not returned by my searches), so I note that as an open verification point in the PoC below. The core defect, however, is provable purely from `staking_contract.move` itself, independent of that timing detail.

### Title
Stale commission distribution entries are paid to the raw operator address instead of the operator's registered beneficiary after `switch_operator` - (File: aptos-move/framework/aptos-framework/sources/staking_contract.move)

### Summary
`distribute_internal` redirects a commission payout to `beneficiary_for_operator(operator)` only when the distribution-pool recipient address equals the **currently associated** `operator` parameter passed into the call [1](#0-0) . `switch_operator` preserves the entire `StakingContract` struct — including its `distribution_pool` — and simply re-keys it from `old_operator` to `new_operator` [2](#0-1) . Any commission distribution entry recorded under `old_operator` (added via `add_distribution(operator, ..., operator, commission_amount)` inside `request_commission_internal` [3](#0-2) ) that has not yet been fully paid out at switch time will persist in the shared `distribution_pool`. Once the operator changes, every later permissionless `distribute()` call passes the **new** operator as the `operator` parameter, so the stale `old_operator` entry never matches `recipient == operator`, and the funds are sent directly to `old_operator`'s raw account instead of `beneficiary_for_operator(old_operator)`.

### Finding Description
1. A staker creates a staking contract with `operator1` and some commission percentage via `create_staking_contract`.
2. `operator1` calls the permissionless `set_beneficiary_for_operator` to register a separate beneficiary address for commission payouts [4](#0-3) .
3. After rewards accrue, commission is requested (by staker/operator/beneficiary) via `request_commission`, which unlocks the commission amount from the stake pool and records a distribution-pool entry keyed by `operator1`'s address (not the beneficiary) [5](#0-4) . This entry is only converted to actual coins for `operator1` once `distribute()`/`distribute_internal` runs *and* the underlying stake becomes withdrawable.
4. Before that unlocked commission is fully distributed (e.g., because the stake pool's lockup has not yet expired), the staker calls `switch_operator`/`switch_operator_with_same_commission` to move to `operator2`. This function forces a `distribute_internal` + `request_commission_internal` pass for `old_operator` first, but if the amount isn't yet withdrawable, or a new small distribution is created during the switch itself, an entry keyed by `old_operator` (i.e., `operator1`) can remain in `distribution_pool` after the switch. The whole `StakingContract` (with its `distribution_pool` intact) is then re-inserted under `new_operator` [2](#0-1) .
5. Later, anyone calls the permissionless `distribute(staker, operator2)` [6](#0-5) , which walks all shareholders in `distribution_pool`, including the stale `operator1` entry. Since `recipient (operator1) != operator (operator2)`, the beneficiary-redirect branch is skipped, and `aptos_account::deposit_coins` sends the commission directly to `operator1`'s address rather than the beneficiary `operator1` explicitly configured [7](#0-6) .

This is exactly the kind of "credits the wrong account" beneficiary-payout corruption called out in the task's required impacts: the payout is unconditionally diverted away from the operator's own configured beneficiary, silently, for anyone who happens to trigger `distribute()` after an operator switch with an outstanding unpaid commission.

### Impact Explanation
Commission funds that the operator explicitly configured to flow to a separate beneficiary account (often used to segregate a validator's hot operating key from a colder/custody payout address) are instead deposited into the operator's own (potentially less-secured, hot) account without the operator's consent for that specific payment. This is a role/beneficiary-boundary violation: the payout recipient is determined incorrectly based on the currently-active operator identity rather than per-recipient beneficiary lookup, breaking the "beneficiary must hold across epoch/operator transitions" invariant emphasized in the task's pivots.

### Likelihood Explanation
The staker (who fully controls `switch_operator` calls, an ordinary unprivileged staking action) can trigger this simply by switching operators at a moment when the previous operator has an unpaid, previously-requested commission still pending distribution (a plausible/likely real-world sequence, especially since `switch_operator` itself calls `request_commission_internal` for the old operator as part of its own logic, potentially creating exactly such a stale entry). No special privileges beyond normal staker/operator roles are required, and the final `distribute()` call is explicitly documented as permissionless ("Allow anyone to distribute...").

### Recommendation
In `distribute_internal`, resolve the beneficiary for **each recipient individually** (i.e., check `beneficiary_for_operator(recipient)` whenever `recipient` is *any* operator address that has ever earned commission in this pool, not just the currently passed-in `operator` parameter), or track distribution-pool entries with an explicit "is this a beneficiary-eligible operator commission" flag set at `add_distribution` time so redirection survives operator switches correctly.

### Proof of Concept
Conceptual reproduction (matching the existing test scaffolding in `staking_contract.move`'s test module, e.g. `setup_staking_contract`/`test_staker_can_switch_operator_with_beneficiary`):
```
1. setup_staking_contract(staker, operator1, INITIAL_BALANCE, commission=10)
2. operator1 calls set_beneficiary_for_operator(beneficiary1)
3. join validator set, stake::end_epoch() to accrue rewards
4. request_commission(operator1, staker, operator1)   // adds distribution entry keyed operator1, unlocks commission (pending_inactive)
5. immediately (before lockup expiry) call switch_operator(staker, operator1, operator2, new_commission)
   -> distribute_internal inside switch_operator can only withdraw whatever is already `inactive`;
      the just-unlocked pending_inactive commission from step 4 is NOT yet inactive and remains
      as an unresolved distribution-pool entry keyed operator1 that survives the re-key to operator2.
6. stake::fast_forward_to_unlock(pool_address)   // lockup expires, pending_inactive -> inactive
7. anyone calls distribute(staker, operator2)
   -> distribute_internal iterates distribution_pool, finds recipient == operator1,
      operator parameter == operator2, so recipient stays operator1 (raw address)
      instead of beneficiary1.
8. Assert: coin::balance<AptosCoin>(beneficiary1) unchanged; coin::balance<AptosCoin>(operator1) increased by the stale commission.
```
Note: I was unable to fully confirm from the indexed portion of `stake.move` the exact semantics of `withdraw_with_cap`/lockup-expiry interaction for `pending_inactive` stake at the precise moment of `switch_operator`, so step 5's exact timing (whether the stale entry is created during the switch itself vs. simply surviving an already-unlocked-but-undistributed prior `request_commission`) should be validated with an actual Move unit test before treating this as fully confirmed. The core logic flaw in `distribute_internal`'s recipient-vs-operator comparison, however, is directly confirmed by reading the source.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L637-661)
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
