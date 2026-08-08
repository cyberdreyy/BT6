### Title
RPC signature-status/confirmation queries can return stale data from a Bank that has already been dumped by a duplicate-slot (re-org) purge - ([File: core/src/replay_stage.rs])

### Summary
The Royco `RecipeKernel` bug is a re-org class issue: a client resolves an object by a numeric/id reference (`targetMarketID`) that is expected to be immutable, but a chain re-org lets that reference silently point to different, attacker-controlled state, and the offer-fulfillment code trusts the id instead of verifying that the underlying data hasn't changed. The Agave analog is in the JSON-RPC / bank-forks read path around `get_signature_status_slot`/`get_transaction_status`: an RPC handler resolves a `Bank` object by commitment level and then looks up a signature's status in that bank's status cache, but the `Bank` reference can be captured *before* `ReplayStage` detects and purges a duplicate/re-orged slot, so the RPC response can be served from state belonging to a fork that the validator has already discarded.

### Finding Description
`ReplayStage::purge_unconfirmed_slot()` handles the “re-org happened, this slot/fork was wrong” case. It clears the fork's `Bank`s from `BankForks`, clears the status cache entries for the purged slot (`root_bank.clear_slot_signatures(slot)`), and purges accounts and blockstore data for that slot [1](#0-0) . The code itself documents that this is racy with respect to concurrent readers:

```
// Clear the slot signatures from status cache for this slot.
// TODO: What about RPC queries that had already cloned the Bank for this slot
// and are looking up the signature for this slot?
root_bank.clear_slot_signatures(slot);
``` [2](#0-1) 

RPC handlers such as `get_transaction_status`/`get_signature_status_slot` obtain an `Arc<Bank>` for a commitment level and then query `bank.get_signature_status_slot(&signature)`, which reads `self.status_cache.read().unwrap().get_status_any_blockhash(signature, &self.ancestors)` [3](#0-2) . This is exposed via `RpcClient::get_signature_statuses`/`get_transaction` style handlers in `rpc/src/rpc.rs` [4](#0-3) .

If a client (or the RPC subsystem internally, e.g. `send_and_confirm_transaction`) grabs the `Bank` handle for a given commitment right before `ReplayStage` detects that the slot is a duplicate and calls `purge_unconfirmed_slot`, the query can complete against a `Bank`/status-cache state that describes a fork the validator is in the process of discarding because of the re-org, i.e., analogous to the Royco case where an id (here: the “commitment level -> bank -> slot” resolution) no longer corresponds to the state the caller/validator ultimately settles on. The existing regression test `test_purge_unconfirmed_duplicate_slot` demonstrates the state transition explicitly: before the purge, `bank7.get_signature_status(&transfer_sig)` reports a status; after purge it is `None`, and `bank7.get_balance` reverts to the pre-transfer balance [5](#0-4)  — showing that any reader that ran the same query in the (unbounded) window between the fork being replayed and the purge completing would observe transient, fork-inconsistent status/balance data that is discarded moments later.

### Impact Explanation
This does not enable fund theft the way the Royco bug does, but it does map onto “wrong-slot/fork/account data returned from a query”: an unprivileged RPC client can be told a transaction succeeded / has N confirmations, or an account balance reflecting a transfer, that in fact belongs to a fork the validator subsequently rules invalid due to a duplicate-slot re-org. Because the validator itself flags this as an open, acknowledged gap (the TODO), it is a genuine, unresolved correctness issue rather than a purely theoretical one.

### Likelihood Explanation
The window is bounded by the time between (a) a client fetching a `Bank` handle via `self.bank(commitment)` in `rpc/src/rpc.rs` and completing `get_signature_status_slot`, and (b) `ReplayStage::purge_unconfirmed_slot` running for that slot after ancestor-hashes/duplicate detection. Duplicate slots are not attacker-controlled at will (they require an actual chain re-org / duplicate block scenario), so likelihood is low-to-moderate and depends on cluster conditions rather than a single crafted RPC call, consistent with the “race condition acknowledged in code” nature of the finding rather than a deterministic exploit.

### Recommendation
Rather than trusting a `Bank` handle in isolation, RPC/status-cache reads that span the purge boundary should re-validate that the resolved slot's bank hash is still present in `BankForks`/blockstore (i.e., not part of a subsequently purged/duplicate fork) before returning the query result, or the purge path should invalidate/expose a generation marker that RPC checks after the lookup, closing the gap called out by the existing TODO in `purge_unconfirmed_slot`.

### Proof of Concept
Not independently reproducible from the index alone; the existing unit test `test_purge_unconfirmed_duplicate_slot` in `core/src/replay_stage/tests.rs` demonstrates the pre/post-purge status-cache divergence that underlies the race window described above [5](#0-4) ; constructing an actual timing PoC against live RPC would require driving a real duplicate-slot event concurrently with an RPC query, which is outside what can be verified via static code search.

### Citations

**File:** core/src/replay_stage.rs (L2284-2298)
```rust
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

**File:** runtime/src/bank.rs (L5330-5333)
```rust
    pub fn get_signature_status_slot(&self, signature: &Signature) -> Option<(Slot, Result<()>)> {
        let rcache = self.status_cache.read().unwrap();
        rcache.get_status_any_blockhash(signature, &self.ancestors)
    }
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

**File:** core/src/replay_stage/tests.rs (L2748-2799)
```rust
    assert!(bank7.get_signature_status(&vote_tx.signatures[0]).is_some());

    // Both signatures should exist in status cache
    assert!(bank7.get_signature_status(&vote_tx.signatures[0]).is_some());
    assert!(bank7.get_signature_status(&transfer_sig).is_some());

    // Give all slots a bank hash but mark slot 7 dead
    for i in 0..=6 {
        blockstore.insert_bank_hash(i, Hash::new_unique(), false);
    }
    blockstore
        .set_dead_slot(7)
        .expect("Failed to mark slot as dead in blockstore");

    // Purging slot 5 should purge only slots 5 and its descendant 6. Since 7 is already dead,
    // it gets reset but not removed
    ReplayStage::purge_unconfirmed_slot(
        5,
        &mut ancestors,
        &mut descendants,
        &mut progress,
        &root_bank,
        &bank_forks,
        &blockstore,
    );
    for i in 5..=7 {
        assert!(bank_forks.read().unwrap().get(i).is_none());
        assert!(progress.get(&i).is_none());
    }
    for i in 0..=4 {
        assert!(bank_forks.read().unwrap().get(i).is_some());
        assert!(progress.get(&i).is_some());
    }

    // Blockstore should have been cleared
    for slot in &[5, 6] {
        assert!(!blockstore.is_full(*slot));
        assert!(!blockstore.is_dead(*slot));
        assert!(blockstore.get_slot_entries(*slot, 0).unwrap().is_empty());
    }

    // Slot 7 was marked dead before, should no longer be marked
    assert!(!blockstore.is_dead(7));
    assert!(!blockstore.get_slot_entries(7, 0).unwrap().is_empty());

    // Should not be able to find signature in slot 5 for previously
    // processed transactions
    assert!(bank7.get_signature_status(&vote_tx.signatures[0]).is_none());
    assert!(bank7.get_signature_status(&transfer_sig).is_none());

    // Getting balance should return the old balance (accounts were cleared)
    assert_eq!(bank7.get_balance(&sender), old_balance);
```
