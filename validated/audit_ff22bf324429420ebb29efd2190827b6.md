I have sufficient evidence to confirm the bug. Let me finalize.

### Title
Stale rejected-weight tally is never corrected when a signer flips from Reject to Accept, causing the miner coordinator to act on a stale/inflated rejection threshold - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
The `StackerDBListener` accumulates `total_weight_approved` and `total_weight_rejected` for a block using two different, asymmetrically-gated tracking sets (`gathered_signatures` and `responded_signers`). When a signer first rejects and later accepts the same block (a normal, legitimate re-evaluation, e.g. after a chain-state recheck in `handle_block_pre_commit`/`handle_block_validate_ok`), its weight is added to *both* `total_weight_rejected` and `total_weight_approved`, and the stale rejected weight is never removed. `SignerCoordinator::wait_for_signer_responses` in `stacks-node/src/nakamoto_node/signer_coordinator.rs` checks the rejection condition before the acceptance condition, so this permanently-inflated `total_weight_rejected` can force a live, valid block into `SignersRejected`.

### Finding Description
In the `Accepted` branch [1](#0-0) , the approved-weight increment is gated only by `!block.gathered_signatures.contains_key(&slot_id)`, and `block.responded_signers.insert(slot_id)` is called unconditionally afterward.

In the `Rejected` branch [2](#0-1) , the rejected-weight increment is gated by `block.responded_signers.insert(slot_id)` returning `true` (i.e., "first time responding at all").

Sequence for a single signer at slot `s` with weight `w`:
1. Signer rejects first: `responded_signers.insert(s)` → `true` → `total_weight_rejected += w`.
2. Same signer later sends Accepted for the same block (e.g., after re-validating): `gathered_signatures.contains_key(s)` is `false` (never touched by the reject path) → `total_weight_approved += w`. `gathered_signatures.insert(s, sig)` and `responded_signers.insert(s)` (no-op, already present) follow.

Result: `w` is now counted in *both* `total_weight_approved` and `total_weight_rejected` simultaneously, and there is no code path that ever decrements `total_weight_rejected` when a signer's later message supersedes an earlier rejection. This breaks the invariant that `total_weight_rejected` reflects the current rejecting weight of the signer set (aggregated-weight vs. verified-accepts equality).

`SignerCoordinator::wait_for_signer_responses` reads these two counters in an if/else chain, checking rejection first: [3](#0-2) . Because the rejected branch is evaluated before the approved branch, a stale/never-decremented `total_weight_rejected` can cross `total_weight - weight_threshold` purely from weight that no longer reflects opposition (the signer switched to Accept), causing the miner to abort a block via `NakamotoNodeError::SignersRejected` even though the *current* signer positions would actually satisfy the approval threshold.

### Impact Explanation
This is a High-impact liveness wedge on the mining/coordination path: the coordinator "acts on a stale threshold" (rejected weight that no longer represents live opposition), which can cause valid blocks — that do in fact have enough current supporting weight — to be discarded as `SignersRejected`, along with side effects like excluding txids and bumping `naka_rejected_blocks` counters [4](#0-3) . This does not on its own make a signer sign an invalid block (the actual signature set collected in `gathered_signatures` only contains legitimate accept signatures), but it degrades the miner/coordinator's ability to make forward progress based on the real, live signer-agreement state.

### Likelihood Explanation
A single signer legitimately reconsidering its vote (reject → accept) is a normal occurrence in the protocol (e.g., the flows around `handle_block_validate_ok`/`handle_block_pre_commit` explicitly allow re-checking and re-deciding on a block), so this requires no majority collusion and no malicious behavior — only one signer with any non-trivial weight changing its mind once is sufficient to permanently corrupt the rejected tally for that block's lifetime in the listener's in-memory state.

### Recommendation
Track approved/rejected weight from a single per-slot "current vote" map (overwriting, not additively accumulating, on each new message) and recompute `total_weight_approved`/`total_weight_rejected` as sums over that map, or explicitly subtract a signer's previous contribution from `total_weight_rejected` before adding it to `total_weight_approved` (and vice versa) when a slot's vote changes. Ensure the `Accepted` handler checks whether the signer's slot is already present in `responded_signers` from a *rejection* and, if so, backs the corresponding weight out of `total_weight_rejected`.

### Proof of Concept
1. Configure a signer set with total weight `T` and approval `weight_threshold` (70% of `T`).
2. Signer `A` (weight `wA`) sends `BlockResponse::Rejected` for block `B` → `total_weight_rejected = wA`.
3. Signer `A` later sends `BlockResponse::Accepted` for the same block `B` (legitimate re-evaluation) → `total_weight_approved = wA`, but `total_weight_rejected` remains `wA` forever.
4. Enough additional signers reject such that `total_weight_rejected.saturating_add(weight_threshold) > total_weight` becomes true purely because `wA` is still counted, even though signer `A`'s live vote is now Accept.
5. `SignerCoordinator::wait_for_signer_responses` returns `Err(NakamotoNodeError::SignersRejected {...})` [4](#0-3) , discarding the block/tenure even though the current live-vote weight may in fact satisfy `weight_threshold` for approval.

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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-540)
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
```
