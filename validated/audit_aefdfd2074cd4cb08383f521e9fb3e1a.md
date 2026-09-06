### Title
`StackerDBListener` lets a signer's stale rejection weight persist in `total_weight_rejected` after it later accepts the same block, permanently inflating the reject tally against the actual sum of current votes - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
The stacks-signer's `handle_block_pre_commit` / `handle_block_rejection` logic explicitly allows a signer to change its position on the *same* block hash: a rejection is described as "a revocable opinion... the block must keep counting" once a signature is later produced [1](#0-0) , and `store_and_process_block_signature`/`handle_block_pre_commit` re-run chainstate checks and can still accept a block a signer previously rejected. The mining-side `StackerDBListener`, however, maintains `total_weight_approved` and `total_weight_rejected` as independent running counters per block, gated by two *different* de-duplication sets (`gathered_signatures` for approvals, `responded_signers` shared across both). This breaks the invariant that `total_weight_approved + total_weight_rejected` (restricted to each signer's *current* vote) equals the sum of individual current per-signer votes — directly analogous to the Beanstalk bug where per-account balances were updated without correspondingly updating the global aggregate.

### Finding Description
In `StackerDBListener::run`, for `BlockResponse::Accepted`, weight is added to `total_weight_approved` only once per slot, gated on `block.gathered_signatures.contains_key(&slot_id)` [2](#0-1) .

For `BlockResponse::Rejected`, weight is added to `total_weight_rejected` only once per slot, but gated on a *shared* set `block.responded_signers`, which is also written to by the Accepted branch [3](#0-2) .

Because these are two independently-tracked counters guarded by two different bookkeeping structures, a single signer's weight can end up counted in *both* `total_weight_approved` and `total_weight_rejected` for the same block if it sends a Reject first and an Accept second (adding to `responded_signers`/`total_weight_rejected`, then later to `gathered_signatures`/`total_weight_approved`, since the Accept branch's guard, `gathered_signatures`, was never touched by the earlier Reject). There is no code path that removes/decrements a signer's weight from `total_weight_rejected` when that signer subsequently signs — the counter is monotonic (`saturating_add` only), while the signer's actual "live" vote is Accept.

This exactly parallels the reported bug class: an individual state transition (a signer's per-vote status changing from reject to accept, analogous to a per-account Stalk/Roots balance change) occurs without a corresponding correction of the aggregate tally (`total_weight_rejected`, analogous to `s.sys.silo.stalk`/`s.sys.silo.roots`), so `total = Σ(individual current votes)` is violated.

### Impact Explanation
`SignerCoordinator::wait_for_confirmation` (the miner-side consumer of `BlockStatus`) treats `total_weight_rejected` as authoritative for aborting block production: once `total_weight_rejected.saturating_add(weight_threshold) > total_weight`, the miner gives up on the block and permanently/temporarily excludes txids [4](#0-3) . Because rejection weight from a signer who has since accepted is never retracted, the miner can reach this "blocking minority rejects" conclusion using stale weight that no longer reflects any live signer's actual vote. This is a liveness wedge: the miner can be forced to abandon and exclude transactions from a block that could legitimately reach the 70% acceptance threshold among currently-voting signers, stalling tenure/transaction inclusion (matches the accepted "High: signer wedged... never signing valid blocks" class, here manifesting as the coordinator wedging valid blocks).

### Likelihood Explanation
This requires only a single signer (any signer, one slot) that first sends a `Rejected` `BlockResponse` and then, on reconsideration (which the codebase explicitly supports — see the "revocable opinion" rejection semantics and pre-commit/re-validation flow), sends an `Accepted` response for the identical `signer_signature_hash`. No majority collusion or key compromise is needed — a single signer's normal reconsideration flow (already built into `signer.rs`) is sufficient to leave the mining-side tally permanently skewed for that block. Given the reject-threshold check only needs `total_weight_rejected` to cross `total_weight - weight_threshold` (~30%), a modest number of already-recorded rejections combined with even one stale reject-then-accept can push the tally over the blocking threshold.

### Recommendation
Track approval/rejection state per-signer in a single authoritative map (e.g., `HashMap<slot_id, Vote>`), and recompute `total_weight_approved`/`total_weight_rejected` by summing over the *current* vote of each responded signer rather than maintaining two independently-incremented, never-decremented counters. When a signer's vote transitions (reject→accept or accept→reject), the previous category's weight must be subtracted before adding to the new category, mirroring how the SignerDB-side handlers in `stacks-signer/src/v0/signer.rs` correctly recompute weight from scratch by re-querying `get_block_rejection_signer_addrs` / `get_block_signatures` on every event rather than maintaining a stale running total.

### Proof of Concept
1. Reward set has signers A (weight 40), B (weight 35), C (weight 25); `weight_threshold` = 70% ≈ 70, blocking minority = 30.
2. Miner proposes block X. Signer A sends `BlockResponse::Rejected` for X. `StackerDBListener` sets `responded_signers = {A}`, `total_weight_rejected = 40`.
3. `total_weight_rejected (40) + weight_threshold (70) = 110 > total_weight (100)` → immediately triggers `NakamotoNodeError::SignersRejected` in `wait_for_confirmation`, excluding txids that A reported as failed, even though B and C have not yet responded and could have supplied ≥70 weight of acceptance [4](#0-3) .
4. Separately (a variant reachable with lower-weight signers): Suppose A rejects (weight 25, below blocking-minority alone) and later legitimately reconsiders and accepts the same block hash after receiving new context (supported by the SignerDB comment on rejections being revocable, `signerdb.rs:4140-4142`). `total_weight_rejected` stays at 25 forever (never decremented) while `total_weight_approved` also grows to include A's 25 plus B/C's weights. If a second signer subsequently rejects for an unrelated reason, `total_weight_rejected` (now A's stale 25 + new signer's weight) can cross the blocking-minority threshold even though A currently counts toward acceptance, causing the miner to wrongly abort the block/exclude txids based on a tally that no longer reflects any signer's live vote. [2](#0-1) [3](#0-2) [5](#0-4) [6](#0-5)

### Citations

**File:** stacks-signer/src/signerdb.rs (L4138-4147)
```rust
        assert!(db.has_signed_block_in_tenure(&consensus_hash_2).unwrap());

        // Global rejection does not clear the commitment: a rejection is a revocable opinion,
        // while the signature is public and can still be aggregated toward the 70% threshold
        // if enough rejecting signers change their minds. The block must keep counting.
        block_info.mark_globally_rejected().unwrap();
        db.insert_block(&block_info).unwrap();

        assert!(db.has_signed_block_in_tenure(&consensus_hash_1).unwrap());
        assert!(db.has_signed_block_in_tenure(&consensus_hash_2).unwrap());
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
