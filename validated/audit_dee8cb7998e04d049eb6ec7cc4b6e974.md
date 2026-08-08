### Title
Duplicate-slot purge race lets `getTransaction`/`getSignatureStatuses` and cloned banks report transaction status from a purged/replaced fork - ([File: core/src/replay_stage.rs])

### Summary
The external report's bug class is: a client relies on a deterministically-derived address/identifier that is silently repointed to a different entity after a reorg, so the client's subsequent action lands on the wrong target. The closest reachable analog in agave is the handling of **duplicate-slot resolution** during replay: when the cluster determines that a previously replayed version of a slot was wrong (a duplicate block), `ReplayStage::purge_unconfirmed_slot` purges that slot's bank/blockstore state and the correct version is later replayed into the *same slot number*. An unprivileged RPC caller that has already resolved a `Bank`/status-cache entry for that slot number, or is mid-read against the blockstore for that slot, can be handed data describing a block that no longer exists in the accepted fork - i.e., wrong-fork data returned for a slot number, analogous to Bob being redirected to `daoB`'s address instead of `daoA`'s.

### Finding Description
`ReplayStage::purge_unconfirmed_slot` is invoked whenever a slot is identified as containing a duplicate/incorrect block. It removes the bank for `slot_to_purge` (and descendants) from `BankForks`, clears the accounts for that slot, and clears the blockstore's transaction/status data for the slot so that a corrected version of the block can be inserted and replayed under the same slot number: [1](#0-0) 

