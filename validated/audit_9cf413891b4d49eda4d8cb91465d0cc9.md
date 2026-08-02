No vulnerability found for this question.

**Analysis:**

The premise requires an address collision between an ordinary delegator and `beneficiary_for_operator(operator)`. That collision can only be created by the operator calling `set_beneficiary_for_operator`, which is a privileged action gated to the operator's own signer [1](#0-0) . An unprivileged delegator cannot set themselves as the beneficiary or force the operator to designate them as such; they can at most already *be* the address the operator chose.

Even when such a collision exists, the accounting is not an "exploit" — it's the intended design. Commission shares are bought into the exact same `pool.active_shares`/`pending_inactive` `pool_u64::Pool` under the beneficiary's address via `buy_in_active_shares`/`buy_in_pending_inactive_shares`, which is the identical mechanism used for a regular delegator's stake [2](#0-1) . Shares in a `pool_u64::Pool` are fungible per-address balances; there is no separate ledger distinguishing "delegated principal" from "commission" once bought in under the same shareholder key. `withdraw`/`withdraw_internal` simply redeems whatever balance exists for `delegator_address`, using `pending_withdrawal_exists` and `redeem_inactive_shares` keyed on that same address [3](#0-2) .

This commingling is explicitly acknowledged and expected elsewhere in the module: `get_stake` adds `commission_active`/`commission_pending_inactive` into the reported balance specifically when `delegator_address == beneficiary_for_operator(get_operator(pool_address))` [4](#0-3) . The framework test `test_set_beneficiary_for_operator` demonstrates the intended flow: once the operator designates a beneficiary, that beneficiary address legitimately unlocks/withdraws the commission stake via the same `unlock`/`withdraw` entrypoints as any delegator [5](#0-4) .

Since the "collision" can only arise from a privileged operator decision (choosing that beneficiary address), and the resulting behavior — that address withdrawing its full combined balance including commission — is the documented, intended semantics rather than a role-boundary violation, this does not meet the review's bar for an unprivileged-input vulnerability.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L669-681)
```text
        // should also include commission rewards in case of the operator account
        // operator rewards are actually used to buy shares which is introducing
        // some imprecision (received stake would be slightly less)
        // but adding rewards onto the existing stake is still a good approximation
        if (delegator_address == beneficiary_for_operator(get_operator(pool_address))) {
            active += commission_active;
            // in-flight pending_inactive commission can coexist with already inactive withdrawal
            if (lockup_cycle_ended) {
                inactive += commission_pending_inactive
            } else {
                pending_inactive += commission_pending_inactive
            }
        };
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1272-1291)
```text
    public entry fun set_beneficiary_for_operator(
        operator: &signer,
        new_beneficiary: address
    ) acquires BeneficiaryForOperator {
        // The beneficiay address of an operator is stored under the operator's address.
        // So, the operator does not need to be validated with respect to a staking pool.
        let operator_addr = signer::address_of(operator);
        let old_beneficiary = beneficiary_for_operator(operator_addr);
        if (exists<BeneficiaryForOperator>(operator_addr)) {
            borrow_global_mut<BeneficiaryForOperator>(operator_addr).beneficiary_for_operator = new_beneficiary;
        } else {
            move_to(operator, BeneficiaryForOperator { beneficiary_for_operator: new_beneficiary });
        };

        emit(SetBeneficiaryForOperator {
            operator: operator_addr,
            old_beneficiary,
            new_beneficiary,
        });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1625-1671)
```text
    fun withdraw_internal(
        pool: &mut DelegationPool,
        delegator_address: address,
        amount: u64
    ) acquires GovernanceRecords {
        // TODO: recycle storage when a delegator fully exits the delegation pool.
        // short-circuit if amount to withdraw is 0 so no event is emitted
        if (amount == 0) { return };

        let pool_address = get_pool_address(pool);
        let (withdrawal_exists, withdrawal_olc) = pending_withdrawal_exists(pool, delegator_address);
        // exit if no withdrawal or (it is pending and cannot withdraw pending_inactive stake from stake pool)
        if (!(
            withdrawal_exists &&
                (withdrawal_olc.index < pool.observed_lockup_cycle.index || can_withdraw_pending_inactive(pool_address))
        )) { return };

        if (withdrawal_olc.index == pool.observed_lockup_cycle.index) {
            amount = coins_to_redeem_to_ensure_min_stake(
                pending_inactive_shares_pool(pool),
                delegator_address,
                amount,
            )
        };
        amount = redeem_inactive_shares(pool, delegator_address, amount, withdrawal_olc);

        let stake_pool_owner = &retrieve_stake_pool_owner(pool);
        // stake pool will inactivate entire pending_inactive stake at `stake::withdraw` to make it withdrawable
        // however, bypassing the inactivation of excess stake (inactivated but not withdrawn) ensures
        // the OLC is not advanced indefinitely on `unlock`-`withdraw` paired calls
        if (can_withdraw_pending_inactive(pool_address)) {
            // get excess stake before being entirely inactivated
            let (_, _, _, pending_inactive) = stake::get_stake(pool_address);
            if (withdrawal_olc.index == pool.observed_lockup_cycle.index) {
                // `amount` less excess if withdrawing pending_inactive stake
                pending_inactive -= amount
            };
            // escape excess stake from inactivation
            stake::reactivate_stake(stake_pool_owner, pending_inactive);
            stake::withdraw(stake_pool_owner, amount);
            // restore excess stake to the pending_inactive state
            stake::unlock(stake_pool_owner, pending_inactive);
        } else {
            // no excess stake if `stake::withdraw` does not inactivate at all
            stake::withdraw(stake_pool_owner, amount);
        };
        aptos_account::transfer(stake_pool_owner, delegator_address, amount);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1720-1738)
```text
    /// Buy shares into the active pool on behalf of delegator `shareholder` who
    /// deposited `coins_amount`. This function doesn't make any coin transfer.
    fun buy_in_active_shares(
        pool: &mut DelegationPool,
        shareholder: address,
        coins_amount: u64,
    ): u128 acquires GovernanceRecords {
        let new_shares = pool.active_shares.amount_to_shares(coins_amount);
        // No need to buy 0 shares.
        if (new_shares == 0) { return 0 };

        // Always update governance records before any change to the shares pool.
        let pool_address = get_pool_address(pool);
        if (partial_governance_voting_enabled(pool_address)) {
            update_governance_records_for_buy_in_active_shares(pool, pool_address, new_shares, shareholder);
        };

        pool.active_shares.buy_in(shareholder, coins_amount);
        new_shares
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L3767-3780)
```text
        withdraw(operator1, pool_address, ONE_APT);
        assert!(coin::balance<AptosCoin>(operator1_address) == ONE_APT - 1, 0);

        set_beneficiary_for_operator(operator1, beneficiary_address);
        assert!(beneficiary_for_operator(operator1_address) == beneficiary_address, 0);
        end_aptos_epoch();

        unlock(beneficiary, pool_address, ONE_APT);
        timestamp::fast_forward_seconds(LOCKUP_CYCLE_SECONDS);
        end_aptos_epoch();

        withdraw(beneficiary, pool_address, ONE_APT);
        assert!(coin::balance<AptosCoin>(beneficiary_address) == ONE_APT - 1, 0);
        assert!(coin::balance<AptosCoin>(operator1_address) == ONE_APT - 1, 0);
```
