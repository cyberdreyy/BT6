Based on my investigation, the strongest Aptos-native analog to the Balancer `totalSupply`/`getActualSupply` bug is in `staking_contract.move`'s distribution-pool share-price accounting, where two different call sites feed inconsistent "total pool value" figures into the same `pool_u64`-based `update_distribution_pool` share-repricing logic.

### Title
Inconsistent total-coins input to `update_distribution_pool` corrupts distribution-pool share pricing and can misdirect operator commission - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract.move` tracks staker/operator claims on unlocked stake via a `pool_u64` "distribution pool," repriced by `update_distribution_pool`, which computes each shareholder's `current_worth` from a caller-supplied `updated_total_coins` value and then permanently sets `distribution_pool.total_coins = updated_total_coins` [1](#0-0) . This is exactly the "totalSupply vs getActualSupply" pattern from the external report: the share-repricing math is only as correct as the "total supply/coins" figure fed into it. Two call sites feed *different* definitions of that total.

### Finding Description
`distribute_internal` (called before any withdrawal/commission event) computes the true withdrawable value as `inactive + pending_inactive` and uses that as `distribution_amount` when repricing the pool: [2](#0-1) 

But `add_distribution` — invoked by `request_commission_internal` (to register the operator's commission claim) and by `unlock_stake` (to register the staker's withdrawal claim) — instead destructures `stake::get_stake` and binds only the 4th tuple element (`pending_inactive`) to the variable it calls `total_distribution_amount`, discarding `inactive` entirely: [3](#0-2) 

Because `update_distribution_pool` unconditionally overwrites `distribution_pool`'s stored `total_coins` with whatever `updated_total_coins` it was given [4](#0-3) , calling `add_distribution` (via `request_commission_internal` or `unlock_stake`) with a `total_distribution_amount` that omits `inactive` stake causes the pool's recorded "total coins" to diverge from what `distribute_internal` would compute for the same underlying stake pool state. On the next `distribute_internal` call, the jump from the artificially low total back up to `inactive + pending_inactive` is interpreted by `update_distribution_pool` as newly-earned "rewards" on which commission is charged (`unpaid_commission = (current_worth - previous_worth) * commission_percentage / 100`) [5](#0-4) , even though part of that delta may simply be `inactive` principal that was never actually new rewards — corrupting the share price basis used for commission and payout splitting between staker and operator.

### Impact Explanation
If the discrepancy is exploitable in the ordering of unprivileged, permissionless calls (`distribute`, `request_commission`, `unlock_stake` are all callable by third parties or the staker/operator without special privilege) [6](#0-5) [7](#0-6) , this falls under the required "operator commission ... share-accounting corruption that credits the wrong account or traps value" impact class, since it would let commission be computed against a wrong total-coins baseline, transferring value to/from the wrong recipient in `pool_u64::transfer_shares`.

### Likelihood Explanation
Medium-to-uncertain: I confirmed the exact code-level discrepancy (different total-coins definitions at two call sites feeding the same repricing function), which is a real, unprivileged-reachable pattern. However, I was unable to fully verify within available tool calls whether `stake::withdraw_with_cap`'s actual withdrawal semantics (which stake states it can pull from) neutralize this discrepancy in practice — e.g., if `distribute_internal` always fully drains `inactive` before `add_distribution` is ever invoked in the same transaction, the divergence may not be triggerable in a state where it matters. I could not retrieve `get_staking_contract_amounts_internal` or `stake::withdraw_with_cap`'s body due to running out of iterations to fully confirm end-to-end exploitability.

### Recommendation
Make `add_distribution`'s `total_distribution_amount` consistent with `distribute_internal`'s definition (`inactive + pending_inactive`) so `update_distribution_pool` is always repriced against the same, complete "total coins" figure regardless of call site.

### Proof of Concept
Not independently verified end-to-end due to tool/iteration limits — the PoC would need to: (1) create a staking contract, (2) advance an epoch so some stake becomes `inactive`, (3) call `unlock_stake` or `request_commission` (which hits `add_distribution` with the truncated total) without an intervening full `distribute`, (4) call `distribute` afterward and observe the commission charged against the artificial jump in total-coins. I was not able to trace `stake::withdraw_with_cap`'s exact semantics to confirm this sequence is reachable, so this should be treated as an unverified candidate requiring further code review (e.g., via a Devin session with full file/test access) rather than a confirmed exploit.

**Caveat:** Given the incomplete verification of the downstream `stake` module semantics, I cannot state with full confidence that this reaches the "High/Critical, independently-proven" bar required by the task's strict output gate. If you want this fully confirmed, a deeper trace of `stake::withdraw_with_cap` and `get_staking_contract_amounts_internal` is needed, which I could not complete within the available tool budget.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L607-635)
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L938-957)
```text
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
