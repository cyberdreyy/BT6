Confirmed: the reject-branch check (`total_weight_rejected.saturating_add(weight_threshold) > total_weight`) is evaluated first in `signer_coordinator.rs::get_block_status`, before the approval branch (`total_weight_approved >= weight_threshold`) — so a stale/inflated rejection count can force a "reject" verdict even on a block that has already gathered enough real signatures.

### Title
Stale rejection weight is never cleared when a signer switches to acceptance, letting a single signer's flip-flop force a fully-signed block into rejection - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListener` tracks two independent tallies per block, `total_weight_approved` and `total_weight_rejected`, inside `BlockStatus` [1](#0-0) . When a `BlockResponse::Rejected` arrives, the signer's weight is added to `total_weight_rejected` and the slot is recorded in `responded_signers` [2](#0-1) . When the same signer later sends `BlockResponse::Accepted` for the same block, the accept handler only checks `gathered_signatures.contains_key(&slot_id)` before adding weight to `total_weight_approved` [3](#0-2)  — it never inspects `responded_signers`/`total_weight_rejected` to retract the earlier rejection weight. The signer's weight therefore ends up counted in *both* buckets simultaneously, so `total_weight_approved + total_weight_rejected` can exceed `total_weight` for a single block.

This is precisely the bug class the project already fixed once, on the signer side: the stacks-signer `SignerDb::add_block_rejection_signer_addr`/`add_block_signature` pair explicitly deletes any prior rejection row when a signature is recorded, and refuses to add a rejection if a signature already exists [4](#0-3) [5](#0-4) , and the CHANGELOG documents this as an intentional fix: "Do not count both a block acceptance and a block rejection for the same signer/block" [6](#0-5) . The node-side `StackerDBListener`, which the mining `SignCoordinator` relies on to decide when a block has enough weight, does not apply the same guard.

### Finding Description
`SignCoordinator::get_block_status` in `signer_coordinator.rs` polls `BlockStatus` and evaluates, in order: (1) whether `total_weight_rejected + weight_threshold > total_weight` (block rejected), then (2) whether `total_weight_approved >= weight_threshold` (block accepted) [7](#0-6) . Because the rejection check is evaluated first and is an `if`/`else if`, a spuriously-inflated `total_weight_rejected` can win the race even when the approval side has genuinely reached the 70% threshold with valid signatures.

A single signer (one slot, ordinary gossip, no majority or extra keys required) can trigger this:
1. Signer S initially rejects the proposed block (e.g., due to a stale local view), adding weight `w` to `total_weight_rejected` and marking its slot in `responded_signers`.
2. Signer S subsequently reconsiders and sends a valid `BlockResponse::Accepted` for the same block (this reconsideration path is normal and expected — see `reject_then_accept` test on the signer side and the CHANGELOG note referenced above; on the signer side switching a vote is legitimate).
3. The `Accepted` handler in `stackerdb_listener.rs` adds `w` to `total_weight_approved` because `gathered_signatures` doesn't yet contain S's slot — but it never removes `w` from `total_weight_rejected`, and `reset_rejections` is only invoked on the coordinator's own timeout path, not on receipt of a late acceptance [8](#0-7) .
4. If other, honest signers' combined weight (excluding S) is already at or above the blocking-minority threshold (`total_weight - weight_threshold`) purely from legitimate, independent rejections while S's stale `w` is also baked in — or if S's own stale weight tips an otherwise-borderline rejection tally over the line — `total_weight_rejected` can cross the blocking-minority line even though the *current* set of signers (with S counted as an accepter, as it should be) does not actually block the block. Simultaneously `total_weight_approved` can independently reach `weight_threshold` from real, currently-valid signatures (including S's).
5. Because the coordinator's `if` checks rejection before approval, the node discards a block that has legitimately collected enough real signatures to be accepted, treating it as globally rejected instead, and reacts by excluding transactions and re-proposing [9](#0-8) .

### Impact Explanation
This breaks the equality that should hold in the coordinator's tally: a signer's weight must be attributed to exactly one side of a decision (approve xor reject) at any given time, not both. The practical consequence is a liveness wedge on block production driven by a single signer's benign vote-switch: a block that has genuinely reached the 70% approval threshold with valid signatures can be discarded as "rejected" by the mining coordinator, forcing unnecessary re-proposals and transaction exclusion (`temporarily_excluded_txids`/`permanently_excluded_txids`). This matches the "signer wedged... acting on stale/incorrect tallies" class of High-severity issue: correct, sufficiently-signed blocks can be repeatedly discarded due to stale rejection accounting that no longer reflects the signer's actual, current vote.

### Likelihood Explanation
The trigger requires only one signer switching its own vote from reject to accept for the same block within the same proposal round — a scenario the codebase's own test suite and CHANGELOG show is a normal, expected occurrence (e.g., "For some rejection reasons, a signer will reconsider a block proposal that it previously rejected") [10](#0-9) . No majority collusion, extra keys, or privileged access is needed; ordinary StackerDB gossip delivering a rejection followed by an acceptance from the same signer is sufficient to desynchronize the two counters.

### Recommendation
In `stackerdb_listener.rs`, when processing a `BlockResponse::Accepted` message, check whether the signer's slot is already present with a recorded rejection and, if so, subtract that signer's weight from `total_weight_rejected` (and vice versa for late rejections after an acceptance, matching the signer-side rule that a signature always wins and rejections are not accepted once a signature exists). This should mirror the already-fixed logic in `stacks-signer/src/signerdb.rs`'s `add_block_signature`/`add_block_rejection_signer_addr` pair, ensuring a signer's weight is attributed to only one bucket at a time so that `total_weight_approved + total_weight_rejected` never exceeds `total_weight` for the same block.

### Proof of Concept
1. Configure a `SignerTest`/`StackerDBListener` scenario with signers `S1..Sn` and weight thresholds as in existing test infrastructure (`stacks-node/src/tests/signer/v0/mod.rs`).
2. Have signer `S1` send `BlockResponse::Rejected` for a proposed block `B`; observe `total_weight_rejected` incremented by `S1`'s weight and `S1`'s slot recorded in `responded_signers`.
3. Have the remaining signers `S2..Sn` send `BlockResponse::Accepted` for `B` with valid signatures, reaching `total_weight_approved >= weight_threshold` on their own.
4. Have `S1` reconsider and send `BlockResponse::Accepted` for `B` (a legitimate reconsideration).
5. Observe that `block_status.total_weight_rejected` still includes `S1`'s stale weight (never cleared), and, depending on the configured weights, `total_weight_rejected.saturating_add(weight_threshold) > total_weight` can now also hold true, causing `SignCoordinator::get_block_status` to return `Err(NakamotoNodeError::SignersRejected { .. })` instead of `Ok(gathered_signatures)`, even though real signatures satisfying the 70% threshold are already present in `block_status.gathered_signatures`.

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L70-82)
```rust
#[derive(Debug, Clone)]
pub struct BlockStatus {
    /// Set of the slot ids of signers who have responded
    pub responded_signers: HashSet<u32>,
    /// Map of the slot id of signers who have signed the block and their signature
    pub gathered_signatures: BTreeMap<u32, MessageSignature>,
    /// Total weight of signers who have signed the block
    pub total_weight_approved: u32,
    /// Total weight of signers who have rejected the block
    pub total_weight_rejected: u32,
    /// Per-txid rejection tracking from signers
    pub failed_txids: HashMap<Txid, FailedTxInfo>,
}
```

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L706-723)
```rust
    /// Reset rejections for a block proposal.
    /// This is used when a block proposal times out and we need to retry it by
    /// clearing the block's rejections. Block approvals cannot be cleared
    /// because an old approval could always be used to make a block reach
    /// the approval threshold.
    pub fn reset_rejections(&self, signer_sighash: &Sha512Trunc256Sum) {
        let (lock, _cvar) = &*self.blocks;
        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");
        if let Some(block) = blocks.get_mut(signer_sighash) {
            block.responded_signers.clear();
            block.total_weight_rejected = 0;

            // Add approving signers back to the responded signers set
            for (slot_id, _) in block.gathered_signatures.iter() {
                block.responded_signers.insert(*slot_id);
            }
        }
    }
```