The code explicitly documents an unresolved race for concurrent RPC readers:
```
// Clear the slot signatures from status cache for this slot.
// TODO: What about RPC queries that had already cloned the Bank for this slot
// and are looking up the signature for this slot?
``` [2](#0-1) 

On the RPC side, `JsonRpcRequestProcessor::get_transaction_status` and `get_signature_statuses` resolve a `Bank` via `self.bank(commitment)`, then read the in-memory status cache off that `Arc<Bank>`: [3](#0-2) 

and `get_transaction` similarly snapshots `confirmed_bank` before dispatching a blocking blockstore read keyed by slot and signature: [4](#0-3) 

If an RPC caller has already obtained (cloned) the `Arc<Bank>` for the duplicate version of a slot, or is in the middle of the blockstore lookup for that slot, before `purge_unconfirmed_slot` removes the bank from `BankForks` and clears the blockstore's `transaction_status_cf`/signatures for that slot, that caller's outstanding query can return the transaction status/contents associated with the discarded (duplicate) version of the block rather than the version that is ultimately accepted by the cluster for that slot number. This is analogous to the reported bug: the identifier (slot number, in place of the deterministic contract address) that a client relies on gets silently repointed to different content by a fork-resolution event that the client had no visibility into.

### Impact Explanation
This falls under "wrong-slot/fork/account data returned" from a query, which is one of the explicitly accepted impact categories. A wallet, exchange, or downstream automation that queries `getTransaction`/`getSignatureStatuses` for a signature can be told a transaction is confirmed in slot `N` with a particular status/instruction/account-balance data, while the block that is ultimately canonical for slot `N` is a different (duplicate-resolved) block. Consumers acting on that stale confirmation (e.g., releasing funds, crediting a deposit) could act on data from a fork that the network subsequently discarded - a direct, unprivileged-user-observable case of wrong-fork data being returned from a single RPC call.

### Likelihood Explanation
Duplicate-slot detection and purge are triggered by legitimate consensus/duplicate-detection mechanisms (not attacker-controlled at will), and the window is the time between a `purge_unconfirmed_slot` cleanup and any RPC caller having already begun (but not finished) a lookup keyed on the purged slot. The developers' own TODO comment indicates this is a known, unaddressed gap rather than a purely theoretical concern, but exploiting/observing it reliably requires the natural occurrence of a duplicate-slot event, which is not attacker-triggerable on demand from an unprivileged RPC client alone.

### Recommendation
- Ensure that `purge_unconfirmed_slot`'s bank removal and blockstore status-cache clearing are made atomic with respect to any concurrently executing RPC read for the same slot (e.g., by taking a read guard/generation counter that RPC handlers must validate before returning a response), so an RPC response is invalidated/retried rather than silently served from a discarded bank/blockstore state.
- Alternatively, tag each `Bank`/blockstore transaction-status entry with a fork/generation identifier and have RPC handlers re-validate that the resolved slot is still part of the current best fork immediately before returning the response to the caller.
- Address the existing TODO in `purge_unconfirmed_slot` directly, since it already flags this exact class of race.

### Proof of Concept
Not independently reproducible from the code alone within this scan; the report is grounded in the explicit TODO/race window in `ReplayStage::purge_unconfirmed_slot` and the corresponding RPC read paths (`rpc/src/rpc.rs::get_transaction_status`, `get_signature_statuses`, `get_transaction`) that snapshot a `Bank`/blockstore state without validating fork-liveness at response time. Reproduction would require orchestrating a duplicate-slot event concurrently with an in-flight RPC query for a signature in that slot, which requires a live multi-node cluster and is not something verifiable via static code reading alone.

### Citations

**File:** core/src/replay_stage.rs (L2260-2298)
```rust
        let banks_to_remove: Vec<_> = {
            let bank_forks = bank_forks.read().unwrap();
            slot_descendants
                .iter()
                .chain(std::iter::once(&slot_to_purge))
                .filter_map(|slot| bank_forks.get_with_scheduler(*slot))
                .collect()
        };
        for bank in banks_to_remove {
            let _ = bank.wait_for_completed_scheduler();
        }

        // Grab the Slot and BankId's of the banks we need to purge, then clear the banks
        // from BankForks
        let (slots_to_purge, removed_banks): (Vec<(Slot, BankId)>, Vec<BankWithScheduler>) = {
            let mut w_bank_forks = bank_forks.write().unwrap();
            w_bank_forks.dump_slots(
                slot_descendants
                    .iter()
                    .chain(std::iter::once(&slot_to_purge)),
                true,
            )
        };

        // Clear the accounts for these slots so that any ongoing RPC scans fail.
        // These have to be atomically cleared together in the same batch, in order
        // to prevent RPC from seeing inconsistent results in scans.
        root_bank.remove_unrooted_slots(&slots_to_purge);

        // Once the slots above have been purged, now it's safe to remove the banks from
        // BankForks, allowing the Bank::drop() purging to run and not race with the
        // `remove_unrooted_slots()` call.
        drop(removed_banks);

        for (slot, slot_id) in slots_to_purge {
            // Clear the slot signatures from status cache for this slot.
            // TODO: What about RPC queries that had already cloned the Bank for this slot
            // and are looking up the signature for this slot?
            root_bank.clear_slot_signatures(slot);
```

**File:** rpc/src/rpc.rs (L1731-1766)
```rust
    fn get_transaction_status(
        &self,
        signature: Signature,
        bank: &Bank,
    ) -> Option<TransactionStatus> {
        let (slot, status) = bank.get_signature_status_slot(&signature)?;

        let optimistically_confirmed_bank = self.bank(Some(CommitmentConfig::confirmed()));
        let optimistically_confirmed =
            optimistically_confirmed_bank.get_signature_status_slot(&signature);

        let r_block_commitment_cache = self.block_commitment_cache.read().unwrap();
        let confirmations = if r_block_commitment_cache.root() >= slot
            && is_finalized(&r_block_commitment_cache, bank, &self.blockstore, slot)
        {
            None
        } else {
            r_block_commitment_cache
                .get_confirmation_count(slot)
                .or(Some(0))
        };
        let err = status.clone().err();
        Some(TransactionStatus {
            slot,
            status,
            confirmations,
            err,
            confirmation_status: if confirmations.is_none() {
                Some(TransactionConfirmationStatus::Finalized)
            } else if optimistically_confirmed.is_some() {
                Some(TransactionConfirmationStatus::Confirmed)
            } else {
                Some(TransactionConfirmationStatus::Processed)
            },
        })
    }
```

**File:** rpc/src/rpc.rs (L1783-1799)
```rust
        let confirmed_bank = self.bank(Some(CommitmentConfig::confirmed()));
        let confirmed_transaction = self
            .runtime
            .spawn_blocking({
                let blockstore = Arc::clone(&self.blockstore);
                let confirmed_bank = Arc::clone(&confirmed_bank);
                move || {
                    if commitment.is_confirmed() {
                        let highest_confirmed_slot = confirmed_bank.slot();
                        blockstore.get_complete_transaction(signature, highest_confirmed_slot)
                    } else {
                        blockstore.get_rooted_transaction(signature)
                    }
                }
            })
            .await
            .expect("Failed to spawn blocking task");
```
