### Title
Stale rejection weight from a signer that later accepts is never retracted from `total_weight_rejected`, letting the coordinator prematurely wedge a recoverable block into `SignersRejected` - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
A single signer's `BlockResponse::Rejected` and later `BlockResponse::Accepted` for the *same* `signer_signature_hash` are tallied into two independent, never-reconciled counters, `total_weight_rejected` and `total_weight_approved`/`gathered_signatures`, on the mining node's `StackerDBListener`. When that signer flips from reject to accept (a flow the signer explicitly supports via `should_reevaluate_reject_reason`), its earlier rejection weight is never subtracted. This breaks the intended equality that "reject weight" reflects currently-rejecting signers, and can wedge a block that would otherwise reach the 70% signature threshold into `NakamotoNodeError::SignersRejected`.

### Finding Description
On the node side, `StackerDBListener::run` maintains, per proposed block (keyed by `signer_signature_hash`), two independent accumulators inside `BlockStatus`:

- `total_weight_approved`, guarded by `block.gathered_signatures.contains_key(&slot_id)` [1](#0-0) 

- `total_weight_rejected`, guarded by `block.responded_signers.insert(slot_id)` [2](#0-1) 

`responded_signers` is a single set shared by *both* code paths, but the accept path never checks it before adding weight, and the reject path never checks `gathered_signatures` or clears `total_weight_rejected` when the same slot later accepts. Concretely:

1. Signer S rejects block B (hash `h`) for a retryable reason (e.g. `ConnectivityIssues`, or any reason for which `should_reevaluate_reject_reason` returns true, so the signer itself can legitimately re-consider and later sign the *identical* proposal hash `h`) — the node's `responded_signers.insert(slot_id)` returns `true` (first response), so `total_weight_rejected += S.weight`. [2](#0-1) 
2. The miner's/signer's own retry logic (or a new identical proposal broadcast) causes S to re-evaluate and sign the same block `h`, sending `BlockResponse::Accepted`.
3. On the node, the accept handler only checks `!block.gathered_signatures.contains_key(&slot_id)` (a different map, still empty for S) — it has no knowledge that S is already counted in `total_weight_rejected`, so it happily adds `S.weight` to `total_weight_approved` as well. [1](#0-0) 
4. `total_weight_rejected` is **never decremented**. There is no code path anywhere in `StackerDBListener` that removes a slot's weight from `total_weight_rejected` once added, even though the design's stated philosophy elsewhere ("a rejection is a revocable opinion, while a signature is a bearer instrument") implies the reject side must be revocable while acceptance persists. [3](#0-2) 

The consumer of these two counters, `SigningCoordinator::wait_for_signatures` (in `signer_coordinator.rs`), checks the rejection-crosses-blocking-minority condition *before* checking the approval threshold: [4](#0-3) 

Because `total_weight_rejected` can now contain weight belonging to a signer who has since accepted, the coordinator can reach `total_weight_rejected + weight_threshold > total_weight` (declaring the block dead as `NakamotoNodeError::SignersRejected`) using stale weight that no longer reflects a currently-rejecting signer, even in a scenario where the block would otherwise have reached the legitimate 70% approval threshold with the flipped signer's fresh, valid signature counted correctly on the approve side.

### Impact Explanation
This is a liveness wedge: a block that a supermajority (including the reconsidering signer) is actually willing to sign can be declared globally rejected by the node purely because of a stale, un-retracted rejection tally from a signer who has since legitimately accepted. This maps to the "High" impact bucket ("a signer wedged into never signing valid blocks... or losing the equivocation guard on restart") in spirit — here it is the *miner/coordinator* side of the same signature-tallying pipeline being wedged by a stale count rather than a live one, breaking the intended equality between "aggregated rejected weight" and "currently rejecting signers." A single signer's benign vote flip (no majority, no other signer's key, no auth_token) is sufficient to poison the tally for that specific block proposal.

### Likelihood Explanation
The reject→accept transition for the identical `signer_signature_hash` is not a hypothetical: `should_reevaluate_reject_reason` (in `stacks-signer/src/v0/signer.rs`) exists specifically to let a signer that previously rejected a proposal reconsider and sign it later once the reject reason (e.g. a transient `ConnectivityIssues`/validation timeout) no longer applies, without requiring the miner to re-propose a different block. Any retry/redelivery of a proposal, StackerDB replay, or the signer's own re-evaluation logic can trigger exactly this timeline with a single signer, requiring no majority collusion and no cryptographic forgery — only two legitimate, correctly-signed messages from the same signer in reject-then-accept order.

### Recommendation
On the node side (`StackerDBListener`), track responded slots per-verdict (or store the last verdict per slot and recompute both weight totals from the current verdict set) instead of using two independently-guarded, unreconciled accumulators. Concretely: before adding a slot to `total_weight_rejected`, ensure the slot is not already counted in `gathered_signatures`/`total_weight_approved`, and — symmetrically — when a slot transitions from reject to accept, subtract its weight from `total_weight_rejected` before adding it to `total_weight_approved`. Alternatively, maintain a single `HashMap<slot_id, Verdict>` and derive both totals fresh from it on every update, matching the "signature is a bearer instrument, rejection is revocable" semantics that the signer-side codebase already documents and implements correctly in `signerdb.rs`.

### Proof of Concept
1. Configure a reward-cycle signer set where signer S has enough weight that its rejection, combined with existing rejections from other signers, is close to (but individually short of) the 30% blocking threshold, while its acceptance is needed to cross the 70% threshold.
2. Have S send `BlockResponse::Rejected(RejectReason::ConnectivityIssues(...))` (or any reason accepted by `should_reevaluate_reject_reason`) for block `B` at `signer_signature_hash = h`. Verify via logs/`total_weight_rejected` that the node's `StackerDBListener` records S's weight in `total_weight_rejected`. [2](#0-1) 
3. Trigger a redelivery of the identical proposal `B` (same `h`) to S, causing S's signer process to re-evaluate per `should_reevaluate_reject_reason` and sign it, sending `BlockResponse::Accepted` for `h`.
4. Observe that `total_weight_approved` also now includes S's weight (via the `gathered_signatures` guard, which is blind to S's prior rejection), while `total_weight_rejected` still includes it too (never decremented). [1](#0-0) 
5. Show that `SigningCoordinator::wait_for_signatures`'s rejection check `total_weight_rejected.saturating_add(weight_threshold) > total_weight` can now be satisfied purely because of the un-retracted stale weight, causing the coordinator to return `NakamotoNodeError::SignersRejected` for `B` even though the current, live signer set (including S's post-flip acceptance) would have reached the 70% approval threshold on a subsequent tally. [4](#0-3)

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

**File:** docs/signer-flows.md (L322-327)
```markdown
A conflict is any block a signature was ever put over — ours, or a group
threshold we observed — whatever its state now. In particular rejection, even
_global_ rejection, does not clear one: a rejection is a revocable opinion,
while a signature is a bearer instrument that can still be aggregated toward
the 70% threshold if rejecting signers change their minds. Only staleness or
node-derived death (the two questions above) clears a conflict.
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
