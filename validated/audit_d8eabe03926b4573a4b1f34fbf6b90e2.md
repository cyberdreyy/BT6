No vulnerability found for this question.

**Reasoning:**

The proof idea conflates two entirely separate subsystems that don't share any state dependency.

`persisted_auxiliary_info_pruner.rs` operates purely on the storage layer's `PersistedAuxiliaryInfoDb`, which stores per-version `PersistedAuxiliaryInfo` (essentially just a `transaction_index` used for hashing/indexing historical transactions) and prunes it in lockstep with the rest of the ledger pruner window [1](#0-0) . This data has no relationship whatsoever to Move on-chain resource state.

`synchronize_delegation_pool` is a pure Move entry function that reads and mutates `DelegationPool` resource fields (`total_coins_inactive`, `observed_lockup_cycle`, `inactive_shares`, `active_shares`) that live in the Move global state/state-tree, entirely independent of the ledger's auxiliary-info column family [2](#0-1) . Delegator inactive balances are derived from these Move resources via `redeem_inactive_shares`/`inactive_shares` pools [3](#0-2) , not from anything in `PersistedAuxiliaryInfoDb`.

Pruning of `persisted_auxiliary_info` (or any ledger-history pruning) only removes historical, already-committed data used for replay/indexing purposes after it falls outside the configured pruning window; it cannot retroactively alter or "corrupt" currently-live Move state such as a delegation pool's `total_coins_inactive` or per-delegator inactive shares, because Move execution reads state exclusively from the state merkle tree / current resource storage, never from the auxiliary-info ledger DB. High-throughput transaction submission by an unprivileged attacker can only accelerate version progression (and thus the pruner's target version), not desynchronize or corrupt on-chain accounting invariants that are computed synchronously within `synchronize_delegation_pool` itself.

There is no unprivileged entrypoint or accounting invariant in the stake/delegation/vesting review scope that this pruning behavior can affect. This falls outside the review's required stake-and-lockup impact criteria and belongs to the excluded "storage/pruner internals" family rather than a stake, delegation, or vesting value-corruption path.

### Citations

**File:** storage/aptosdb/src/pruner/ledger_pruner/persisted_auxiliary_info_pruner.rs (L25-35)
```rust
    fn prune(&self, current_progress: Version, target_version: Version) -> Result<()> {
        let mut batch = SchemaBatch::new();
        PersistedAuxiliaryInfoDb::prune(current_progress, target_version, &mut batch)?;
        batch.put::<DbMetadataSchema>(
            &DbMetadataKey::PersistedAuxiliaryInfoPrunerProgress,
            &DbMetadataValue::Version(target_version),
        )?;
        self.ledger_db
            .persisted_auxiliary_info_db()
            .write_schemas(batch)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1843-1857)
```text
        let inactive_shares = pool.inactive_shares.borrow_mut(lockup_cycle);
        // 1. reaching here means delegator owns inactive/pending_inactive shares at OLC `lockup_cycle`
        let redeemed_coins = inactive_shares.redeem_shares(shareholder, shares_to_redeem);

        // if entirely reactivated pending_inactive stake or withdrawn inactive one,
        // re-enable unlocking for delegator by deleting this pending withdrawal
        if (inactive_shares.shares(shareholder) == 0) {
            // 2. a delegator owns inactive/pending_inactive shares only at the OLC of its pending withdrawal
            // 1 & 2: the pending withdrawal itself has been emptied of shares and can be safely deleted
            pool.pending_withdrawals.remove(shareholder);
        };
        // destroy inactive shares pool of past OLC if all its stake has been withdrawn
        if (lockup_cycle.index < pool.observed_lockup_cycle.index && inactive_shares.total_coins() == 0) {
            pool.inactive_shares.remove(lockup_cycle).destroy_empty();
        };
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1917-1993)
```text
    public entry fun synchronize_delegation_pool(
        pool_address: address
    ) acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage {
        assert_delegation_pool_exists(pool_address);
        let pool = borrow_global_mut<DelegationPool>(pool_address);
        let (
            lockup_cycle_ended,
            active,
            pending_inactive,
            commission_active,
            commission_pending_inactive
        ) = calculate_stake_pool_drift(pool);

        // zero `pending_active` stake indicates that either there are no `add_stake` fees or
        // previous epoch has ended and should release the shares owning the existing fees
        let (_, _, pending_active, _) = stake::get_stake(pool_address);
        if (pending_active == 0) {
            // renounce ownership over the `add_stake` fees by redeeming all shares of
            // the special shareholder, implicitly their equivalent coins, out of the active shares pool
            redeem_active_shares(pool, NULL_SHAREHOLDER, MAX_U64);
        };

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

        event::emit_event(
            &mut pool.distribute_commission_events,
            DistributeCommissionEvent {
                pool_address,
                operator: stake::get_operator(pool_address),
                commission_active,
                commission_pending_inactive,
            },
        );

        emit(DistributeCommission {
            pool_address,
            operator: stake::get_operator(pool_address),
            beneficiary: beneficiary_for_operator(stake::get_operator(pool_address)),
            commission_active,
            commission_pending_inactive,
        });

        // advance lockup cycle on delegation pool if already ended on stake pool (AND stake explicitly inactivated)
        if (lockup_cycle_ended) {
            // capture inactive coins over all ended lockup cycles (including this ending one)
            let (_, inactive, _, _) = stake::get_stake(pool_address);
            pool.total_coins_inactive = inactive;

            // advance lockup cycle on the delegation pool
            pool.observed_lockup_cycle.index += 1;
            // start new lockup cycle with a fresh shares pool for `pending_inactive` stake
            pool.inactive_shares.add(pool.observed_lockup_cycle, pool_u64::create_with_scaling_factor(SHARES_SCALING_FACTOR));
        };

        if (is_next_commission_percentage_effective(pool_address)) {
            pool.operator_commission_percentage = borrow_global<NextCommissionPercentage>(
                pool_address
            ).commission_percentage_next_lockup_cycle;
        }
    }
```
