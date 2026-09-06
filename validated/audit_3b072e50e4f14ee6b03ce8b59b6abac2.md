### Title
Stale Rejection Weight Not Cleared on Vote Flip Causes Valid, Sufficiently-Signed Blocks to Be Treated as Rejected - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener` tracks two independent weight tallies per block — `total_weight_approved` and `total_weight_rejected` — used by `SignCoordinator::gather_signatures` to decide whether a block was globally accepted or rejected. When a signer first rejects a block and later changes its mind and accepts it (a scenario the signer-side design explicitly treats as legitimate — "a rejection is a revocable opinion"), the node-side aggregator adds that signer's weight to `total_weight_approved` but never removes it from `total_weight_rejected`. This is the same class of bug as the reported "lack of adjustment for refunded rewards": a value that moves from one accounting bucket to another is added to the destination but never subtracted from the source, breaking the aggregated-weight vs. verified-accepts equality.

### Finding Description
In `stacks-node/src/nakamoto_node/stackerdb_listener.rs`, the `Rejected` branch gates the weight addition on `block.responded_signers.insert(slot_id)`: [1](#0-0) 
and the `Accepted` branch gates its weight addition on a *different* set, `block.gathered_signatures.contains_key(&slot_id)`, then unconditionally inserts into `responded_signers`: [2](#0-1) 

Because these two additions are gated on different keys (`responded_signers` vs. `gathered_signatures`), the following sequence double-counts a signer's weight:
1. Signer S rejects the block → `responded_signers.insert(slot_id)` succeeds → `total_weight_rejected += weight(S)`.
2. Signer S later reconsiders and accepts the same block → `gathered_signatures` does not yet contain `slot_id`, so `total_weight_approved += weight(S)` — weight is added again, and nothing removes it from `total_weight_rejected`.

`total_weight_rejected` therefore never decreases even though S no longer opposes the block. In `stacks-node/src/nakamoto_node/signer_coordinator.rs`, the rejection check is evaluated **before** the acceptance check: [3](#0-2) 

so a stale, no-longer-current rejection weight can push `total_weight_rejected + weight_threshold > total_weight` and cause the coordinator to return `NakamotoNodeError::SignersRejected`, even in a state where `total_weight_approved >= weight_threshold` is also true (i.e., the block genuinely has enough current signatures to be accepted).

The reverse direction (accept, then later reject) is correctly guarded: since the `Accepted` branch unconditionally inserts into `responded_signers`, a later rejection from the same signer is a no-op because `responded_signers.insert(slot_id)` returns `false`. This asymmetry confirms the bug is a missing "decrement/clear the old bucket" step specifically on the reject→accept transition, exactly mirroring the omitted `incentive.remainingReward -= refunded` step in the referenced report.

### Impact Explanation
This breaks the "aggregated-weight vs. verified-accepts" equality: the node's rejection tally can remain inflated with weight from a signer who has since verifiably accepted. Because the rejection branch is checked first in `gather_signatures`, this can cause the coordinator to declare a well-formed, sufficiently-signed block as rejected (denial of a valid block), stalling that mining attempt and forcing a retry/reproposal — a liveness degradation triggered by the ordinary, sanctioned behavior of a single signer reconsidering its vote (no majority collusion required).

### Likelihood Explanation
Reachable by any single signer that legitimately reverses an initial rejection into an acceptance for the same block (e.g. it initially rejected due to a transient validation issue and, per the signer's own documented behavior, "reconsiders" and later signs) — this is a normal, permitted lifecycle in the signer's local rules, not an attack requiring a majority or special access. No signer collusion, node compromise, or crafted malformed message is needed — a single peer's WebAPI/StackerDB round trip suffices.

### Recommendation
When adding a signer's weight to `total_weight_approved` in the `Accepted` branch, check whether that `slot_id` previously contributed to `total_weight_rejected` (e.g. track per-signer vote state, not just two independent counters) and subtract the corresponding weight from `total_weight_rejected` at that point (symmetric to how the `Rejected` branch already avoids double counting via `responded_signers`). Alternatively, track a single per-signer "current vote weight" record and recompute both totals from it rather than incrementing two independently-gated running counters.

### Proof of Concept
1. Configure 3 signers with equal weight (weight_threshold requiring 2/3, rejection-blocking requiring weight > 1/3).
2. Signer A rejects the proposed block first → `total_weight_rejected = weight(A)`.
3. Signers B and C accept normally → `total_weight_approved = weight(B)+weight(C)`, which already meets `weight_threshold`.
4. Before the coordinator observes the accept-threshold state, Signer A reconsiders (e.g. re-validation succeeds) and sends an `Accepted` response for the same block.
5. `total_weight_approved` becomes `weight(A)+weight(B)+weight(C)` (over-threshold, as expected) but `total_weight_rejected` still holds `weight(A)` from step 2 (never cleared).
6. If a fourth signer D also rejects around the same time such that `total_weight_rejected (now A+D) + weight_threshold > total_weight`, the rejection branch in `gather_signatures` fires first and returns `SignersRejected`, even though `total_weight_approved >= weight_threshold` is simultaneously true and the block was, in fact, validly and sufficiently signed. [3](#0-2)

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
