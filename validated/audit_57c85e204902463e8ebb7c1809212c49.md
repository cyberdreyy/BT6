### Title
Operator-switch leaves a stale commission entry in `distribution_pool` that is subsequently re-taxed and redirected to the new operator - ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Summary
`staking_contract::switch_operator` unlocks the old operator's pending commission into the shared `distribution_pool` right before changing the map key from `old_operator` to `new_operator`. Because `StakingContract` has no explicit `operator` field (the "operator" identity used everywhere is just the caller-supplied address parameter, matched against the `Store.staking_contracts` map key), the pool_u64 shares entry that was bought under `old_operator`'s address survives the switch unchanged. Any subsequent call that touches the pool (`unlock_stake`, `request_commission`, `distribute`) runs `update_distribution_pool` with `operator == new_operator`, and since `old_operator != new_operator` the stale entry is no longer recognized as "the operator's own balance" (which is normally commission-exempt) - it's treated like an ordinary staker balance and taxed by `new_commission_percentage`, with the skimmed shares transferred to `new_operator`. Value that was already earned and locked by the old operator is siphoned into the new operator's payout.

### Finding Description
The `StakingContract` struct has no `operator` field; the "operator" for all purposes is just the key under which the struct is stored in `Store.staking_contracts` (a `SimpleMap<address, StakingContract>`): [1](#0-0) 

`switch_operator` performs, in order: (1) flush any currently withdrawable funds via `distribute_internal`, (2) unlock/record the *newly accrued* commission for `old_operator` via `request_commission_internal` (which calls `add_distribution` → `update_distribution_pool` and then `stake::unlock_with_cap`), (3) call `stake::set_operator_with_cap` to the new operator, and (4) re-insert the *same* `StakingContract` (with its unchanged `distribution_pool`) under the `new_operator` key: [2](#0-1) 

The commission just requested in step (2) is unlocked stake (`pending_inactive`), which cannot be withdrawn/distributed in the same transaction because it has not cleared the lockup yet, so its `pool_u64` shares entry — keyed by the **old_operator address** — remains in `distribution_pool` with a nonzero balance when the switch completes.

`request_commission_internal` / `add_distribution` both call `update_distribution_pool`, whose only protection against taxing the operator's own commission balance is an address equality check against the `operator` parameter passed in by the caller: [3](#0-2) [4](#0-3) 

Once the staking contract is filed under `new_operator`, every future call (`unlock_stake`, `request_commission`, `distribute`) passes `operator = new_operator` into `update_distribution_pool`. The stale entry keyed by `old_operator` no longer matches `shareholder != operator` as "false" (exempt) — it now evaluates to `true` (not exempt), so it gets taxed exactly like a normal staker share: its illusory "growth" (which occurs purely because `updated_total_coins` increases whenever any new distribution, e.g. the staker's own later `unlock_stake`, is recorded into the same shared pool) is charged `new_commission_percentage` and the corresponding shares are transferred from `old_operator` to `new_operator`: [5](#0-4) [6](#0-5) 

Root cause: the exemption logic in `update_distribution_pool` relies on comparing shareholder address to the *currently passed* `operator` parameter rather than tracking which shareholder entries represent historical commission owed to a *previous* operator. `switch_operator` changes the operator identity without first fully draining or otherwise reconciling the old operator's unresolved pending_inactive commission entry, so that entry becomes indistinguishable from a plain staker balance and is exposed to erroneous taxation by whoever is the new operator.

### Impact Explanation
This corrupts operator-commission accounting: a legitimately-earned commission amount belonging to `old_operator` (already unlocked from the stake pool before the switch and awaiting the lockup to clear) is partially reassigned to `new_operator` on every subsequent pool-touching call, with no consent from or notification to `old_operator`. This is a credit-to-wrong-account / value-redirection bug in the operator-commission flow, falling squarely in the required impact class ("Operator commission ... payout, or share-accounting corruption that credits the wrong account or traps value"). The magnitude scales with how much time/activity elapses between the switch and when `old_operator`'s stale entry is finally withdrawn, and with how large `new_commission_percentage` is, and it repeats on every `add_distribution`/`update_distribution_pool` invocation until the stale balance is exhausted.

### Likelihood Explanation
`switch_operator` is a normal, unprivileged (staker-only) action exposed as a public entry function, and the corrupting side effect requires no special conditions beyond: (a) the old operator having an unpaid/unlocked commission portion still in `pending_inactive` (lockup not yet cleared) at switch time — a very common situation since lockup periods are long relative to typical commission-request cadence — and (b) any subsequent normal usage of the contract (staker unlocking more stake, anyone calling `distribute`/`request_commission`). Because these are all routine operations that occur naturally, the bug is likely to trigger without any adversarial intent, and can also be deliberately engineered by a staker (in collusion with, or benefiting, a new operator) to strip value from a departing operator.

### Recommendation
Persist the operator identity that owned each accrued distribution at the time it was recorded (e.g., tag `add_distribution` entries with the operator who earned them, or maintain a dedicated separate accounting record for pending operator commissions that is fully settled/withdrawn — not merely unlocked — before `switch_operator` is allowed to proceed). At minimum, `switch_operator` should refuse to complete (or should force a full drain including waiting for/forcing the new pending_inactive commission to resolve) while any nonzero, not-yet-distributed commission balance for `old_operator` remains in `distribution_pool`, and `update_distribution_pool`'s exemption check should be based on that persisted historical-operator tag rather than the currently passed `operator` parameter.

### Proof of Concept
1. Staker creates a staking contract with `operator_1` and `commission_percentage = 10`, deposits `P` APT (`create_staking_contract`).
2. Stake pool earns rewards over some epochs.
3. `operator_1` (or staker) calls `request_commission`. This unlocks the accrued commission `C1` into `pending_inactive` and records it in `distribution_pool` under key `operator_1` (via `add_distribution` → `update_distribution_pool`, exempt since `shareholder == operator == operator_1`). [7](#0-6) 
4. Before the lockup expires (so `C1` is still `pending_inactive`, not withdrawable), staker calls `switch_operator(staker, operator_1, operator_2, new_commission_percentage)`.
   - `distribute_internal` runs but cannot pay out `C1` (not yet inactive) so it remains as a `distribution_pool` entry keyed by `operator_1`.
   - `request_commission_internal` may add further unlocked commission also keyed `operator_1`.
   - The `StakingContract` (with its `distribution_pool` containing the `operator_1` entry) is moved to key `operator_2`. [2](#0-1) 
5. Staker later calls `unlock_stake(staker, operator_2, amount)`. This calls `request_commission_internal(operator_2, ...)` → `add_distribution(operator_2, ...)` → `update_distribution_pool(distribution_pool, updated_total_coins, operator_2, new_commission_percentage)`. Since `updated_total_coins` now includes the newly added staker distribution, the pool's `total_coins` grows; the loop iterates the `operator_1` shareholder entry, and because `operator_1 != operator_2`, it computes `unpaid_commission` on `operator_1`'s stale balance and transfers those shares to `operator_2`. [3](#0-2) 
6. When `operator_1`'s stale entry is finally paid out via `distribute`, it receives less than `C1` — the difference having been redirected to `operator_2` — even though `operator_2` did nothing to earn that portion.

Note: I was not able to run the Move test suite in this environment to empirically confirm the exact numeric skim (this would require executing `stake::withdraw_with_cap`/`pool_u64` arithmetic), so the quantitative magnitude is derived from static code tracing rather than an executed test; a Devin session with build/test tooling would be needed to produce and run a concrete unit test confirming the exact stolen amount.

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L676-720)
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
