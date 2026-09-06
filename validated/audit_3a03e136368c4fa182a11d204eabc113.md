### Title
Stale rejection weight is never retracted when a signer flips from Reject to Accept, letting one Byzantine (or simply reconsidering) signer double-count its weight in both `total_weight_rejected` and `total_weight_approved` - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
In the node-side `StackerDBListener` that the mining coordinator (`signer_coordinator.rs`) polls to decide whether a block has been approved or rejected by the signer set, the bookkeeping for a given signer's `slot_id` is asymmetric between the `Accepted` and `Rejected` branches. A signer that first rejects a block and later accepts the *same* block (a legitimate, protocol-supported sequence — see `should_reevaluate_reject_reason` / `should_reevaluate_block` in `stacks-signer/src/v0/signer.rs`) has its weight added to `total_weight_approved`, but the earlier contribution to `total_weight_rejected` is never removed. The two aggregate counters can therefore both include the same signer's weight simultaneously, breaking the invariant that `total_weight_approved` and `total_weight_rejected` should classify each responding signer's weight into mutually exclusive buckets ("aggregated-weight vs verified-accepts").

### Finding Description
In the `Accepted` handler: [1](#0-0) 
the weight is only added to `total_weight_approved` if `slot_id` is not already in `gathered_signatures` — that check has nothing to do with whether the same slot previously contributed to `total_weight_rejected`.

In the `Rejected` handler: [2](#0-1) 
the weight is added to `total_weight_rejected` gated only by `responded_signers.insert(slot_id)` (first-time-only), and is never decremented or moved when that same slot subsequently sends an `Accepted` response for the same `block_signer_sighash`.

Sequence that triggers the break:
1. Signer S (weight `w`) sends `Rejected` for block B. `responded_signers` gains slot S; `total_weight_rejected += w`.
2. S later re-evaluates B (a supported path in the signer state machine — a prior rejection can be revisited via `should_reevaluate_reject_reason`) and sends `Accepted` for the same B.
3. On the node side, `gathered_signatures` does not yet contain S's slot, so `total_weight_approved += w` fires. `total_weight_rejected` still contains `w` from step 1, unmodified.

Now `total_weight_approved + total_weight_rejected` exceeds what should be possible given the true, current set of responses (S's weight is counted on both sides at once), even though a signer's committed weight should only ever back one of the two mutually exclusive outcomes at a time.

### Impact Explanation
This is reachable in `signer_coordinator.rs`'s polling loop, which uses exactly these two aggregates to decide the block's fate: [3](#0-2) 
Because `total_weight_rejected` can carry stale weight from signers who have since accepted, the `SignersRejected` branch (`total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight`) can fire even when the live, current opinion of the signer set has actually reached the 70% approval threshold. This is a liveness wedge on the mining/coordination path for a block that would otherwise be validly, canonically approved: the coordinator can give up on (or the miner logic can discard) a block that in truth secured sufficient real acceptance weight, purely because of un-retracted stale rejection weight. It does not require a majority — a single signer switching its vote is enough to inflate `total_weight_rejected` by that signer's own weight without any corresponding retraction.

### Likelihood Explanation
Vote-flipping (reject then later accept for the same block) is an explicitly supported path in the signer protocol via `should_reevaluate_reject_reason`/`should_reevaluate_block`, so this is not a contrived edge case — it can happen during normal operation whenever conditions change between a signer's initial rejection and a later re-evaluation of the same proposal (e.g., `SortitionViewMismatch`-class rejections that are re-evaluable). Any single signer doing this once is sufficient to produce the double-count; no coordination with other signers or possession of another party's key is required.

### Recommendation
Track each slot's current classification (accepted/rejected) rather than only gating on first-seen-ness. When a slot transitions from `Rejected` to `Accepted` (or vice versa) for the same block, retract the weight from the old bucket before adding it to the new one, e.g. maintain a `HashMap<slot_id, Vote>` (or reuse `gathered_signatures`/a parallel rejected-set) and recompute `total_weight_approved`/`total_weight_rejected` from that single source of truth instead of two independently-incremented counters.

### Proof of Concept
1. Configure a signer set where signer S has weight `w` and the block-rejection blocking minority is `total_weight - weight_threshold`.
2. Signer S sends `BlockResponse::Rejected` for block B with `signer_signature_hash = H`. `stackerdb_listener` records `total_weight_rejected += w` for `H`.
3. Have S (per the legitimate re-evaluation logic in `stacks-signer/src/v0/signer.rs`) later send `BlockResponse::Accepted` for the same `H`. `stackerdb_listener` records `total_weight_approved += w` for `H`, leaving the earlier `total_weight_rejected` contribution intact.
4. Have enough other signers accept B such that `total_weight_approved >= weight_threshold` is genuinely reached from current votes — but combine with enough other (possibly stale or live) rejections so that `total_weight_rejected + weight_threshold > total_weight` also holds using S's doubly-counted weight.
5. In `signer_coordinator.rs`'s poll loop, the `SignersRejected` branch is checked before the `total_weight_approved >= weight_threshold` branch, so the coordinator returns `Err(NakamotoNodeError::SignersRejected {...})` even though the current, live signer opinion has reached true approval consensus — demonstrating the liveness wedge caused by the un-retracted stale rejection weight.

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
