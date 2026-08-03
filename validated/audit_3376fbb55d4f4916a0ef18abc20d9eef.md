## Valid Finding

The described vulnerability is real. `distribute` is explicitly documented as callable by anyone (`staker` or `operator` restriction is not required), and its call path into `update_distribution_pool` treats a former operator's own unpaid commission shares as ordinary "shareholder" shares once `switch_operator` reassigns the `StakingContract` to the new operator's key — leading to commission being skimmed from the old operator's already-finalized entitlement and handed to the new operator.

### Title
Stale operator commission shares are incorrectly re-taxed and redirected to the new operator after `switch_operator` - ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Summary
`switch_operator` finalizes the old operator's earned commission by calling `request_commission_internal`, which calls `add_distribution` to buy the old operator into the `distribution_pool` for the exact commission amount owed at that time [1](#0-0) . The `StakingContract` (and its `distribution_pool`) is then re-keyed under `new_operator` [2](#0-1) . The old operator's shares remain unredeemed in that same pool until the pending_inactive stake finishes unlocking.

Any unprivileged account can then call `distribute(staker, new_operator)` — the doc comment explicitly states it "does not need to be restricted to just the staker or operator" [3](#0-2) . `distribute_internal` calls `update_distribution_pool` with `operator = new_operator` and `updated_total_coins` equal to the freshly withdrawn inactive+pending_inactive balance [4](#0-3) . Because pending_inactive/inactive stake keeps earning validator rewards until fully withdrawn, this balance grows between the time of the switch and the time `distribute` is finally called.

`update_distribution_pool` charges commission on that growth to every shareholder **except** the `operator` parameter passed in, transferring the skimmed shares to that operator [5](#0-4) . Since the old operator is not equal to `new_operator`, the old operator's shares — which represent an already-finalized, already-commission-adjusted payout — are treated as ordinary principal and taxed again, with the proceeds going to the new operator who did nothing to earn them.

### Finding Description
The exclusion `if (shareholder != operator)` in `update_distribution_pool` exists specifically so that an operator's own commission shares are never re-taxed. This invariant silently breaks across a `switch_operator` boundary because the "operator" identity used for that exclusion is always the *current* operator field/key, never the address that actually earned the shares. The old operator's shares are bought in via `buy_in` at switch time, so their exchange rate is initially fair [6](#0-5) , but any subsequent `update_total_coins` growth (driven by real reward accrual on the pending_inactive stake, not by new deposits) is captured by `update_distribution_pool`'s loop and reassigned to whichever address is the operator at call time [7](#0-6) .

Any account can trigger the harmful re-sync merely by calling `distribute(staker, new_operator)` after enough epochs have passed for the pending_inactive amount to grow, corrupting the accounting invariant that the old operator's commission was fixed at switch time.

### Impact Explanation
The old operator's finalized, already-commissioned payout is partially redirected to the new operator, deflating what the old operator ultimately receives and inflating the new operator's take without any corresponding work or entitlement. This is a real value-redirection across an operator-role boundary, affecting commission and reward accounting invariants that must hold across epoch/lockup transitions.

### Likelihood Explanation
High. `distribute` has no access-control restriction by design, `switch_operator` is a standard, frequently-used staker operation, and the only precondition is that some epochs pass between the switch and the `distribute` call (or any other trigger of `add_distribution`/`distribute_internal` on the same pool) — a normal occurrence given lockup periods.

### Recommendation
`update_distribution_pool` (and its callers `add_distribution`/`distribute_internal`) should not use the *current* operator address as the sole basis for excluding commission-shares from re-taxation. Either: (a) pay out/settle the old operator's queued shares to actual coins at switch time instead of leaving them as pool shares subject to future re-syncs, or (b) track, per shareholder, whether their shares represent "principal" (subject to commission on growth) versus "commission already earned" (exempt from further commission regardless of which address is currently the operator).

### Proof of Concept
1. Staker creates a staking contract with `old_operator` at commission `X%`.
2. After some active-stake reward accrual, staker calls `switch_operator(old_operator, new_operator, Y%)`. This forces `distribute_internal` (draining any already-inactive/pending_inactive funds) then `request_commission_internal(old_operator, ...)`, which computes commission `C1` and calls `add_distribution(old_operator, ..., old_operator, C1)`, buying old_operator into the (now empty) `distribution_pool`, and issues `stake::unlock_with_cap(C1, ...)` so `C1` becomes pending_inactive.
3. Several epochs pass; the pending_inactive `C1` grows to `C1' > C1` from ordinary validator rewards.
4. Any unprivileged account calls `distribute(staker_address, new_operator)`. `distribute_internal` withdraws `C1'`, and `update_distribution_pool` computes `unpaid_commission = (C1' - C1) * Y% / 100` and transfers that many shares from `old_operator` to `new_operator` before the payout loop runs.
5. Assert: `old_operator` receives `C1' - (C1' - C1) * Y% / 100`, not `C1'`, and `new_operator` receives an extra `(C1' - C1) * Y% / 100` despite having no claim to rewards accrued on `old_operator`'s already-finalized commission.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L791-805)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L838-853)
```text
    }

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L868-886)
```text
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

        let distribution_pool = &mut staking_contract.distribution_pool;
        update_distribution_pool(
            distribution_pool,
            distribution_amount,
            operator,
            staking_contract.commission_percentage
        );
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

**File:** aptos-move/framework/aptos-stdlib/sources/pool_u64.move (L129-131)
```text
    public fun update_total_coins(self: &mut Pool, new_total_coins: u64) {
        self.total_coins = new_total_coins;
    }
```

**File:** aptos-move/framework/aptos-stdlib/sources/pool_u64.move (L134-145)
```text
    public fun buy_in(self: &mut Pool, shareholder: address, coins_amount: u64): u64 {
        if (coins_amount == 0) return 0;

        let new_shares = self.amount_to_shares(coins_amount);
        assert!(MAX_U64 - self.total_coins >= coins_amount, error::invalid_argument(EPOOL_TOTAL_COINS_OVERFLOW));
        assert!(MAX_U64 - self.total_shares >= new_shares, error::invalid_argument(EPOOL_TOTAL_COINS_OVERFLOW));

        self.total_coins += coins_amount;
        self.total_shares += new_shares;
        self.add_shares(shareholder, new_shares);
        new_shares
    }
```
