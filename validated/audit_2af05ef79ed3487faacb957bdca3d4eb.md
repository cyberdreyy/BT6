### Title
Node-side signer response tally double-counts a signer's weight into both `total_weight_approved` and `total_weight_rejected` when a signer flips from Reject to Accept for the same block — (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener` maintains an in-memory, per-block tally of aggregated signer weight (`total_weight_approved`, `total_weight_rejected`) that `SigningCoordinator` uses to decide whether a block has reached the 70% approval threshold or the >30% blocking-minority rejection threshold. The guard used to prevent double-counting a signer's weight is asymmetric between the accept and reject code paths, so a signer who rejects a block and later reconsiders and accepts it (a behavior the signer protocol explicitly allows) has its weight counted into **both** buckets simultaneously, unlike the properly reconciled bookkeeping on the signer side.

### Finding Description
In `StackerDBListener::run` (or its handling loop), the Accepted branch adds weight to `total_weight_approved` guarded by `gathered_signatures.contains_key(&slot_id)`, and *always* inserts the slot into the shared `responded_signers` set: [1](#0-0) 

The Rejected branch adds weight to `total_weight_rejected` guarded by `responded_signers.insert(&slot_id)`: [2](#0-1) 

Because `responded_signers` is shared, if a signer sends Accept first, a later Reject from the same signer is correctly ignored (the `insert` returns `false`, so `total_weight_rejected` is never incremented) — that direction is safe. However, the reverse ordering is not: if a signer sends Reject first (adding its weight to `total_weight_rejected` and marking `responded_signers`), and later sends Accept for the same block hash, the Accept guard checks `gathered_signatures`, which was never touched by the Reject path. The check therefore succeeds and the signer's weight is added to `total_weight_approved` too — with no code path ever subtracting the stale weight from `total_weight_rejected`.

By contrast, the actual signer-side bookkeeping in `signerdb.rs` handles this transition correctly and symmetrically: `add_block_signature` explicitly deletes any prior rejection row for that signer/block before inserting the signature — [3](#0-2) 

— and `add_block_rejection_signer_addr` refuses to record a rejection at all if a signature already exists for that signer — [4](#0-3) 

This is precisely the "fee not deducted from the shared pool" bug class from the report: an amount (here, weight) is credited into one bucket (locker rewards / rejected) and, on the state transition, the *other* bucket (caller reward / approved) is filled without subtracting what was already accounted for elsewhere — the two ledgers stop summing correctly. The node-side `stackerdb_listener.rs` tally lacks the reconciliation step that `signerdb.rs` performs, breaking the invariant that a signer's weight belongs to only one side of the vote at any time.

### Impact Explanation
`SigningCoordinator::watch_for_block_totals` (`signer_coordinator.rs`) consumes these tallies directly to make consensus-relevant liveness decisions: [5](#0-4) [6](#0-5) 

Because a signer's stale rejection weight is never cleared once it later accepts, `total_weight_rejected` can remain permanently inflated by weight belonging to signers who have since legitimately signed the block. This can push `total_weight_rejected + weight_threshold > total_weight` even though genuine, live support for the block has since reached (or is converging on) the acceptance threshold, causing the miner to erroneously treat the block as rejected (`NakamotoNodeError::SignersRejected`) and abandon it — a liveness wedge in the mining/block-broadcast path driven purely by a legitimate single-signer vote change, not requiring a majority of colluding signers.

### Likelihood Explanation
This requires only one honest (or even honest-but-slow) signer to reject a proposal and subsequently reconsider and accept it for the same `signer_signature_hash` — a scenario the signer's own design explicitly anticipates (per `handle_block_pre_commit`/`store_and_process_block_rejection` comments about reconsidering rejected blocks once conditions change). No majority collusion, secondary keys, or auth tokens are needed; a single miner-visible signer state flip during normal operation is sufficient to desynchronize the two counters.

### Recommendation
Make the node-side tally symmetric with the signer-side bookkeeping: when a signer's Accept message arrives, if that signer's slot is already present in a "rejected" tracking set, subtract its weight from `total_weight_rejected` (and vice versa is already effectively handled via the shared `responded_signers` guard). Concretely, track rejecters and accepters in independent per-slot sets, and on transition from reject→accept, do:
```rust
if self.rejected_signers.remove(&slot_id) {
    block.total_weight_rejected = block.total_weight_rejected.saturating_sub(signer_entry.weight);
}
```
mirroring the deletion performed by `SignerDb::add_block_signature` in `stacks-signer/src/signerdb.rs`.

### Proof of Concept
1. Node proposes a block; signer `S` (weight `w`) sends `BlockResponse::Rejected` — `total_weight_rejected += w`, `responded_signers.insert(S)`.
2. Per the reconsideration flow (`handle_block_pre_commit` chainstate re-check passing), `S` later sends `BlockResponse::Accepted` for the same `signer_signature_hash`.
3. In `StackerDBListener`, the Accept branch checks `!block.gathered_signatures.contains_key(&slot_id)` — true, since only `responded_signers` (not `gathered_signatures`) was touched by the reject — so `total_weight_approved += w` is executed.
4. Now `total_weight_approved` and `total_weight_rejected` both include `w` for the same signer `S`; nothing in the code path ever reverses the entry made in step 1.
5. `SigningCoordinator::watch_for_block_totals` can subsequently compute `total_weight_rejected + weight_threshold > total_weight` using this stale weight and abort the block as `SignersRejected`, even though `S` (and possibly enough other signers) have since supplied valid acceptance signatures for that block.

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-465)
```rust
                        if !block.gathered_signatures.contains_key(&slot_id) {
                            block.total_weight_approved = block
                                .total_weight_approved
                                .saturating_add(signer_entry.weight);

                            info!("StackerDBListener: Signature Added to block";
                                "signer_signature_hash" => %block_sighash,
                                "signer_pubkey" => signer_pubkey.to_hex(),
                                "signer_slot_id" => slot_id,
                                "signature" => %signature,
                                "signer_weight" => signer_entry.weight,
                                "total_weight_approved" => block.total_weight_approved,
                                "percent_approved" => block.total_weight_approved as f64 / self.total_weight as f64 * 100.0,
                                "total_weight_rejected" => block.total_weight_rejected,
                                "percent_rejected" => block.total_weight_rejected as f64 / self.total_weight as f64 * 100.0,
                                "weight_threshold" => self.weight_threshold,
                                "tenure_extend_timestamp" => tenure_extend_timestamp,
                                "read_count_extend_timestamp" => read_count_extend_timestamp,
                                "server_version" => metadata.server_version,
                            );
                        }
                        block.gathered_signatures.insert(slot_id, signature);
                        block.responded_signers.insert(slot_id);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-518)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-signer/src/signerdb.rs (L1876-1881)
```rust
    ) -> Result<bool, DBError> {
        // Remove any block rejection entry for this signer and block hash
        let del_qry = "DELETE FROM block_rejection_signer_addrs WHERE signer_signature_hash = ?1 AND signer_addr = ?2";
        let del_args = params![block_sighash, signer_addr.to_string()];
        self.db.execute(del_qry, del_args)?;

```

**File:** stacks-signer/src/signerdb.rs (L1929-1940)
```rust
        // If this signer/block already has a signature, do not allow a rejection
        let sig_qry = "SELECT EXISTS(SELECT 1 FROM block_signatures WHERE signer_signature_hash = ?1 AND signer_addr = ?2)";
        let sig_args = params![block_sighash, addr.to_string()];
        let exists = self.db.query_row(sig_qry, sig_args, |row| row.get(0))?;
        if exists {
            warn!("Cannot add block rejection because a signature already exists.";
                "signer_signature_hash" => %block_sighash,
                "signer_address" => %addr,
                "reject_reason" => ?reject_reason
            );
            return Ok(false);
        }
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-519)
```rust
            if block_status
                .total_weight_rejected
                .saturating_add(self.weight_threshold)
                > self.total_weight
            {
                info!(
                    "{}/{} signer weight votes to reject block",
                    block_status.total_weight_rejected, self.total_weight;
                    "signer_signature_hash" => %block_signer_sighash,
                );
                counters.bump_naka_rejected_blocks();
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L541-545)
```rust
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```