**File:** stacks-signer/src/signerdb.rs (L1870-1906)
```rust
    /// Record an observed block signature
    pub fn add_block_signature(
        &self,
        block_sighash: &Sha512Trunc256Sum,
        signer_addr: &StacksAddress,
        signature: &MessageSignature,
    ) -> Result<bool, DBError> {
        // Remove any block rejection entry for this signer and block hash
        let del_qry = "DELETE FROM block_rejection_signer_addrs WHERE signer_signature_hash = ?1 AND signer_addr = ?2";
        let del_args = params![block_sighash, signer_addr.to_string()];
        self.db.execute(del_qry, del_args)?;

        // Insert the block signature
        let qry = "INSERT OR IGNORE INTO block_signatures (signer_signature_hash, signer_addr, signature) VALUES (?1, ?2, ?3);";
        let args = params![
            block_sighash,
            signer_addr.to_string(),
            serde_json::to_string(signature).map_err(DBError::SerializationError)?
        ];
        let rows_added = self.db.execute(qry, args)?;

        let is_new_signature = rows_added > 0;
        if is_new_signature {
            debug!("Added block signature.";
                "signer_signature_hash" => %block_sighash,
                "signer_address" => %signer_addr,
                "signature" => %signature
            );
        } else {
            debug!("Duplicate block signature.";
                "signer_signature_hash" => %block_sighash,
                "signer_address" => %signer_addr,
                "signature" => %signature
            );
        }
        Ok(is_new_signature)
    }
```

**File:** stacks-signer/src/signerdb.rs (L1922-1941)
```rust
    /// Record an observed block rejection_signature
    pub fn add_block_rejection_signer_addr(
        &self,
        block_sighash: &Sha512Trunc256Sum,
        addr: &StacksAddress,
        reject_reason: RejectReasonPrefix,
    ) -> Result<bool, DBError> {
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

**File:** stacks-signer/CHANGELOG.md (L134-134)
```markdown
- Do not count both a block acceptance and a block rejection for the same signer/block. Also ignore repeated responses (mainly for logging purposes).
```

**File:** stacks-signer/CHANGELOG.md (L178-180)
```markdown
### Changed

- For some rejection reasons, a signer will reconsider a block proposal that it previously rejected ([#5880](https://github.com/stacks-network/stacks-core/pull/5880))
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-545)
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

                // Only act on failed txids that a blocking minority (>30% weight) agrees on
                let blocking_minority = self.total_weight.saturating_sub(self.weight_threshold);
                let mut temporarily_excluded_txids = HashSet::new();
                let mut permanently_excluded_txids = HashSet::new();
                for (txid, info) in &block_status.failed_txids {
                    if info.total_weight > blocking_minority {
                        // Do not perma ban txids that only a small minority of signers reported as problematic
                        // But make sure its removed from the next block proposal
                        if info.problematic_weight > blocking_minority {
                            permanently_excluded_txids.insert(txid.clone());
                        } else {
                            temporarily_excluded_txids.insert(txid.clone());
                        }
                    }
                }

                return Err(NakamotoNodeError::SignersRejected {
                    temporarily_excluded_txids,
                    permanently_excluded_txids,
                });
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```
