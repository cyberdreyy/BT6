## Confirmed local analog: operator commission is misdirected away from the operator's beneficiary after `switch_operator`

### Title
Commission earned before `switch_operator` bypasses the operator's registered beneficiary on later distribution - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
The external report's root cause is a missing check that lets an accounting/ownership pointer stay valid past a state change (vault capacity), causing funds routed to the wrong place. The Aptos analog: `staking_contract::distribute_internal` decides whether to redirect a payout to an operator's beneficiary by comparing the *stored recipient* of a pending distribution entry to the *current* `operator` parameter passed into the function. `switch_operator` changes the key under which a `StakingContract` is stored (and the `operator` used in all future `distribute` calls) while leaving previously recorded pending-distribution entries keyed to the old operator address. Once the operator changes, any commission recorded for the old operator before the switch will permanently bypass the beneficiary redirection logic.

### Finding Description
`distribute_internal` pays out pending distributions and redirects operator payouts to their registered beneficiary via: [1](#0-0) 

The redirect only fires `if (recipient == operator)`, where `operator` is the function parameter supplied by the caller (always the *current* key of the `StakingContract`, i.e. `new_operator` after a switch), and `recipient` is whatever address was stored in the distribution pool when the entry was created (the *old* operator address, added via `add_distribution` with `recipient = operator` at creation time): [2](#0-1) [3](#0-2) 

In `switch_operator`, the sequence is: flush any already-distributable stake (`distribute_internal` with `old_operator`), then force a new pending commission distribution for the not-yet-withdrawable stake via `request_commission_internal(old_operator, ...)` (this adds a distribution entry keyed by `recipient = old_operator`), and only afterwards does the code re-key the `StakingContract` from `old_operator` to `new_operator`: [4](#0-3) 

After this point, the `StakingContract` lives under the `new_operator` key. Every subsequent call to `distribute(staker, new_operator)` (or any other staker action that forces `distribute_internal`) will invoke `distribute_internal(staker, new_operator, staking_contract)`. When the pending-inactive stake matching the old operator's pre-switch commission finally becomes withdrawable and is paid out, the comparison `recipient == operator` evaluates `old_operator == new_operator`, which is false. The `beneficiary_for_operator(operator)` redirect is therefore never applied for that entry — even if the old operator had previously called `set_beneficiary_for_operator` to route their commission to a separate (e.g., cold-storage) address: [5](#0-4) 

The funds still go to `recipient` (the old operator's own account address, which was captured verbatim at `add_distribution` time), not to the beneficiary the operator configured. This is a wrong-account credit for a beneficiary payout: the operator's chosen beneficiary permanently loses claim to commission that was earned and recorded before the operator switch, once the underlying stake becomes withdrawable after the switch has occurred.

### Impact Explanation
This matches the required "Operator commission, beneficiary payout ... corruption that credits the wrong account" impact. An operator who relies on a beneficiary address (for security or custody separation reasons) to receive commission will have any commission that was pending/unlocking at the time of a `switch_operator` call silently redirected to the operator's own account address instead, with no way to recover the redirect after the fact (the entry's `recipient` field is fixed at creation and the comparison logic can never match again since the `StakingContract` is permanently re-keyed to the new operator). This is not an attacker directly stealing another party's funds, but it is a non-recoverable payout-corruption bug reachable by the ordinary, unprivileged `staker` role calling `switch_operator`, or by the operator itself failing to call `distribute` before requesting a switch — as the code comment at line 808 warns, but does not enforce.

### Likelihood Explanation
Likelihood is Medium: it requires (1) an operator having previously set a beneficiary via `set_beneficiary_for_operator`, and (2) a `switch_operator` call occurring while there is unpaid/unlocking commission for that operator. Both are normal, expected staking-contract lifecycle operations (operator rotation is a documented supported flow), not adversarial edge cases, so this can be triggered unintentionally by any staker performing a routine operator change without following the "distribute before switching" guidance in the code comment.

### Recommendation
In `distribute_internal`, do not compare `recipient == operator` (the function's current operator argument) to decide whether to redirect to a beneficiary. Instead, always look up `beneficiary_for_operator(recipient)` whenever `recipient` corresponds to any address that was, at the time of the distribution's creation, an operator of this staking contract (e.g., record whether an entry is a "commission" distribution and store the recipient's own beneficiary-eligibility flag at creation time, or store the beneficiary address directly on the distribution entry when the commission is unlocked, resolved through `request_commission_internal`, since that is when the operator identity that earned it is unambiguous). Alternatively, force a full `distribute_internal`/beneficiary-safe flush before permitting the `StakingContract` to be re-keyed in `switch_operator`, rejecting the switch if any pending/unlocking commission is still owed to the old operator.

### Proof of Concept
1. Staker creates a staking contract with `operator_1`, `commission_percentage = 10`.
2. `operator_1` calls `set_beneficiary_for_operator(operator_1, beneficiary_1)`.
3. Stake pool earns rewards; validator is active.
4. Staker calls `switch_operator(staker, operator_1, operator_2, new_commission)`. Internally:
   - `distribute_internal(staker, operator_1, ...)` pays out any already-inactive stake correctly to `beneficiary_1` (matches at this point).
   - `request_commission_internal(operator_1, ...)` unlocks new commission and records a distribution entry `recipient = operator_1` for the not-yet-withdrawable commission.
   - The contract is re-keyed to `operator_2`.
5. Time passes until the stake pool's lockup expires and the commission stake becomes inactive/withdrawable.
6. Anyone calls `distribute(staker_address, operator_2)`. `distribute_internal(staker, operator_2, ...)` runs; it finds the pending entry with `recipient = operator_1`, but since `operator_1 != operator_2`, the beneficiary redirect is skipped, and the commission is deposited directly to `operator_1`'s account instead of `beneficiary_1`.

Note: this PoC was traced statically from the code; it was not executed against a live Move test harness, so exact numeric balances were not verified by running `#[test]` code, but the control-flow path and the recipient/operator key mismatch are directly supported by the cited source lines.

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L807-822)
```text
    /// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
    /// beneficiary. To ensures payment to the current beneficiary, one should first call `distribute` before switching
    /// the beneficiary. An operator can set one beneficiary for staking contract pools, not a separate one for each pool.
    public entry fun set_beneficiary_for_operator(












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
