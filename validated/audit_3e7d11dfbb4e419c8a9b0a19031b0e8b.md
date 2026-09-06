### Title
Weight double-counting across accept/reject tallies in `StackerDBListener` breaks the aggregated-weight vs. verified-accepts invariant - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
The node's `StackerDBListener` tallies signer weight for a proposed block into two mutually-exclusive buckets, `total_weight_approved` and `total_weight_rejected`, which `SignerCoordinator::get_block_status` uses to decide whether the block was approved (≥70% weight) or rejected (>30% weight). The dedup guard used to prevent a signer's weight from being counted twice is **different** for the two message kinds: the `Accepted` path dedups on `gathered_signatures` while the `Rejected` path dedups on the shared `responded_signers` set. A single malicious signer can send a `Rejected` message for a block hash first, then later send an `Accepted` message for the *same* hash, and have its weight added to **both** `total_weight_rejected` and `total_weight_approved`, breaking the invariant that the two tallies are disjoint partitions of `total_weight`.

### Finding Description
In `handle_signer_messages`, the `Accepted` branch dedups solely against `block.gathered_signatures`: [1](#0-0) 

while the `Rejected` branch dedups against `block.responded_signers`: [2](#0-1) 

Both branches also unconditionally call `block.responded_signers.insert(slot_id)` (line 465 for accept, and it is only checked, not conditioned, for reject at line 515). This makes the two directions asymmetric:

- Accept → Reject (same slot): the reject branch's `responded_signers.insert(slot_id)` returns `false` (already present from the accept), so the reject weight is correctly *not* added a second time.
- Reject → Accept (same slot): the accept branch only checks `gathered_signatures`, which is still empty for that slot (the signer never accepted before), so the condition `!block.gathered_signatures.contains_key(&slot_id)` is `true` and the signer's weight is added to `total_weight_approved`, **in addition to** the weight already added to `total_weight_rejected` when the earlier `Rejected` message was processed.

The result: `total_weight_approved + total_weight_rejected` can exceed `self.total_weight` for a given block hash, because one signer's weight was credited on both sides of the equality that the coordinator relies on: [3](#0-2)  uses exactly these two counters to decide "block accepted" vs "signers rejected", assuming they represent disjoint, verified sets of accepting/rejecting weight.

This is the direct structural analog of the Tempus `lend` bug: a value that is supposed to represent one specific, verified quantity (tokens actually received / weight actually and exclusively committed to one outcome) is instead computed from an unrelated or already-double-used quantity, corrupting the accounting that a downstream threshold decision depends on.

### Impact Explanation
A single Byzantine/misbehaving signer (one StackerDB slot) can inflate `total_weight_approved` with weight that is simultaneously counted in `total_weight_rejected`, without needing a majority of signers or any other signer's key. Combined with weight from a smaller set of genuinely-approving signers, this phantom double-counted weight can push `total_weight_approved` over the 70% `weight_threshold` earlier than legitimate distinct signer weight would allow, or simultaneously contribute to crossing the blocking-minority rejection threshold — corrupting the aggregated-weight-vs-verified-accepts equality that block finalization depends on. This matches the Critical impact category of "aggregated weight vs verified accepts" being broken.

### Likelihood Explanation
Triggerable by any single signer/attacker controlling one StackerDB slot who sends a `Rejected` message followed by an `Accepted` message for the same `signer_signature_hash` — no cooperation from other signers, no node bug beyond this listener's bookkeeping, and no special timing beyond ordinary gossip delivery order.

### Recommendation
Use a single, unified per-slot "has this signer already responded to this block" gate (e.g., `responded_signers`) for *both* the `Accepted` and `Rejected` branches before crediting weight to either `total_weight_approved` or `total_weight_rejected`, so that each signer's weight can only ever be counted once, and only toward the first (or otherwise canonical) response recorded for that block hash.

### Proof of Concept
1. Node proposes block `B` with `signer_signature_hash = H`; `StackerDBListener` creates a `BlockStatus` entry for `H` with `total_weight_approved = total_weight_rejected = 0`.
2. Malicious signer at slot `S` (weight `w`) sends `SignerMessageV0::BlockResponse(BlockResponse::Rejected(...))` for `H`. In `handle_signer_messages`, `block.responded_signers.insert(S)` succeeds, `block.total_weight_rejected += w`.
3. The same signer then sends `SignerMessageV0::BlockResponse(BlockResponse::Accepted(...))` for the same `H` with a validly-signed `MessageSignature` over `H`. `block.gathered_signatures.contains_key(&S)` is `false` (never accepted before), so `block.total_weight_approved += w` as well; `gathered_signatures` and `responded_signers` are updated again (`responded_signers.insert` is a no-op but the weight was already added elsewhere).
4. Now `total_weight_approved + total_weight_rejected = w + (other honest weight)`, exceeding `total_weight` by `w`. If enough additional honest signers approve, `total_weight_approved` in `SignerCoordinator::get_block_status` can cross `weight_threshold` using less genuinely-distinct-approving weight than the protocol intends, while `total_weight_rejected` simultaneously still reflects this same signer's earlier rejection.

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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-546)
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
            } else if rejections_timer.elapsed() > *rejections_timeout {
```
