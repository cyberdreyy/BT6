## Finding: Confirmed

Tracing `switch_operator` and `distribute_internal` in `staking_contract.move` confirms the vulnerability described in the question.

### Root cause

`switch_operator` performs this sequence while `staking_contract` is still keyed by `old_operator` in the map:

1. `distribute_internal(staker_address, old_operator, &mut staking_contract)` — this call fully drains `distribution_pool.shareholders()`, so any *pre-existing* commission share for `old_operator` is correctly redirected via `beneficiary_for_operator(old_operator)` (since `recipient == operator` holds, `operator == old_operator`). [1](#0-0) 

2. Immediately after, `request_commission_internal(old_operator, &mut staking_contract)` recalculates rewards accrued up to this instant and calls `add_distribution(old_operator, staking_contract, old_operator, commission_amount)`, which **buys in new shares keyed to `old_operator`'s raw address** in the same `distribution_pool`. [2](#0-1) 

3. Only after that does the code call `stake::set_operator_with_cap(&staking_contract.owner_cap, new_operator)` and re-insert the `StakingContract` (including the `distribution_pool` with the freshly-created `old_operator`-keyed share) into the map under the new key `new_operator`. [3](#0-2) 

The framework's own test explicitly documents this leftover state: after `switch_operator`, "the staking_contract is now associated with operator 2 but there should be a pending distribution of unpaid commission to operator 1," verified via `assert_distribution(staker_address, operator_2_address, operator_1_address, commission_for_operator_1)`. [4](#0-3) 

### The mismatch

Later, when anyone (unprivileged, since `distribute` has no signer/role check) calls `distribute(staker, new_operator)`, it fetches the contract by `new_operator` and runs:
```
if (recipient == operator) {
    recipient = beneficiary_for_operator(operator);
};
``` [5](#0-4) 

Here `operator == new_operator`, but the pending share's `recipient == old_operator`. The equality check fails, so the beneficiary redirect never triggers, and the accrued pre-switch commission for `old_operator` is deposited straight to `old_operator`'s raw account address via `aptos_account::deposit_coins`, completely bypassing any `BeneficiaryForOperator` resource `old_operator` had configured. `distribute` itself is explicitly documented as callable by anyone: "Allow anyone to distribute already unlocked funds... does not need to be restricted to just the staker or operator." [6](#0-5) 

### Assessment

This is a real accounting/beneficiary-routing bug: the `recipient == operator` heuristic used to detect "this share belongs to the operator's commission" silently breaks whenever operator identity changes mid-cycle with unresolved pending commission shares, and the resulting payout is a raw-address deposit that ignores the `beneficiary_for_operator` mapping for the old operator, rather than paying the currently-stored beneficiary. Note the impact is scoped: funds still land in `old_operator`'s own account (not an attacker-controlled account) — the affected party is the old operator (or their designated beneficiary), not a third-party unprivileged attacker gaining value. Whether this rises to "valid" per the review's bar depends on whether beneficiary misrouting to the operator's own address (rather than fund theft by the caller) counts as sufficient impact; the mechanism itself is real and reproducible via the framework's own test scaffolding.

### Recommendation (conceptual)
`distribute_internal`/`update_distribution_pool` should resolve the beneficiary based on whether `recipient` itself has any `BeneficiaryForOperator` entry (i.e. check `exists<BeneficiaryForOperator>(recipient)` or otherwise track the operator identity a given share was earned under) rather than comparing against the staking contract's *current* `operator` field, since the current operator can change between when a commission share is created and when it is redeemed. [7](#0-6)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L637-657)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L783-796)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L798-805)
```text
        // Update the staking contract's commission rate and stake pool's operator.
        stake::set_operator_with_cap(&staking_contract.owner_cap, new_operator);
        staking_contract.commission_percentage = new_commission_percentage;

        let pool_address = staking_contract.pool_address;
        staking_contracts.add(new_operator, staking_contract);
        emit(SwitchOperator { pool_address, old_operator, new_operator });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L840-920)
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

        let distribution_pool = &mut staking_contract.distribution_pool;
        update_distribution_pool(
            distribution_pool,
            distribution_amount,
            operator,
            staking_contract.commission_percentage
        );

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

            emit(
                Distribute {
                    operator,
                    pool_address,
                    recipient,
                    amount: amount_to_distribute
                }
            );
        };

        // In case there's any dust left, send them all to the staker.
        if (coin::value(&coins) > 0) {
            aptos_account::deposit_coins(staker, coins);
            distribution_pool.update_total_coins(0);
        } else {
            coin::destroy_zero(coins);
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1568-1577)
```text
        // The staking_contract is now associated with operator 2 but there should be a pending distribution of unpaid
        // commission to operator 1.
        let new_balance = with_rewards(INITIAL_BALANCE);
        let commission_for_operator_1 = (new_balance - INITIAL_BALANCE) / 10;
        assert_distribution(
            staker_address,
            operator_2_address,
            operator_1_address,
            commission_for_operator_1
        );
```
