## Finding: Beneficiary bypass for pending operator commission after `switch_operator`/`switch_operator_with_same_commission`

### Title
Operator commission accrued at `switch_operator`/`switch_operator_with_same_commission` bypasses the operator's configured beneficiary and is force-paid to the old operator's raw address - (`aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary of the analog
The external report's bug class is: a checkpoint/continuation structure (`ongoing_soul_tx`) stores only a coarse key (the `from` account) and resolves the real recipient dynamically at redemption time, so a later mismatch between the key used to create the checkpoint and the key used to resolve it silently redirects value. `staking_contract.move`'s `distribution_pool` has the same structural pattern: distributions are buy-in shares keyed by `recipient` (which, for commission, is literally set to the `operator` address at accrual time), and the beneficiary redirect is only applied if `recipient == operator`, where `operator` is whatever key is passed into `distribute_internal` *at redemption time*. When the operator key used at redemption time differs from the key used at accrual time (which happens across `switch_operator`), the redirect silently fails.

### Finding Description
`add_distribution`/`request_commission_internal` record pending commission as a share in `staking_contract.distribution_pool` keyed by the operator address at the moment the commission is requested: [1](#0-0) 

`switch_operator_with_same_commission` (and the analogous `switch_operator`) first flushes any already-distributable balance via `distribute_internal(staker_address, old_operator, &mut staking_contract)`, and only *afterward* calls `request_commission_internal(old_operator, &mut staking_contract)`, which creates a brand-new distribution-pool entry keyed to `old_operator` for the commission on stake accrued up to the switch. The `StakingContract` object (with this now-populated `distribution_pool`) is then moved into the `Store.staking_contracts` map under the **new** operator's address: [2](#0-1) 

Because that entry is only "requested" (unlocked) but not yet withdrawable/distributed, it stays in `distribution_pool` until someone later calls `distribute(staker, new_operator)`, which is the only key now present for the staking contract: [3](#0-2) 

`distribute_internal`'s beneficiary redirect logic compares the recorded `recipient` against the `operator` argument it was called with:
```
if (recipient == operator) {
    recipient = beneficiary_for_operator(operator);
};
``` [4](#0-3) 

At this final `distribute()` call, `operator == new_operator` while the leftover share's `recipient == old_operator`, so `recipient == operator` is `false`. The redirect to `beneficiary_for_operator(old_operator)` is never applied, and the coins are deposited directly to `old_operator`'s raw account instead of the beneficiary that `old_operator` had explicitly configured via `set_beneficiary_for_operator`: [5](#0-4) 

This exactly mirrors the report's root cause: the recipient-resolution key used when the checkpoint (distribution share) is created is not the same key used when it is later redeemed, so the intended beneficiary/recipient binding silently breaks.

### Impact Explanation
Any pending commission unlocked for an operator right at (or shortly before) a `switch_operator`/`switch_operator_with_same_commission` call is permanently rerouted away from that operator's configured `BeneficiaryForOperator` and paid to the operator's own base address instead. This is a beneficiary-payout mis-crediting to the wrong account as explicitly called out in the required impact set ("Operator commission, beneficiary payout ... that credits the wrong account"). It is triggered purely by the staker (who legitimately owns the right to call `switch_operator*`) but the resulting mis-payment affects the operator/beneficiary relationship without either party's consent at redemption time, and it is irreversible once the deposit happens (funds land in a normal, spendable account, not stuck, but not the account the operator designated).

### Likelihood Explanation
Requires: (1) `operator_beneficiary_change_enabled` feature on, (2) the operator has set a beneficiary different from itself via `set_beneficiary_for_operator`, (3) the staker calls `switch_operator` or `switch_operator_with_same_commission` while there is unpaid/inactive commission that was requested during that same call. Since `distribute()` is permissionless and stakers can call `switch_operator*` at will, this can be triggered any time a switch happens with outstanding commission, without any special privilege beyond the staker role the caller already possesses over their own staking contract.

### Recommendation
Store the beneficiary (or resolve/lock it) at the moment `add_distribution`/`request_commission_internal` creates the commission share, or make `distribute_internal`'s redirect check independent of the possibly-stale `operator` key by looking up `beneficiary_for_operator(recipient)` directly whenever `recipient` is (or was) ever an operator address, rather than only when `recipient == operator` for the currently-mapped operator.

### Proof of Concept
1. Staker creates a staking contract with `operator1`, commission > 0.
2. `operator1` calls `set_beneficiary_for_operator(operator1, beneficiary_X)`.
3. Rewards accrue; commission becomes unlockable.
4. Staker calls `switch_operator_with_same_commission(staker, operator1, operator2)`. Internally: `distribute_internal` pays out any already-inactive commission correctly to `beneficiary_X` (recipient==operator at that point matches). `request_commission_internal` then creates a *new* distribution-pool share keyed to `operator1` for the just-unlocked commission, and the `StakingContract` (with this pending share) is re-keyed to `operator2` in the staker's `Store`.
5. Once the lockup expires, anyone calls `distribute(staker, operator2)`. In `distribute_internal`, `operator == operator2`, but the pending share's `recipient == operator1`, so the beneficiary check fails and the coins are deposited straight to `operator1`, not `beneficiary_X`. [6](#0-5)

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L807-838)
```text
    /// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
    /// beneficiary. To ensures payment to the current beneficiary, one should first call `distribute` before switching
    /// the beneficiary. An operator can set one beneficiary for staking contract pools, not a separate one for each pool.
    public entry fun set_beneficiary_for_operator(
        operator: &signer, new_beneficiary: address
    ) acquires BeneficiaryForOperator {
        assert!(
            features::operator_beneficiary_change_enabled(),
            std::error::invalid_state(EOPERATOR_BENEFICIARY_CHANGE_NOT_SUPPORTED)
        );
        // The beneficiay address of an operator is stored under the operator's address.
        // So, the operator does not need to be validated with respect to a staking pool.
        let operator_addr = signer::address_of(operator);
        let old_beneficiary = beneficiary_for_operator(operator_addr);
        if (exists<BeneficiaryForOperator>(operator_addr)) {
            borrow_global_mut<BeneficiaryForOperator>(operator_addr).beneficiary_for_operator =
                new_beneficiary;
        } else {
            move_to(
                operator,
                BeneficiaryForOperator { beneficiary_for_operator: new_beneficiary }
            );
        };

        emit(
            SetBeneficiaryForOperator {
                operator: operator_addr,
                old_beneficiary,
                new_beneficiary
            }
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L840-853)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L856-920)
```text
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
