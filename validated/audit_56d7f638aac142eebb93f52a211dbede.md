### Title
Signer vote flip (reject-then-accept) lets a single signer's weight be double-booked into both `total_weight_approved` and `total_weight_rejected` - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
The node-side `StackerDBListener` tallies signer weight for a block proposal into two supposedly mutually-exclusive buckets, `total_weight_approved` and `total_weight_rejected`, gated by two *different* membership sets (`gathered_signatures` for accepts, `responded_signers` shared by both). A single signer who first rejects and later accepts the same block (both are validly signed `BlockResponse` messages, sendable via ordinary StackerDB gossip) gets their weight counted into `total_weight_rejected` on the reject, and then *again* into `total_weight_approved` on the later accept, because the accept path never checks `responded_signers`. The signer's weight is now double-booked across both tallies, and `total_weight_rejected` is never decremented — mirroring the Wildcat bug where a value that should have been "frozen"/moved out of a bucket keeps contributing to it as if unchanged.

### Finding Description
In `store_and_process_block_response` handling, the two branches use inconsistent gating:

Accept branch — gates weight addition on `gathered_signatures`, not on `responded_signers`: [1](#0-0) 

Reject branch — gates weight addition on `responded_signers`: [2](#0-1) 

Walk-through for signer `S` with weight `w`:
1. `S` sends `BlockResponse::Rejected`. `block.responded_signers.insert(slot_id)` returns `true` (first time) → `total_weight_rejected += w`.
2. `S` (equivocating, or replaying an earlier signed accept — both are gossip messages a single slot can emit) later sends `BlockResponse::Accepted` for the *same* block. The gate is `!block.gathered_signatures.contains_key(&slot_id)`, which is `true` because `S`'s slot was never inserted into `gathered_signatures` during step 1 (only `responded_signers` was touched by the reject path). So `total_weight_approved += w` fires, `gathered_signatures.insert(slot_id, signature)` and `responded_signers.insert(slot_id)` (no-op, already present).

Result: `total_weight_approved + total_weight_rejected` now exceeds the true sum of *distinct* signer weight that responded, because `w` was added to both buckets and `total_weight_rejected` is never reduced when `S` flips to accept. The invariant "a signer's weight counts toward at most one of approved/rejected" is broken.

Note the reverse order (accept-then-reject) *is* protected, because the reject branch's `responded_signers.insert` will return `false` (already inserted by the accept branch) and skip incrementing `total_weight_rejected` — this asymmetry confirms the bug is a genuine oversight rather than intended behavior.

### Impact Explanation
This directly corresponds to the report's Critical category "a rejection recounted as an accept": the consensus-relevant weight for a block becomes internally inconsistent. Downstream, `stacks-node/src/nakamoto_node/signer_coordinator.rs` polls this same `BlockStatus` and makes accept/reject decisions from these two counters independently: [3](#0-2) 

Because `total_weight_rejected` can remain permanently inflated by weight that has since "moved" to accept, and `total_weight_approved` simultaneously reflects the same signer's later acceptance, it becomes possible for both the reject-threshold check (`total_weight_rejected + weight_threshold > total_weight`, checked first) and the accept-threshold check (`total_weight_approved >= weight_threshold`) to be satisfied from overlapping/double-counted weight, corrupting which branch is authoritative and letting a stale rejection or a wrongly-early acceptance decision be reached with less genuinely-distinct signer weight than the 70%/30% design assumes.

### Likelihood Explanation
Triggerable by exactly one signer slot via ordinary StackerDB gossip (send `Rejected` then later `Accepted` for the same `signer_signature_hash`), requiring no majority collusion, no key compromise, and no auth token. It only requires the attacker-controlled signer to emit two validly-signed, differently-typed responses for the same block.

### Recommendation
Gate both branches on the same membership set (e.g., always check/insert into `responded_signers` first before mutating either weight counter), and when a signer's vote flips, subtract their weight from the previous bucket before adding it to the new one, so `total_weight_approved` and `total_weight_rejected` remain a true partition of responded signer weight.

### Proof of Concept
1. Node proposes block `B`; signer `S` (weight `w`) sends `BlockResponse::Rejected(B)`. `total_weight_rejected += w` (via `responded_signers.insert` succeeding at [2](#0-1) ).
2. `S` sends `BlockResponse::Accepted(B)` (a validly signed acceptance for the same hash). The gate `!gathered_signatures.contains_key(&slot_id)` passes because step 1 never touched `gathered_signatures`, so `total_weight_approved += w` fires at [4](#0-3) .
3. Now `total_weight_rejected` still includes `w` (never decremented) and `total_weight_approved` also includes `w`. Both thresholds in `signer_coordinator.rs`'s polling loop can be satisfied using this overlapping weight, corrupting the accept/reject decision for block `B`.

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
