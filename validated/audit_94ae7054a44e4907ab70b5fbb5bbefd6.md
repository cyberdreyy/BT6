## Finding

### Title
Stale/duplicated signer weight tally lets a rejection permanently outlive a later Accept, wedging the miner's block-signature coordinator - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
The node-side `StackerDBListener` that the `SignCoordinator` (in `signer_coordinator.rs`) relies on to decide whether a block has been approved or rejected by the signer set tracks `total_weight_approved` and `total_weight_rejected` using two *different* de-duplication keys (`gathered_signatures` for accepts, `responded_signers` for rejects). This lets a single signer's weight be counted in both tallies for the same block, and the rejection weight is never removed once a signer later switches to Accept, unless the whole round is reset on a full `SignatureTimeout`.

### Finding Description
In `handle_block_response`/message loop of `stacks-node/src/nakamoto_node/stackerdb_listener.rs`:

- On `BlockResponse::Rejected`, weight is added to `block.total_weight_rejected` and gated by `block.responded_signers.insert(slot_id)` (first time only): [1](#0-0) 

- On `BlockResponse::Accepted`, weight is added to `block.total_weight_approved`, but the guard is `!block.gathered_signatures.contains_key(&slot_id)`, i.e. it checks the *signature map*, not `responded_signers`: [2](#0-1) 

Because the two paths use different membership sets, a single signer that first sends a valid signed `Rejected` (weight tallied into `total_weight_rejected`, `slot_id` added to `responded_signers`) and later sends a valid signed `Accepted` for the *same* `block_sighash` (`gathered_signatures` does not yet contain `slot_id`, so the guard passes) will have its weight added a second time into `total_weight_approved`, while `total_weight_rejected` is never decremented. The reverse order (Accept-then-Reject) is safe, because the Reject branch's `responded_signers` guard is already tripped by the earlier Accept — but the Accept branch has no equivalent protection against a preceding Reject.

The only code path that clears `total_weight_rejected` is `reset_rejections`, which is invoked solely on a full proposal `SignatureTimeout`, not when an individual signer updates their vote: [3](#0-2) 

This breaks the intended equality that "aggregated approved/rejected weight" should equal the weight of the signers' *current* (latest) decisions: the sum `total_weight_approved + total_weight_rejected` can now exceed `total_weight`, and a superseded rejection remains counted indefinitely.

### Impact Explanation
`SignCoordinator::send_and_wait_for_signatures` in `signer_coordinator.rs` consumes exactly these two counters to decide the miner's outcome, checking the rejection condition before the approval condition: [4](#0-3) 

Since `total_weight_rejected` never shrinks after a signer flips their vote to Accept (short of a full timeout/reset), one signer's stale rejection persists and can, combined with the natural rejection weight of other signers near the blocking-minority boundary, push `total_weight_rejected.saturating_add(weight_threshold) > total_weight` even though the *current* votes (after the flip) would otherwise legitimately reach `total_weight_approved >= weight_threshold`. Because the rejection branch is checked first and short-circuits, the miner returns `NakamotoNodeError::SignersRejected`, permanently excludes/quarantines transactions, and abandons an otherwise-valid, sufficiently-approved block for that tenure attempt — a liveness wedge triggerable by a single signer's own key via ordinary gossip, matching the "wedged into never signing valid blocks / acting on a corrupted tally" impact category.

### Likelihood Explanation
Any single signer can trigger the ordering (Reject then Accept for the same block hash) simply by broadcasting two validly signed `BlockResponse` StackerDB chunks; a signer's own state machine can legitimately produce this sequence (e.g., a transient reorg/consensus-hash mismatch causes an initial reject, and a subsequent re-evaluation — as seen in the pre-commit re-check logic in `stacks-signer/src/v0/signer.rs` — causes it to accept once conditions clear). No majority or other signers' keys are required; the miner's coordinator alone is affected.

### Recommendation
Use a single, shared "responded" set (or store each signer's *current* decision and its weight) so that a switch from Reject to Accept (or vice versa) atomically removes the signer's weight from the stale tally before adding it to the new one, e.g. gate the `Accepted` branch on the same `responded_signers` set used by `Rejected`, and when a signer's vote changes, subtract their weight from the previous tally before adding it to the new one.

### Proof of Concept
1. Miner proposes a block; `StackerDBListenerComms::insert_block` initializes `total_weight_approved = 0`, `total_weight_rejected = 0`.
2. Signer S (slot `k`, weight `w`) sends `BlockResponse::Rejected` for the block → `total_weight_rejected += w`, `responded_signers = {k}` (lines 515-519).
3. Signer S later sends `BlockResponse::Accepted` for the same `block_sighash` (e.g., after re-validating and finding the earlier objection resolved) → since `gathered_signatures` doesn't contain `k`, `total_weight_approved += w` as well (lines 443-465). `total_weight_rejected` remains `w`.
4. In `signer_coordinator.rs`, both counters are now nonzero for signer S's weight simultaneously; if other signers' organic rejection weight brings `total_weight_rejected + weight_threshold` over `total_weight` before/while `total_weight_approved` also reaches `weight_threshold`, the rejection branch fires first and the miner aborts the block via `NakamotoNodeError::SignersRejected`, even though the up-to-date votes would have approved it.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-519)
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
