## Finding: Confirmed

I traced the code path directly and confirmed a real beneficiary-redirection bug (distinct in mechanism from, but validating the underlying concern of, the submitted question) in `staking_contract::switch_operator` / `switch_operator_with_same_commission`.

### Title
Commission queued during operator switch bypasses old operator's beneficiary on later distribution — ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Summary
Any unprivileged staker calling `switch_operator_with_same_commission`/`switch_operator` on their own `staking_contract` — a normal, permitted action — causes any commission generated at switch time to later be paid directly to the old operator's raw address instead of the old operator's registered beneficiary, because `distribute_internal`'s beneficiary-redirect check compares the distribution-pool shareholder address against the *current* `operator` parameter, which changes identity (to the new operator) after the switch while the queued shareholder entry still uses the old operator's address.

### Finding Description
`switch_operator` performs, in order:
1. `distribute_internal(staker_address, old_operator, &mut staking_contract)` — pays out any already-inactive stake, correctly resolving `old_operator`'s beneficiary since the `operator` parameter still equals `old_operator` at this point. [1](#0-0) 
2. `request_commission_internal(old_operator, &mut staking_contract)` — computes newly accrued commission and calls `add_distribution(operator, staking_contract, operator, commission_amount)` with `operator = old_operator`, which does `distribution_pool.buy_in(old_operator, commission_amount)` and `stake::unlock_with_cap(...)`. This only unlocks the stake (moves it to `pending_inactive`); it is not yet withdrawable/distributed. [2](#0-1) 
3. `stake::set_operator_with_cap(&staking_contract.owner_cap, new_operator)` and the `StakingContract` (including its `distribution_pool`, which still has a shareholder entry keyed by `old_operator`) is moved to be stored under the `new_operator` key in the `Store.staking_contracts` map. [3](#0-2) 

Since the `Store` map no longer contains an entry for `old_operator` (it was `remove`d), any future call to `distribute(staker, operator)` must use `new_operator` as the key: [4](#0-3) 

Inside `distribute_internal`, the beneficiary redirect is:
```
if (recipient == operator) {
    recipient = beneficiary_for_operator(operator);
};
```
where `operator` is the function's *current-call* parameter (now `new_operator`), and `recipient` is the shareholder pulled from the pool (still `old_operator`, from the pre-switch `request_commission_internal` call). Because `old_operator != new_operator`, the redirect never triggers, and the coins are deposited directly to the raw `old_operator` address instead of `beneficiary_for_operator(old_operator)`. [5](#0-4) 

### Impact Explanation
This is a share-accounting / beneficiary-payout corruption that credits the wrong account: any operator who has configured a beneficiary via `set_beneficiary_for_operator` (e.g., pointing commission to a multisig, revenue-split contract, or exchange custody address) will have the commission tranche generated at the moment of an operator switch silently misrouted to their raw operator address rather than their configured beneficiary — a value redirection the beneficiary did not consent to and cannot prevent, triggered entirely by an unprivileged staker action on their own pool. This falls squarely under the allowed impact "Operator commission, beneficiary payout, or share-accounting corruption that credits the wrong account."

### Likelihood Explanation
High: this occurs on essentially every `switch_operator`/`switch_operator_with_same_commission` call where the old operator has any accrued, unrequested commission and has set a distinct beneficiary — both are normal, unprivileged conditions (staker rotating operators, operator using a beneficiary payout address is a supported and documented feature via `set_beneficiary_for_operator`). No special timing or race condition is needed beyond the ordinary unlock/lockup delay that already exists in the protocol.

### Recommendation
In `distribute_internal`, resolve the beneficiary using the shareholder's own address as the operator identity for beneficiary lookup (i.e., check `beneficiary_for_operator` keyed on `recipient` whenever `recipient` corresponds to an operator-type distribution entry) rather than comparing `recipient` to the transient `operator` parameter that can point to a different address than when the distribution entry was created. Alternatively, resolve and freeze the beneficiary address at the time `add_distribution` records the commission entry (in `request_commission_internal`), so later payout uses the beneficiary known at accrual time regardless of subsequent operator switches.

### Proof of Concept
1. `staker` creates a staking contract with `operator_1`, commission 10%.
2. `operator_1` calls `set_beneficiary_for_operator(operator_1, beneficiary_addr)`. [6](#0-5) 
3. Advance an epoch so rewards (and thus pending commission) accrue but are not yet requested.
4. `staker` calls `switch_operator_with_same_commission(staker, operator_1, operator_2)` — this internally calls `request_commission_internal`, queuing commission under shareholder `operator_1` in the (now `operator_2`-keyed) `distribution_pool`.
5. Fast-forward past the lockup (`stake::fast_forward_to_unlock`).
6. Call `distribute(staker_address, operator_2)` (the only valid key now).
7. Assert: `coin::balance<AptosCoin>(beneficiary_addr)` did **not** increase by the queued commission, while `coin::balance<AptosCoin>(operator_1_address)` did — demonstrating the beneficiary redirect was silently skipped and funds landed at the raw `operator_1` address instead of the configured beneficiary.

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L783-789)
```text
        let (_, staking_contract) = staking_contracts.remove(&old_operator);
        // Force distribution of any already inactive stake.
        distribute_internal(
            staker_address,
            old_operator,
            &mut staking_contract,
        );
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L798-804)
```text
        // Update the staking contract's commission rate and stake pool's operator.
        stake::set_operator_with_cap(&staking_contract.owner_cap, new_operator);
        staking_contract.commission_percentage = new_commission_percentage;

        let pool_address = staking_contract.pool_address;
        staking_contracts.add(new_operator, staking_contract);
        emit(SwitchOperator { pool_address, old_operator, new_operator });
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L810-840)
```text
    public entry fun set_beneficiary_for_operator(






























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
