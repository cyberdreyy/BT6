## Title
Distribution-pool commission accounting misattributes newly-added distributions as organic rewards, letting operators overcharge stakers' pending unlock balances - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract.move` tracks unpaid distributions (unlocked stake awaiting withdrawal) in a shares-based `distribution_pool`. Every time a new distribution is added — whether it's a staker's `unlock_stake` or an operator's `request_commission` — `add_distribution()` first calls `update_distribution_pool()` to "settle" reward growth on the *existing* pool balance before minting new shares for the incoming amount. That settlement step cannot distinguish organic reward growth of the existing balance from the brand-new amount about to be deposited, because both are folded into the same `updated_total_coins` figure before the new depositor's shares are bought in. As a result, any account that already holds an un-distributed balance in `distribution_pool` (e.g., a staker who previously called `unlock_stake` and hasn't yet called `distribute`) gets taxed commission on top of the *entire newly-added, unrelated distribution amount*, not just on real reward growth — and that excess commission is transferred as shares to the operator.

### Finding Description
`add_distribution()` computes the pool's new total using the stake pool's live pending-inactive balance and calls `update_distribution_pool()` before minting shares for the incoming recipient: [1](#0-0) 

`update_distribution_pool()` charges every non-operator shareholder commission on `current_worth - previous_worth`, where `current_worth` is computed using the pool's shares against the **already-updated** total coins (which already includes the amount about to be deposited for the new distribution, before that new amount receives its own shares): [2](#0-1) 

Because a shareholder's `current_worth` is `shares / total_shares * updated_total_coins`, and `updated_total_coins` already contains the newly unlocked (but not-yet-shared) amount, any pre-existing shareholder’s "worth" spikes by (approximately) their share of the freshly added, unrelated amount — even if zero real reward accrued on their existing balance. The function then transfers `unpaid_commission = diff * commission_percentage / 100` in shares from that shareholder to the operator via `pool_u64::transfer_shares`.

This is triggered whenever:
1. A staker calls `unlock_stake()`, leaving a non-zero share balance for themselves in `distribution_pool` (not yet drained by `distribute()`), per [3](#0-2) 
2. The operator later calls `request_commission()` again (permitted for staker, operator, or beneficiary — no special privilege beyond the existing operator role): [4](#0-3) 

`request_commission_internal` unlocks a fresh commission amount and calls `add_distribution(operator, staking_contract, operator, commission_amount)`, which re-triggers `update_distribution_pool` and misattributes the entire freshly unlocked `commission_amount` as "growth" spread across the staker's still-pending shares, taxing the staker on it and moving those shares to the operator.

### Impact Explanation
This is a share-accounting corruption in the operator-commission flow that credits the wrong account: value that legitimately belongs to the staker's already-unlocked (pending withdrawal) balance is silently redirected to the operator every time a new distribution (commission or a later unlock) is added while an earlier distribution is still outstanding. No consent or privileged role beyond the operator's normal ability to call `request_commission` is required, and the staker cannot prevent it except by calling `distribute()` immediately after every unlock (which is not enforced or guaranteed by the protocol). Because `staking_contract` underlies `vesting.move`'s reward/vesting flow as well (`vesting::vest`/`unlock_rewards` call into `staking_contract::unlock_stake`), the same corruption propagates to vesting shareholders' payouts, not just direct stakers.

### Likelihood Explanation
Likely to occur naturally: any staker who unlocks stake before it's fully distributed (common, since distribution requires a separate `distribute()` call and lockup expiry) and any operator who requests commission more than once during that window will trigger it. No adversarial setup is required beyond ordinary usage patterns, and an operator can proactively call `request_commission` (even for negligible amounts) whenever they observe a staker with an outstanding un-distributed balance to extract additional value.

### Recommendation
Settle/tax the *existing* distribution pool balance strictly against the stake pool's reward growth measured **before** adding the new distribution amount, and only then add the new amount's own shares without letting it contribute to the "diff" used for commission calculation. Concretely, `add_distribution`/`update_distribution_pool` should compute `updated_total_coins` as the current pending-inactive balance *excluding* the amount about to be deposited for the new recipient (or perform the commission-on-growth settlement using the total observed at the last settlement plus only genuine reward accrual, separate from the newly injected principal amount).

### Proof of Concept
1. Staker `S` and operator `O` set up a `staking_contract` with 10% commission; stake pool is active and earning rewards.
2. `S` calls `unlock_stake(S, O, 1000)`. This forces a preceding `distribute_internal` (no-op, nothing inactive yet) and `request_commission_internal` (pays out any owed commission), then calls `add_distribution(O, sc, S, 1000)`, giving `S` 1000 shares in `distribution_pool` (`total_coins = 1000`).
3. Before `distribute()` is called (lockup has not expired), `O` calls `request_commission(O, staker_addr, O)` again. Suppose the stake pool produced `C` new commission-eligible rewards, unlocking `C` coins for the operator.
4. Inside `add_distribution(O, sc, O, C)`, `total_distribution_amount` (from `stake::get_stake`) now equals `1000 + C` (assuming negligible/no real reward growth on the 1000 pending coins). `update_distribution_pool` computes for `S`: `previous_worth = 1000`, `current_worth = 1000 * (1000 + C) / 1000 = 1000 + C`, `unpaid_commission = C * 10 / 100`. This is transferred from `S`'s shares to `O`, even though `S`'s 1000 coins earned zero real rewards in this step.
5. `S`'s pending balance is now `1000 - C*0.1` while `O` additionally receives `C*0.1` worth of shares beyond their entitled `C` commission — value taken from `S` with no corresponding reward event.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L607-674)
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

        request_commission_internal(
            operator,
            staking_contract,
        );
    }

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L676-729)
```text
    /// Staker can call this to request withdrawal of part or all of their staking_contract.
    /// This also triggers paying commission to the operator for accounting simplicity.
    public entry fun unlock_stake(
        staker: &signer, operator: address, amount: u64
    ) acquires Store, BeneficiaryForOperator {
        // Short-circuit if amount is 0.
        if (amount == 0) return;

        let staker_address = signer::address_of(staker);
        assert_staking_contract_exists(staker_address, operator);

        let store = borrow_global_mut<Store>(staker_address);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);

        // Force distribution of any already inactive stake.
        distribute_internal(
            staker_address,
            operator,
            staking_contract,
        );

        // For simplicity, we request commission to be paid out first. This avoids having to ensure to staker doesn't
        // withdraw into the commission portion.
        let commission_paid =
            request_commission_internal(
                operator,
                staking_contract,
            );

        // If there's less active stake remaining than the amount requested (potentially due to commission),
        // only withdraw up to the active amount.
        let (active, _, _, _) = stake::get_stake(staking_contract.pool_address);
        if (active < amount) {
            amount = active;
        };
        staking_contract.principal -= amount;

        // Record a distribution for the staker.
        add_distribution(
            operator,
            staking_contract,
            staker_address,
            amount,
        );

        // Request to unlock the distribution amount from the stake pool.
        // This won't become fully unlocked until the stake pool's lockup expires.
        stake::unlock_with_cap(amount, &staking_contract.owner_cap);

        let pool_address = staking_contract.pool_address;
        emit(
            UnlockStake { pool_address, operator, amount, commission_paid }
        );
    }
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
