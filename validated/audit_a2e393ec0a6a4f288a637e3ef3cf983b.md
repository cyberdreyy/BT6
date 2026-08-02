Based on the code I reviewed in `staking_contract.move`, this is a legitimate finding, though the exact mechanism differs from the submitted proof idea (which suggested "dust" misattribution — the real bug is in commission re-taxation of the old operator's own settled shares).

### Title
Post-`switch_operator` commission re-taxation misattributes old operator's settled commission shares to the new operator - ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Summary
`switch_operator` reuses the same `distribution_pool` (a `pool_u64::Pool`) across the operator boundary. `update_distribution_pool` exempts only the *current* `operator` address from having their shares re-taxed as "unpaid commission." Once `switch_operator` changes `staking_contract`'s effective operator, the old operator's own already-earned, unredeemed commission shares that remain in the shared pool are no longer exempt, and any further reward compounding on those shares (which naturally occurs while stake sits `pending_inactive`) gets skimmed as commission and transferred to the new operator.

### Finding Description
`switch_operator` at [1](#0-0)  removes the `StakingContract` (including its `distribution_pool`) from the map under `old_operator`, calls `distribute_internal` then `request_commission_internal(old_operator, ...)` to settle old commission, then reinstalls the *same* `StakingContract` struct under `new_operator` with the new commission rate.

`request_commission_internal` unlocks the freshly computed commission via `stake::unlock_with_cap` and records it in the distribution pool via `add_distribution(operator, staking_contract, operator, commission_amount)` [2](#0-1) . Because this commission amount was just unlocked (not yet inactive), it will remain `pending_inactive` and therefore continues to compound rewards for a full lockup cycle before it is redeemable, per the module's own share-price model described in its header comment [3](#0-2) .

The core defect is in `update_distribution_pool`: it exempts a shareholder from commission-taxation only if `shareholder != operator` fails, i.e. only if `shareholder == operator` (the address passed into the function) [4](#0-3) . Before a switch, the old operator's own commission shares are correctly exempt because `shareholder == old_operator == operator` at the time. After `switch_operator` completes, the pool is reused under `new_operator`; any subsequent call to `update_distribution_pool` (triggered by `request_commission`, `unlock_stake`, `distribute`, or `update_commision` under the new operator) passes `operator = new_operator`. Now `old_operator`'s residual shares satisfy `shareholder != operator`, so any appreciation of those shares (from reward compounding on the still-locked pending_inactive stake) is computed as "unpaid commission" and transferred via `pool_u64::transfer_shares` from `old_operator` to `new_operator` — i.e., the new operator is paid commission out of value that was already fully earned and settled to the old operator before the switch.

### Impact Explanation
This corrupts operator commission accounting across the switch boundary: value that legitimately belongs to the old operator's beneficiary is silently transferred to the new operator's beneficiary on every subsequent pool-value update, without any action from or consent of the old operator. This falls squarely under the "Operator commission ... corruption that credits the wrong account" impact category. The magnitude is proportional to reward compounding on the residual commission amount over the remaining lockup period, and repeats on every triggering call until the old operator's shares are fully redeemed via `distribute`.

### Likelihood Explanation
This triggers under entirely ordinary, permitted usage — a staker calling `switch_operator` while the old operator has an outstanding unredeemed commission distribution (a common occurrence, since commission requested near lockup expiry won't be inactive yet). No privileged role or attacker-controlled pool ownership is required beyond the staker's own authority over their own contract, which they already legitimately hold. The old operator becomes a passive victim.

### Recommendation
`update_distribution_pool` should track exemption from re-taxation per shareholder based on whether that shareholder's shares represent commission already paid to *some* operator (e.g., tag distribution entries as "commission" vs. "principal/withdrawal" rather than relying on `shareholder == operator`), or `switch_operator` should fully redeem/flush all of `old_operator`'s shares (not just call `distribute_internal`, which only redistributes already-inactive funds) before reassigning the `StakingContract` to `new_operator`, ensuring no residual old-operator shares remain in a pool that will later be taxed under a different operator identity.

### Proof of Concept
1. Staker creates a staking contract with `operator_A`, commission 10%.
2. Stake pool accrues rewards; `operator_A` calls `request_commission` — this unlocks commission and adds a distribution entry for `operator_A` in `distribution_pool` while it's still `pending_inactive` (not yet withdrawable).
3. Before this commission becomes fully inactive/withdrawable, staker calls `switch_operator(staker, operator_A, operator_B, new_commission_percentage)`. `distribute_internal` pays out only what's currently inactive (likely nothing new here since the commission is still pending_inactive), `request_commission_internal(operator_A, ...)` settles any final active-stake commission, then the contract (with `operator_A`'s pending_inactive commission shares still in `distribution_pool`) is moved to be keyed by `operator_B`.
4. Stake pool continues to compound rewards on the pending_inactive amount. `operator_B` later calls `request_commission` (or staker calls `unlock_stake`), triggering `update_distribution_pool` with `operator = operator_B`.
5. Assert: `pending_attribution_snapshot(staker, operator_B, operator_A)` after step 4 is *less* than it was right after step 3, and the difference has been transferred to `operator_B`'s shares — demonstrating that `operator_A`'s already-settled commission was partially redirected to `operator_B` purely due to the operator switch, violating the expectation that old operator's post-switch pending attribution remains fully redeemable to the old operator's beneficiary.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1-26)
```text
/// Allow stakers and operators to enter a staking contract with reward sharing.
/// The main accounting logic in a staking contract consists of 2 parts:
/// 1. Tracks how much commission needs to be paid out to the operator. This is tracked with an increasing principal
/// amount that's updated every time the operator requests commission, the staker withdraws funds, or the staker
/// switches operators.
/// 2. Distributions of funds to operators (commissions) and stakers (stake withdrawals) use the shares model provided
/// by the pool_u64 to track shares that increase in price as the stake pool accumulates rewards.
///
/// Example flow:
/// 1. A staker creates a staking contract with an operator by calling create_staking_contract() with 100 coins of
/// initial stake and commission = 10%. This means the operator will receive 10% of any accumulated rewards. A new stake
/// pool will be created and hosted in a separate account that's controlled by the staking contract.
/// 2. The operator sets up a validator node and, once ready, joins the validator set by calling stake::join_validator_set
/// 3. After some time, the stake pool gains rewards and now has 150 coins.
/// 4. Operator can now call request_commission. 10% of (150 - 100) = 5 coins will be unlocked from the stake pool. The
/// staker's principal is now updated from 100 to 145 (150 coins - 5 coins of commission). The pending distribution pool
/// has 5 coins total and the operator owns all 5 shares of it.
/// 5. Some more time has passed. The pool now has 50 more coins in rewards and a total balance of 195. The operator
/// calls request_commission again. Since the previous 5 coins have now become withdrawable, it'll be deposited into the
/// operator's account first. Their new commission will be 10% of (195 coins - 145 principal) = 5 coins. Principal is
/// updated to be 190 (195 - 5). Pending distribution pool has 5 coins and operator owns all 5 shares.
/// 6. Staker calls unlock_stake to unlock 50 coins of stake, which gets added to the pending distribution pool. Based
/// on shares math, staker will be owning 50 shares and operator still owns 5 shares of the 55-coin pending distribution
/// pool.
/// 7. Some time passes and the 55 coins become fully withdrawable from the stake pool. Due to accumulated rewards, the
/// 55 coins become 70 coins. Calling distribute() distributes 6 coins to the operator and 64 coins to the validator.
```

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L783-805)
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
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1010-1036)
```text
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
```
