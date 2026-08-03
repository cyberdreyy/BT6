## Finding Assessment

The specific proof idea as literally stated—that `request_commission`'s already-computed amount gets revalued at the new rate *during the same* operator-switch call—does **not** hold. In `switch_operator` the order is: `distribute_internal` and `request_commission_internal` run (both using the **old** `commission_percentage` and old operator identity) *before* `staking_contract.commission_percentage = new_commission_percentage` is assigned [1](#0-0) . The same sequencing exists in `update_commision` [2](#0-1) . So the commission amount that accrues to the outgoing operator is computed and locked in at the rate in effect at accrual time, not the new rate — the literal PoC described would fail.

However, there is a related, genuine accounting flaw in the shared `distribution_pool` that produces the same underpayment outcome the question is chasing, just through a different trigger.

`update_distribution_pool` exempts only the address passed as `operator` from being taxed when the pool's `total_coins` baseline grows [3](#0-2) . When `switch_operator` runs, the outgoing operator's just-accrued, unredeemed commission shares remain inside the very same `Pool` (recipient key = old operator address), and the whole `StakingContract` (including that `distribution_pool`) is re-keyed under the **new** operator [4](#0-3) .

Any subsequent normal, unprivileged activity on the pool — the staker calling `unlock_stake`, or the new operator calling `request_commission` — invokes `add_distribution`, which recomputes `update_distribution_pool` using `total_distribution_amount` derived from the pool's current `pending_inactive` balance and the operator param now equal to the **new** operator [5](#0-4) . Because the old operator's address no longer matches the `operator` exemption check, their still-unpaid pending shares are treated as an ordinary shareholder's "worth growth" and are taxed at the **currently effective (new) `commission_percentage`**, with the deducted shares transferred to the new operator [6](#0-5) . This only fires if `distribute_internal` hasn't already fully emptied the pool first, which happens whenever the outgoing operator's unlocked commission is still in `pending_inactive` (lockup not yet expired) rather than already-withdrawable `inactive` stake, since `distribute_internal` only redeems what `stake::withdraw_with_cap` actually returns as real coins [7](#0-6) .

I was not able to pull the exact body of `pool_u64::buy_in`/`update_total_coins`/`shares_to_amount_with_total_coins` in this pass (search for the file returned no matching function bodies), so the precise arithmetic of how the pool's exchange rate is bumped independent of `total_shares` is inferred from call-site usage rather than directly confirmed; a Devin session with full file access should verify this before finalizing severity.

There is no existing test in `staking_contract.move` covering `switch_operator` combined with a pending, not-yet-distributed commission followed by further pool activity [8](#0-7) , and the corresponding formal spec explicitly disables verification of `update_distribution_pool`-dependent functions (`switch_operator`, `request_commission`, `unlock_stake`) with `pragma verify = false`, meaning this path is not currently proven safe [9](#0-8) .

### Title
Outgoing operator's unredeemed pending commission can be re-taxed at the new operator's commission rate and misrouted to the incoming operator - (File: aptos-move/framework/aptos-framework/sources/staking_contract.move)

### Summary
`staking_contract::switch_operator` (and the vesting-layer wrapper `vesting::update_operator`) correctly computes the outgoing operator's commission at the old rate before updating `commission_percentage` and re-keying the `StakingContract` under the new operator. But the outgoing operator's unredeemed commission shares remain in the same `distribution_pool` `Pool`. Because `update_distribution_pool` only exempts the *current* `operator` address from being taxed on pool growth, once the operator address changes, any later legitimate call (`unlock_stake` by the staker, or `request_commission` by the new operator) that grows the pool's tracked `total_coins` baseline will tax the old operator's still-parked balance at the **new** commission percentage and transfer the deducted shares to the **new** operator.

### Finding Description
`update_distribution_pool` treats any increase in a shareholder's calculated worth (driven purely by a rebased `total_coins`) as new commission-eligible income unless the shareholder equals the `operator` parameter passed in [6](#0-5) . `switch_operator` moves the `StakingContract` (and its `distribution_pool`) to a new map key without redeeming the old operator's outstanding shares first if those funds are not yet fully unlocked [4](#0-3) [10](#0-9) . Subsequent calls to `add_distribution` from `request_commission_internal`/`unlock_stake` recompute the pool's total using `stake::get_stake`'s `pending_inactive` value and the new operator/commission rate [5](#0-4) , causing the old operator's dormant shares to be incorrectly taxed and reassigned to the new operator.

### Impact Explanation
This misroutes economic value from a legitimate outgoing operator (or their beneficiary) to the incoming operator, without either party needing elevated privileges beyond the staker's normal, permitted `switch_operator`/`unlock_stake`/`request_commission` calls. This matches the required impact class: it changes who can earn/recover commission across a role-boundary transition.

### Likelihood Explanation
Requires a specific but realistic sequence: outgoing operator has an unredeemed commission request pending in `distribution_pool` (funds still in `pending_inactive`, lockup not expired), followed by operator switch, followed by any further staker/new-operator stake activity before the old distribution is paid out via `distribute()`. This is a plausible real-world validator/operator rotation scenario, not an attacker-controlled edge case requiring privileged access.

### Recommendation
Before `switch_operator` re-keys the `StakingContract`, force a full `distribute_internal` payout regardless of whether the funds are only in `pending_inactive` (or, alternatively, forbid switching operator while there is an outstanding, un-distributed balance for the current operator), and/or make `update_distribution_pool`'s exemption logic operator-identity-agnostic by tracking exemption per recorded distribution rather than by comparing against the live `operator` field.

### Proof of Concept
A Move test in `staking_contract.move`'s test module should: (1) create a staking contract with commission `C1`; (2) accrue rewards and call `request_commission` for `operator_1` (funds go to `pending_inactive`, not yet past lockup); (3) call `switch_operator` to `operator_2` with commission `C2 != C1`; (4) before calling `distribute`, trigger another `unlock_stake` or `request_commission` call that grows `pending_inactive`; (5) call `distribute` and assert `operator_1`'s final payout equals the commission computed at `C1` on the originally accrued rewards — the current code will show `operator_1` underpaid and `operator_2` overpaid relative to that expectation.

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L855-879)
```text
    /// Distribute all unlocked (inactive) funds according to distribution shares.
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

```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L937-957)
```text
    /// Add a new distribution for `recipient` and `amount` to the staking contract's distributions list.
    fun add_distribution(
        operator: address,
        staking_contract: &mut StakingContract,
        recipient: address,
        coins_amount: u64,
    ) {
        let distribution_pool = &mut staking_contract.distribution_pool;
        let (_, _, _, total_distribution_amount) =
            stake::get_stake(staking_contract.pool_address);
        update_distribution_pool(
            distribution_pool,
            total_distribution_amount,
            operator,
            staking_contract.commission_percentage
        );

        distribution_pool.buy_in(recipient, coins_amount);
        let pool_address = staking_contract.pool_address;
        emit(AddDistribution { operator, pool_address, amount: coins_amount });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1001-1038)
```text
    fun update_distribution_pool(
        distribution_pool: &mut Pool,
        updated_total_coins: u64,
        operator: address,
        commission_percentage: u64
    ) {
        // Short-circuit and do nothing if the pool's total value has not changed.
        if (distribution_pool.total_coins() == updated_total_coins) { return };

        // Charge all stakeholders (except for the operator themselves) commission on any rewards earnt relatively to the
        // previous value of the distribution pool.
        let shareholders = &distribution_pool.shareholders();
        shareholders.for_each_ref(
            |shareholder| {
                let shareholder: address = *shareholder;
                if (shareholder != operator) {
                    let shares = pool_u64::shares(distribution_pool, shareholder);
                    let previous_worth = pool_u64::balance(distribution_pool, shareholder);
                    let current_worth =
                        pool_u64::shares_to_amount_with_total_coins(
                            distribution_pool, shares, updated_total_coins
                        );
                    let unpaid_commission =
                        (current_worth - previous_worth) * commission_percentage / 100;
                    // Transfer shares from current shareholder to the operator as payment.
                    // The value of the shares should use the updated pool's total value.
                    let shares_to_transfer =
                        pool_u64::amount_to_shares_with_total_coins(
                            distribution_pool, unpaid_commission, updated_total_coins
                        );
                    pool_u64::transfer_shares(
                        distribution_pool, shareholder, operator, shares_to_transfer
                    );
                };
            }
        );

        distribution_pool.update_total_coins(updated_total_coins);
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.spec.move (L254-326)
```text
    spec update_commision(staker: &signer, operator: address, new_commission_percentage: u64) {
        // TODO: Call `distribute_internal` and could not verify `update_distribution_pool`.
        // TODO: A data invariant not hold happened here involve with 'pool_u64' #L16.
        pragma verify = false;
        let staker_address = signer::address_of(staker);
        aborts_if new_commission_percentage > 100;
        include ContractExistsAbortsIf { staker: staker_address };
    }

    /// Only staker or operator can call this.
    spec request_commission(account: &signer, staker: address, operator: address) {
        // TODO: Call `update_distribution_pool` and could not verify `update_distribution_pool`.
        // TODO: A data invariant not hold happened here involve with 'pool_u64' #L16.
        pragma verify = false;
        let account_addr = signer::address_of(account);
        include ContractExistsAbortsIf { staker };
        aborts_if account_addr != staker && account_addr != operator;
    }

    spec request_commission_internal(
        operator: address,
        staking_contract: &mut StakingContract,
    ): u64 {
        // TODO: A data invariant not hold happened here involve with 'pool_u64' #L16.
        pragma verify = false;
        include GetStakingContractAmountsAbortsIf;
    }

    /// Staking_contract exists the stacker/operator pair.
    spec unlock_rewards(staker: &signer, operator: address) {
        // TODO: Call `update_distribution_pool` and could not verify `update_distribution_pool`.
        // TODO: Set because of timeout (estimate unknown).
        pragma verify = false;
        let staker_address = signer::address_of(staker);
        let staking_contracts = global<Store>(staker_address).staking_contracts;
        let staking_contract = simple_map::spec_get(staking_contracts, operator);
        include ContractExistsAbortsIf { staker: staker_address };
    }

    spec unlock_stake(staker: &signer, operator: address, amount: u64) {
        // TODO: Call `update_distribution_pool` and could not verify `update_distribution_pool`.
        // TODO: Set because of timeout (estimate unknown).
        pragma verify = false;
        let staker_address = signer::address_of(staker);
        include ContractExistsAbortsIf { staker: staker_address };
    }

    /// Staking_contract exists the stacker/operator pair.
    spec switch_operator_with_same_commission(
        staker: &signer, old_operator: address, new_operator: address
    ) {
        // TODO: Call `update_distribution_pool` and could not verify `update_distribution_pool`.
        pragma aborts_if_is_partial;
        let staker_address = signer::address_of(staker);
        include ContractExistsAbortsIf { staker: staker_address, operator: old_operator };
    }

    /// Staking_contract exists the stacker/operator pair.
    spec switch_operator(
        staker: &signer,
        old_operator: address,
        new_operator: address,
        new_commission_percentage: u64
    ) {
        // TODO: Call `update_distribution_pool` and could not verify `update_distribution_pool`.
        // TODO: Set because of timeout (estimate unknown).
        pragma verify = false;
        let staker_address = signer::address_of(staker);
        include ContractExistsAbortsIf { staker: staker_address, operator: old_operator };
        let store = global<Store>(staker_address);
        let staking_contracts = store.staking_contracts;
        aborts_if simple_map::spec_contains_key(staking_contracts, new_operator);
    }
```
