## Finding Assessment

Based on my review of `stake.move`'s transaction-fee distribution logic, this is a **valid, but low-to-moderate severity** finding regarding the timing of fee-split accounting, not a full asset-loss bug.

### Title
Transaction-fee split ratio between active/pending_inactive stake is captured only at the epoch-boundary instant, letting an unprivileged delegator front-run the split via `unlock`/`reactivate_stake` - ([File: aptos-move/framework/aptos-framework/sources/stake.move])

### Summary
`record_fee` accumulates transaction-fee credits for a validator's pool continuously throughout an entire epoch, based on all transactions processed by that validator across many blocks. [1](#0-0) 
However, that accumulated `fee_octa` is only split between `active` and `pending_inactive` once, inside `update_stake_pool`, at the very moment `on_new_epoch` runs — using whatever `stake_active`/`stake_pending_inactive` balances the `StakePool` happens to hold at that single instant: [2](#0-1) 
`update_stake_pool` is invoked once per epoch for every active/pending_inactive validator from `on_new_epoch`: [3](#0-2) 

### Finding Description
The fee-split ratio (`fee_pending_inactive = fee_octa * stake_pending_inactive / (stake_active + stake_pending_inactive)`, `fee_active = fee_octa - fee_pending_inactive`) is a *snapshot* calculation, whereas the underlying `fee_octa` pot represents fees collected across the *entire epoch's* worth of transactions. Because `unlock()`/`reactivate_stake()` on an active validator's `StakePool` move coins directly and synchronously between `stake_pool.active` and `stake_pool.pending_inactive` (per the pool's own documented state-machine semantics), an unprivileged delegator (through `delegation_pool::unlock`, which calls into `stake::unlock` on their behalf) can submit an `unlock` transaction in the last block before the epoch-ending reconfiguration transaction executes. This shifts a large amount of stake from `active` into `pending_inactive` immediately before `update_stake_pool` runs, inflating `stake_pending_inactive` relative to `stake_active` at exactly the instant the whole epoch's fee pot is split.

Downstream, `delegation_pool::synchronize_delegation_pool` reads the post-`update_stake_pool` `active`/`pending_inactive` totals via `calculate_stake_pool_drift` and distributes the resulting reward/fee delta pro-rata to the existing `active_shares` pool and `pending_inactive_shares` pool respectively. [4](#0-3) 
Since the attacker's newly-unlocked shares now occupy a large fraction of the `pending_inactive_shares` pool, they capture a disproportionate share of the fee-mint that was routed to `pending_inactive`, at the expense of the delegators who remained in `active_shares` (who would otherwise have received a larger `fee_active` allocation had the split been computed on the epoch-average, rather than end-of-epoch-instant, active/pending_inactive ratio).

### Impact Explanation
This does not let an attacker steal another delegator's principal or bypass lockup — it only skews how newly-minted transaction-fee rewards are apportioned between the active and pending_inactive buckets of a pool for that one epoch, redirecting some of that epoch's fee-reward yield from active delegators toward the attacker's own (about-to-be-inactivated) stake. The magnitude an attacker can redirect is bounded by how much of their own stake they can move in a single unlock and by the pool's total active/pending_inactive size, so it is a value-shifting/MEV-style issue on reward accrual, not an accounting break that permanently strands or fabricates funds.

### Likelihood Explanation
Exploitation requires precise knowledge of which block will trigger `on_new_epoch` (reconfiguration timing is not perfectly predictable to a delegator, though epoch length is fixed/known within a margin) and requires the attacker to already hold meaningful active stake in the pool relative to its total pending_inactive stake to move the ratio materially. This lowers practical likelihood versus a straightforward call-anytime exploit, but epoch boundaries are deterministic enough (fixed `EPOCH_DURATION`) that a sophisticated delegator could reliably target the final block(s) of an epoch.

### Recommendation
Compute the active/pending_inactive fee split ratio using a time-weighted or epoch-start snapshot of `stake_active`/`stake_pending_inactive` rather than the instantaneous balance read at `update_stake_pool` time, or freeze/checkpoint the split ratio earlier in the epoch (e.g., at `on_new_epoch`'s `PendingTransactionFee` reset point) so that late-epoch `unlock`/`reactivate_stake` calls cannot influence how already-accrued fees are apportioned.

### Proof of Concept
1. Delegator D holds a large `active` position in validator V's pool and a comparatively small existing `pending_inactive` position.
2. Over the epoch, V processes many transactions, accumulating `fee_octa` via repeated `record_fee` calls credited to V's `pending_fee_by_validator` entry. [5](#0-4) 
3. In the last block before the epoch-ending reconfiguration transaction, D calls `delegation_pool::unlock` for a large amount, synchronously moving coins from `stake_pool.active` to `stake_pool.pending_inactive`.
4. `on_new_epoch` → `update_stake_pool` executes immediately after, computing `fee_pending_inactive`/`fee_active` using the now-skewed `stake_active`/`stake_pending_inactive` ratio, and mints/merges the fee coins accordingly. [6](#0-5) 
5. `delegation_pool::synchronize_delegation_pool` distributes the resulting `pending_inactive` fee delta pro-rata to the `pending_inactive_shares` pool, where D's freshly-unlocked shares now hold an outsized fraction, letting D capture more of the epoch's fee reward than their time-weighted active stake would have earned.

Note: I was unable to retrieve the full source of `delegation_pool::unlock`/`stake::unlock` line-by-line in this session due to indexing limits on file content; a Devin session with full repo access is recommended to confirm the exact synchronous active→pending_inactive transfer path before treating this as final.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L671-694)
```text
    public(friend) fun record_fee(
        vm: &signer,
        fee_distribution_validator_indices: vector<u64>,
        fee_amounts_octa: vector<u64>
    ) acquires PendingTransactionFee {
        // Operational constraint: can only be invoked by the VM.
        system_addresses::assert_vm(vm);

        assert!(
            fee_distribution_validator_indices.length() == fee_amounts_octa.length()
        );

        let num_validators_to_distribute = fee_distribution_validator_indices.length();
        let pending_fee = borrow_global_mut<PendingTransactionFee>(@aptos_framework);
        let i = 0;
        while (i < num_validators_to_distribute) {
            let validator_index = fee_distribution_validator_indices[i];
            let fee_octa = fee_amounts_octa[i];
            pending_fee.pending_fee_by_validator.borrow_mut(&validator_index).add(
                fee_octa
            );
            i += 1;
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1348-1359)
```text
        // Process pending stake and distribute transaction fees and rewards for each currently active validator.
        validator_set.active_validators.for_each_ref(|validator| {
            let validator: &ValidatorInfo = validator;
            update_stake_pool(validator_perf, validator.addr, &config);
        });

        // Process pending stake and distribute transaction fees and rewards for each currently pending_inactive validator
        // (requested to leave but not removed yet).
        validator_set.pending_inactive.for_each_ref(|validator| {
            let validator: &ValidatorInfo = validator;
            update_stake_pool(validator_perf, validator.addr, &config);
        });
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1888-1893)
```text



                            / (stake_active + stake_pending_inactive)) as u64
                    );
                fee_active = fee_octa - fee_pending_inactive;
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1928-1943)
```text
        if (std::features::is_distribute_transaction_fee_enabled()) {
            let mint_cap =
                &borrow_global<AptosCoinCapabilities>(@aptos_framework).mint_cap;
            if (fee_active > 0) {
                coin::merge(&mut stake_pool.active, coin::mint(fee_active, mint_cap));
            };
            if (fee_pending_inactive > 0) {
                coin::merge(
                    &mut stake_pool.pending_inactive,
                    coin::mint(fee_pending_inactive, mint_cap)
                );
            };
            let fee_amount = fee_active + fee_pending_inactive;
            if (fee_amount > 0) {
                event::emit(DistributeTransactionFee { pool_address, fee_amount });
            };
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1939-1956)
```text
        // distribute rewards remaining after commission, to delegators (to already existing shares)
        // before buying shares for the operator for its entire commission fee
        // otherwise, operator's new shares would additionally appreciate from rewards it does not own

        // update total coins accumulated by `active` + `pending_active` shares
        // redeemed `add_stake` fees are restored and distributed to the rest of the pool as rewards
        pool.active_shares.update_total_coins(active - commission_active);
        // update total coins accumulated by `pending_inactive` shares at current observed lockup cycle
        pending_inactive_shares_pool_mut(pool).update_total_coins(pending_inactive - commission_pending_inactive);

        // reward operator its commission out of uncommitted active rewards (`add_stake` fees already excluded)
        buy_in_active_shares(pool, beneficiary_for_operator(stake::get_operator(pool_address)), commission_active);
        // reward operator its commission out of uncommitted pending_inactive rewards
        buy_in_pending_inactive_shares(
            pool,
            beneficiary_for_operator(stake::get_operator(pool_address)),
            commission_pending_inactive
        );
```
