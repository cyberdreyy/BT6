## Finding: Valid

The exact "new operator's beneficiary receives old operator's commission" outcome hypothesized in the question does not occur, but investigation of `distribute_internal` uncovered a related, real misrouting bug in the opposite direction: after `switch_operator`, commission owed to the **old operator's beneficiary** is instead paid to the **old operator's raw address**, silently bypassing the beneficiary redirection — and any unprivileged account can trigger the faulty payout via the permissionless `distribute` entry function.

### Title
Post-`switch_operator` commission distributions bypass `beneficiary_for_operator` and pay the raw old-operator address instead - ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Summary
`distribute_internal` decides whether to redirect a payout to a beneficiary by comparing the stored distribution-pool `recipient` address to the `operator` argument passed into that specific call: `if (recipient == operator) { recipient = beneficiary_for_operator(operator) }` [1](#0-0) . The `operator` argument used at call time is always the *current* map key under which the `StakingContract` resource lives in the staker's `Store` (i.e. whichever operator currently controls the contract), not the operator identity that was recorded when a distribution share was created.

### Finding Description
`switch_operator` removes the `StakingContract` from the map keyed by `old_operator`, settles any already-inactive funds via `distribute_internal(staker, old_operator, ...)`, and then calls `request_commission_internal(old_operator, ...)`, which computes commission on rewards accrued up to that moment and records a **new** distribution-pool share for `recipient = old_operator` via `add_distribution` [2](#0-1) . This freshly-added commission is only *unlocked* (`stake::unlock_with_cap`), not yet withdrawable, so it remains a pending shareholder entry in `staking_contract.distribution_pool` keyed to the `old_operator` address [3](#0-2) .

Immediately after, `switch_operator` calls `stake::set_operator_with_cap(&staking_contract.owner_cap, new_operator)` and re-inserts the same `StakingContract` object into the `Store` map under the `new_operator` key [4](#0-3) .

Once the stake pool's lockup expires and its pending_inactive stake becomes inactive, anyone can call the permissionless `distribute(staker, new_operator)` entry function [5](#0-4) . This invokes `distribute_internal(staker, new_operator, staking_contract)` — note the `operator` parameter is now `new_operator`. When it processes the still-pending distribution-pool entry with `recipient == old_operator`, the check `recipient == operator` (`old_operator == new_operator`) is false, so the beneficiary lookup `beneficiary_for_operator(operator)` is never applied to that entry. The coins are deposited directly to the raw `old_operator` address instead of `beneficiary_for_operator(old_operator)` [6](#0-5) .

This directly contradicts the invariant documented at the `pending_attribution_snapshot` view function: "Operator commission is recorded under the operator address in the distribution_pool, but may ultimately be paid to a separate beneficiary address during distribution... In operator-switch scenarios, the previous operator may still have a non-zero pending attribution" [7](#0-6) . The code comment on `set_beneficiary_for_operator` also assumes distribution always honors beneficiaries: "Any existing unpaid commission rewards will be paid to the new beneficiary" [8](#0-7) , but this only holds when the operator identity used to key the pool at distribution time matches the one under which the share was created — which `switch_operator` breaks.

### Impact Explanation
This is a beneficiary-payout corruption that credits the wrong account: commission that an old operator explicitly assigned to a `beneficiary_for_operator` (e.g. a custodial/segregated payout account) is instead sent to the operator's own raw address once that operator has been switched out, without any consent or action from the operator or the recipient designation being respected. In setups where the beneficiary account is the only account with legal/technical claim to commission proceeds (the operator key may be a hot/automation key with no custody rights), this results in fund misdirection into an unintended account. It matches the "Required Impacts" category of "Operator commission, beneficiary payout, or share-accounting corruption that credits the wrong account."

### Likelihood Explanation
The trigger requires no special privilege: `distribute` is explicitly documented as callable by "anyone" [9](#0-8) . The only precondition is a normal, legitimate `switch_operator` call by the staker (an in-role action, not privilege escalation) that leaves a non-zero pending commission distribution for the outgoing operator — a common occurrence since `switch_operator` itself calls `request_commission_internal` right before reassigning the operator, guaranteeing a residual distribution-pool entry keyed to the old operator whenever there are unclaimed rewards at switch time.

### Recommendation
`distribute_internal` should resolve the beneficiary for each `recipient` based on whether that specific address is (or was) an operator with a beneficiary set, independent of the `operator` parameter passed to that particular call — e.g., by checking `exists<BeneficiaryForOperator>(recipient)` directly rather than comparing `recipient == operator`. Alternatively, `switch_operator` should immediately resolve and pay out any newly-added `old_operator` distribution to `beneficiary_for_operator(old_operator)` before or as part of the operator switch, rather than leaving a stale distribution-pool entry that will later be resolved under the wrong operator identity.

### Proof of Concept
1. Staker creates a staking contract with `operator1`, commission 10%.
2. `operator1` calls `set_beneficiary_for_operator(operator1, beneficiary1)`.
3. Rewards accrue; ensure any prior pending distributions are fully settled via `distribute` (so `assert_no_pending_distributions` holds), matching the existing test setup pattern at [10](#0-9) .
4. Let further rewards accrue (active/pending_active), then have the staker call `switch_operator(staker, operator1, operator2, new_commission_percentage)` [11](#0-10) . This internally calls `request_commission_internal(operator1, ...)`, creating a new distribution-pool share for `operator1` from rewards accrued between step 3 and step 4.
5. Fast-forward past the lockup so the newly unlocked commission becomes `inactive`.
6. Any unprivileged third-party account calls `distribute(staker_address, operator2)` (the new map key).
7. Assert: the commission is deposited to `operator1`'s raw address, not to `beneficiary1` — i.e. `coin::balance<AptosCoin>(beneficiary1)` is unchanged while `coin::balance<AptosCoin>(operator1)` increases by the commission amount, even though `beneficiary_for_operator(operator1) == beneficiary1` was set and never reset.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L383-397)
```text
    /// IMPORTANT SEMANTICS:
    /// - This function returns a SNAPSHOT of the staking contract's attribution ledger.
    ///   It reflects amounts that have been unlocked and recorded via the contract,
    ///   but NOT necessarily the stake pool's latest withdrawable balances.
    /// - The returned value does NOT automatically reflect newly unlocked stake or commission
    ///   unless the contract state has been advanced (e.g. via unlock or distribute paths).
    /// - Operator commission is recorded under the operator address in the distribution_pool,
    ///   but may ultimately be paid to a separate beneficiary address during distribution.
    ///   Call `beneficiary_for_operator(operator)` to determine the final recipient.
    ///
    /// USAGE NOTES:
    /// - To query the staker's pending amount, pass `account = staker`.
    /// - To query the operator's pending commission, pass `account = operator`.
    /// - In operator-switch scenarios, the previous operator may still have a
    ///   non-zero pending attribution; in that case, pass `account = old_operator`.
```

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L762-805)
```text
    public entry fun switch_operator(
        staker: &signer,
        old_operator: address,
        new_operator: address,
        new_commission_percentage: u64
    ) acquires Store, BeneficiaryForOperator {
        let staker_address = signer::address_of(staker);
        assert_staking_contract_exists(staker_address, old_operator);

        assert!(
            new_commission_percentage <= 100,
            error::invalid_argument(EINVALID_COMMISSION_PERCENTAGE)
        );
        // Merging two existing staking contracts is too complex as we'd need to merge two separate stake pools.
        let store = borrow_global_mut<Store>(staker_address);
        let staking_contracts = &mut store.staking_contracts;
        assert!(
            !staking_contracts.contains_key(&new_operator),
            error::invalid_state(ECANT_MERGE_STAKING_CONTRACTS)
        );

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L807-810)
```text
    /// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
    /// beneficiary. To ensures payment to the current beneficiary, one should first call `distribute` before switching
    /// the beneficiary. An operator can set one beneficiary for staking contract pools, not a separate one for each pool.
    public entry fun set_beneficiary_for_operator(
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1784-1792)
```text
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
