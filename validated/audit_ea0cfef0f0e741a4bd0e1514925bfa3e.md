## Title
Operator switch mid-lockup causes distribution-pool commission double-charging that redirects value from old operator to new operator - (File: aptos-move/framework/aptos-framework/sources/staking_contract.move)

### Summary
`switch_operator` reassigns a `StakingContract` (including its `distribution_pool`) from `old_operator` to `new_operator` while the pool can still contain unpaid, pending-inactive commission shares that were just credited to `old_operator` via `request_commission_internal`. Because `update_distribution_pool` exempts shares from taxation only when `shareholder == operator` (the *current* operator param passed by the caller), once the contract is reassigned, any future growth in the pending-inactive balance causes `old_operator`'s already-earned (but not-yet-distributed) commission shares to be treated as ordinary staker shares and taxed again, with the extra cut transferred to `new_operator`.

### Finding Description
`switch_operator` [1](#0-0)  performs, in order:
1. `distribute_internal` – pays out anything already fully `inactive`.
2. `request_commission_internal` – computes new commission owed to `old_operator` based on rewards accrued since the last principal update, adds a distribution entry keyed to `old_operator`'s address in the same `distribution_pool` [2](#0-1) , and calls `stake::unlock_with_cap`, moving that commission from `active`/`pending_active` into `pending_inactive` (not yet withdrawable).
3. Sets the pool's operator to `new_operator`, updates `commission_percentage`, and re-inserts the *same* `StakingContract` struct (same `distribution_pool`) under the `new_operator` key.

The `distribution_pool` still has shares recorded for `old_operator`'s address for the commission requested in step 2, because that amount is only `pending_inactive`, not yet `inactive`, so `distribute_internal` in step 1 (which ran before the new request) does not pay it out.

`update_distribution_pool` [3](#0-2)  is invoked on every subsequent `add_distribution` or `distribute_internal` call (i.e., any future `request_commission`, `unlock_stake`, or `distribute` call on the contract). It taxes the *growth* in value of every shareholder's shares except the one matching the `operator` argument passed in, transferring the cut to that `operator`. After the switch, all such calls pass `operator = new_operator` (since the map key is now `new_operator`). This means `old_operator`'s previously-recorded, already-vested commission shares are no longer exempt (`shareholder != operator` now evaluates true for `old_operator`), so any reward growth on that pending-inactive commission balance during the remainder of the lockup period gets taxed at `commission_percentage` and the resulting shares are transferred to `new_operator` via `pool_u64::transfer_shares` [4](#0-3) .

### Impact Explanation
This redirects value that rightfully belongs to `old_operator` (rewards accrued on their already-requested, unpaid commission) to `new_operator`, without either party's consent, purely as a side effect of a staker-initiated `switch_operator` call. It falls under "Operator commission ... share-accounting corruption that credits the wrong account," per the stated impact scope. The magnitude is bounded to the reward growth on the outstanding pending-inactive commission balance during the remaining lockup window (not the full commission amount), which limits severity but does not eliminate the misdirection.

### Likelihood Explanation
The path is fully triggerable by the staker (owner) alone, using only their own staking contract, and requires no privileged role beyond what a staker legitimately controls: call `request_commission`/`unlock_stake` to build up unpaid pending-inactive shares for `old_operator`, then call `switch_operator` before that pending amount is distributed, then trigger any subsequent `add_distribution`/`distribute_internal` (e.g., `new_operator` calling `request_commission`, or the staker calling `unlock_stake`/`distribute`) before the lockup fully elapses and pays it out. This is a normal sequence of otherwise-permitted operations, making it moderately likely to occur in practice (intentionally or accidentally) whenever operators are switched mid-lockup with unpaid commission outstanding.

### Recommendation
Before reassigning the `StakingContract` to `new_operator` in `switch_operator`, ensure all of `old_operator`'s outstanding shares in `distribution_pool` are fully resolved/paid or otherwise excluded from future commission taxation — e.g., by forcing a full settlement (waiting for pending_inactive to become inactive is not always possible synchronously, so an alternative is to track exempted shareholders per-operator-at-time-of-accrual rather than relying solely on the current `operator` parameter equality check in `update_distribution_pool`).

### Proof of Concept
Cannot fully verify with static review alone since the exact numeric flow of `pool_u64` share pricing and the timing of pending_inactive reward accrual (whether meaningful rewards actually accrue on `pending_inactive` balances within the same lockup cycle before it becomes `inactive`) were not independently confirmed against `stake.move`'s reward-distribution logic within this review's tool budget. A concrete Move unit test to confirm this would:
1. Create `staking_contract(staker, old_operator, 100 coins, 10% commission)`.
2. Advance epochs to accrue rewards; call `request_commission` for `old_operator`, creating unpaid pending-inactive shares owned by `old_operator` in `distribution_pool`.
3. Call `switch_operator(staker, old_operator, new_operator, new_commission_percentage)` before those shares become `inactive`.
4. Advance further epochs (still within the same lockup cycle) so `pending_inactive` balance grows via rewards.
5. Trigger `request_commission`/`unlock_stake` by `new_operator`/`staker`, causing `update_distribution_pool` to run with `operator = new_operator`.
6. Assert that `old_operator`'s recorded share value has decreased below what was recorded at switch time, and that `new_operator`'s shares increased correspondingly — confirming value was redirected from `old_operator` to `new_operator`. [1](#0-0) [3](#0-2)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L637-674)
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

        let pool_address = staking_contract.pool_address;
        emit(
            RequestCommission {
                operator,
                pool_address,
                accumulated_rewards,
                commission_amount
            }
        );

        commission_amount
    }
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1001-1039)
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
    }
```
