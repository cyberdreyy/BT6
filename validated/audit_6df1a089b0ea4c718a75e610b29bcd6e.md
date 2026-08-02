## Analysis

The Pyth bug's core lesson: a **stateful key** (provider address + sequence number) must be tightly bound to its true "owner"; if a rotation swaps out one component of the key while reusing the mapping, funds/state that belonged to the old owner get misattributed to the new one. In Aptos's `staking_contract` module, the analogous state is the `distribution_pool` (a `pool_u64::Pool` shares-based ledger of commission/stake owed to various recipients) that is keyed by plain address, and the "current operator" identity used to exempt/charge commission on that pool.

### The break

`StakingContract.distribution_pool` [1](#0-0)  holds shares for whoever is owed a payout — including the operator's own unpaid commission, added via `add_distribution(operator, staking_contract, operator, commission_amount)` inside `request_commission_internal` [2](#0-1) .

`switch_operator` lets the staker swap the operator on a live `StakingContract` **without settling** any already-requested-but-not-yet-inactive commission shares that were bought in under the old operator's address; those shares simply remain in the same `distribution_pool` after the operator field is changed and the struct is re-keyed to `new_operator`: [3](#0-2) 

Every later touch of that pool (`add_distribution` or `distribute_internal`) calls `update_distribution_pool(distribution_pool, updated_total_coins, operator, commission_percentage)`, where `operator` is now the **current** operator (`new_operator`): [4](#0-3) 

`update_distribution_pool` charges commission on the appreciation of **every** shareholder except the one matching the `operator` parameter, transferring the corresponding shares to that `operator`:
```
if (shareholder != operator) {
    ...
    let unpaid_commission = (current_worth - previous_worth) * commission_percentage / 100;
    pool_u64::transfer_shares(distribution_pool, shareholder, operator, shares_to_transfer);
};
```
Since the old operator's pending commission entry is keyed by `old_operator` (not `new_operator`), the exemption `shareholder != operator` no longer protects it. The new operator's own `request_commission`/`unlock_stake`/`distribute` calls will treat `old_operator`'s already-earned, already-requested commission entry as an ordinary staker share and skim `new_commission_percentage` off its appreciation into `new_operator`'s own shares.

This is structurally identical to the Pyth flaw: a state entry (`games[sequenceNumber]` / here `distribution_pool` shares keyed by address) that should remain scoped to its original "provider"/operator instead gets reinterpreted under a rotated identity, letting the new entity claim value it never earned — with no composite key (e.g. `(operator_tenure_id, address)`) to prevent the collision.

### Title
Operator switch lets the new operator skim commission from the previous operator's already-requested distribution shares - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`switch_operator` changes the operator of a `StakingContract` but leaves the old operator's pending (already-requested, not-yet-withdrawn) commission shares inside the same `distribution_pool`, keyed by the old operator's address. `update_distribution_pool`, invoked on every subsequent `request_commission`, `unlock_stake`, or `distribute` call, exempts only the *current* `operator` parameter from commission charges. Because the old operator's shares no longer match the current operator identity, they are treated like ordinary staker shares and a cut of their appreciation is transferred to the new operator.

### Finding Description
`request_commission_internal` buys the operator into `distribution_pool` under their own address as a placeholder for unpaid commission: [5](#0-4) 

`switch_operator` reassigns the pool's operator (`stake::set_operator_with_cap`) and re-keys the `StakingContract` in the staker's `Store` without first forcing the old operator's pending commission shares to be fully paid out or otherwise isolated: [3](#0-2) 

`add_distribution` and `distribute_internal` both call `update_distribution_pool` using whatever `operator` argument reflects the caller's current view of the contract's operator: [6](#0-5) [7](#0-6) 

`update_distribution_pool`'s exemption is by address identity only, and it is this identity that no longer matches the old operator's stranded shares once the operator has rotated: [8](#0-7) 

### Impact Explanation
This directly corrupts share-accounting for operator commission (explicitly listed impact): the new operator, an unprivileged actor freshly appointed by the staker, is credited with a portion of value that was earned and already claimed (via `request_commission`) by the previous operator. The old operator's already-locked-in claim right is reduced without their consent, and the funds are permanently redirected to the wrong account with no recovery path once shares are transferred and later distributed.

### Likelihood Explanation
Requires only a normal `switch_operator` call by the staker (an ordinary, expected staking operation) followed by any routine interaction (`request_commission`, `unlock_stake`, or `distribute`) by the new operator or anyone else before the old operator's already-requested commission stake becomes fully inactive (i.e., during the remainder of the current lockup cycle). This is a common, easily triggered timing window, not a privileged or exotic sequence.

### Recommendation
Before reassigning the operator in `switch_operator`, fully settle (force-distribute or otherwise migrate) any outstanding shares held under `old_operator`'s key in `distribution_pool`, or use a composite/tenure-scoped key (e.g., record which operator "owns" each pending distribution entry) so `update_distribution_pool` can correctly continue to exempt stale entries from commission-skimming by a subsequent operator.

### Proof of Concept
1. Staker creates a staking contract with `operator1`, commission 10%.
2. Stake pool earns rewards; `operator1` calls `request_commission`, which unlocks their commission and buys them into `distribution_pool` (shares still pending_inactive, not yet withdrawable).
3. Staker calls `switch_operator(staker, operator1, operator2, 50)` — `operator1`'s pending commission shares remain in the same `distribution_pool`, now attached to a `StakingContract` re-keyed under `operator2` with `commission_percentage = 50`.
4. An epoch passes; the pending_inactive stake (including `operator1`'s stranded commission shares) earns additional rewards.
5. `operator2` calls `request_commission`, which calls `add_distribution` → `update_distribution_pool(distribution_pool, updated_total_coins, operator2, 50)`. Because `operator1 != operator2`, the function charges 50% of `operator1`'s share appreciation and transfers those shares to `operator2`.
6. When distribution finally occurs, `operator2` receives value skimmed from `operator1`'s already-requested, previously-earned commission, which `operator1` can never reclaim.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L73-85)
```text
    struct StakingContract has store {
        // Recorded principal after the last commission distribution.
        // This is only used to calculate the commission the operator should be receiving.
        principal: u64,
        pool_address: address,
        // The stake pool's owner capability. This can be used to control funds in the stake pool.
        owner_cap: OwnerCapability,
        commission_percentage: u64,
        // Current distributions, including operator commission withdrawals and staker's partial withdrawals.
        distribution_pool: Pool,
        // Just in case we need the SignerCap for stake pool account in the future.
        signer_cap: SignerCapability
    }
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L880-886)
```text
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
