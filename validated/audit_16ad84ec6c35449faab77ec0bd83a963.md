### Title
Signer weight double-counted in both accepted and rejected tallies when a signer flips from Reject to Accept for the same block - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListener::run` tallies `total_weight_approved` and `total_weight_rejected` for a proposed block independently, gated by two different dedup structures (`gathered_signatures` for accepts, `responded_signers` for rejects). Because the Accepted-message handler only checks `gathered_signatures` (not `responded_signers`) before adding weight, a signer that first sends a `Rejected` response and later sends an `Accepted` response for the *same* block gets its weight added to `total_weight_rejected` **and** later also to `total_weight_approved`, inflating both counters with the same signer's weight.

### Finding Description
In `stacks-node/src/nakamoto_node/stackerdb_listener.rs`, the handler for `SignerMessageV0::BlockResponse(BlockResponse::Accepted(...))` only checks whether the slot id is already present in `block.gathered_signatures` before crediting `signer_entry.weight` to `block.total_weight_approved`: [1](#0-0) 

Separately, the handler for `BlockResponse::Rejected` guards weight addition with `block.responded_signers.insert(slot_id)` — a `HashSet` shared between both message kinds: [2](#0-1) 

The two guards are not symmetric:
- If a signer **Accepts first**, `responded_signers` gets the slot id, so a later **Reject** from the same signer is correctly suppressed (the `insert` returns `false`, `total_weight_rejected` is not incremented).
- If a signer **Rejects first**, `responded_signers` gets the slot id and `total_weight_rejected` is incremented. But the Accepted handler never consults `responded_signers` — it only checks `gathered_signatures`, which is still empty for this signer. So when the same signer later sends an `Accepted` message (e.g. after re-evaluating the proposal, a stale/duplicate message being replayed, or a deliberately crafted second message), the check `!block.gathered_signatures.contains_key(&slot_id)` passes and `total_weight_approved` is incremented too.

The net effect is that a single signer's weight can be counted in **both** `total_weight_rejected` and `total_weight_approved` for the same block, breaking the invariant that each signer's weight should count toward at most one side of the tally.

### Impact Explanation
`get_block_status`/`SignCoordinator` in `stacks-node/src/nakamoto_node/signer_coordinator.rs` drives the miner's decision to treat a block as globally accepted (gather signatures and push the block) or globally rejected, purely from `total_weight_approved` and `total_weight_rejected`: [3](#0-2) 

Because a rejecting signer's weight can be silently re-counted as an accept, the accept tally can reach the 70% threshold using weight that a signer intended to withhold (their reject was never revoked/undone), and simultaneously the reject tally still reflects that same weight as having rejected. This lets the coordinator conclude "block accepted" using an inflated/incoherent weight accounting that does not correspond to a real, uniquely-attributable set of accepting signers reaching consensus — i.e. a miscounted response feeding the block-acceptance decision, which is the "rejection recounted as an accept" class explicitly called out as Critical.

### Likelihood Explanation
Triggering this requires only a single signer (or a StackerDB replay/race that delivers a stale Accepted message after a fresher Rejected one, or vice versa timing) sending two different `BlockResponse` messages over StackerDB for the same block hash — no majority collusion is needed, and both message types are validated for a correct signature from that signer before being tallied, so this is reachable purely through normal gossip/StackerDB message flow that the coordinator already processes.

### Recommendation
Make the two counters mutually exclusive using a single decision-state map keyed by `slot_id` (e.g., record whether the signer's counted vote is Accept or Reject) and refuse to re-tally weight into the opposite bucket once a decision has been recorded for that slot — mirroring the guard already used for Reject-after-Accept, but applied symmetrically to Accept-after-Reject in `stacks-node/src/nakamoto_node/stackerdb_listener.rs`.

### Proof of Concept
1. Miner proposes block `B` with sighash `h`; `StackerDBListener` initializes `blocks[h]` with `total_weight_approved = 0`, `total_weight_rejected = 0`.
2. Signer `S` (weight `w`) sends `BlockResponse::Rejected` for `h`. Handler executes `block.responded_signers.insert(slot_S)` → `true`, so `total_weight_rejected += w`.
3. Signer `S` later sends `BlockResponse::Accepted` for the same `h` (valid signature over `h`, e.g. due to a race/replay or re-evaluation). Handler checks `!block.gathered_signatures.contains_key(&slot_S)` → `true` (never populated), so `total_weight_approved += w`, and `gathered_signatures`/`responded_signers` are updated.
4. Result: `total_weight_rejected` and `total_weight_approved` both include `S`'s weight `w` for the same block, even though `S` can legitimately cast only one vote — inflating whichever threshold check (`>= weight_threshold` for accept, or `> total_weight - weight_threshold` for reject) is evaluated next in `signer_coordinator.rs::get_block_status`.

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
