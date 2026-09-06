### Title
Reject→Accept vote switch by a single signer causes their weight to be double-counted (once in `total_weight_rejected`, once in `total_weight_approved`), letting stale rejection weight permanently wedge a validly-signed block out of the miner's tally - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
The miner-side `StackerDBListener` tracks per-block vote weight in two counters, `total_weight_approved` and `total_weight_rejected`, guarded by a single `responded_signers` set that is supposed to ensure each signer's weight is only counted once. The `Rejected` handler correctly gates on `responded_signers.insert(slot_id)` before adding weight, but the `Accepted` handler never consults `responded_signers` at all - it only checks `gathered_signatures.contains_key(&slot_id)`. A signer that rejects a block first and later legitimately re-evaluates to accept it (a state transition explicitly supported by the signer's own state machine, `LocallyRejected -> LocallyAccepted`) therefore has its weight added to `total_weight_rejected` once and, on the later accept, added *again* to `total_weight_approved`, without the stale rejection weight ever being removed. This is exactly analogous to the Plaza bug: a value used to gate a binary threshold decision (`collateralLevel` there, aggregate signer weight here) can be pushed across a threshold by a sequence of individually-legitimate, reversible actions from a single actor, breaking the invariant that the tally reflects only the current, distinct state of each participant.

### Finding Description
`stacks-node/src/nakamoto_node/stackerdb_listener.rs` processes `BlockResponse` messages for a given block hash:

- `Accepted` branch: weight is added iff `!block.gathered_signatures.contains_key(&slot_id)`, then unconditionally `block.responded_signers.insert(slot_id)` is called: [1](#0-0) 

- `Rejected` branch: weight is added only `if block.responded_signers.insert(slot_id)` succeeds (i.e., only the first time this slot is ever seen, whether from an accept or a reject): [2](#0-1) 

Because the `Accepted` branch never checks `responded_signers`, the ordering "reject, then accept" is not symmetric with "accept, then reject":
- Accept → Reject: the reject's `responded_signers.insert(slot_id)` returns `false` (already present), so no rejection weight is added. Correctly deduplicated.
- Reject → Accept: the reject adds weight to `total_weight_rejected` and marks the slot in `responded_signers`. The subsequent accept only checks `gathered_signatures` (still empty for this slot), so it adds the *same signer's* weight to `total_weight_approved` too. `total_weight_rejected` is never decremented.

This corrupts the invariant that each signer's weight should count toward exactly one side of the tally at any time; after a reject→accept flip, `total_weight_approved + total_weight_rejected` permanently exceeds the true single-count total by that signer's weight, and the stale rejection contribution can never be cleared for that block hash (short of the coordinator's `reset_rejections` call, which only fires after a timeout, not immediately).

The consuming code in `stacks-node/src/nakamoto_node/signer_coordinator.rs` checks the rejection threshold *before* the approval threshold on every poll: [3](#0-2) 

So if the (stale, inflated) `total_weight_rejected` crosses `total_weight - weight_threshold` (the blocking-minority threshold), the coordinator immediately returns `NakamotoNodeError::SignersRejected` and gives up on the block - even if every signer who ever rejected it has since switched to accept, and even if the true current, distinct-signer approval weight has already reached the 70% signing threshold.

### Impact Explanation
This is a liveness break at the miner's block-confirmation logic: a validly, fully re-signed block (per the real chain-level signature check in `NakamotoBlockHeader::verify_signer_signatures`, which recomputes weight from the actual distinct signatures in the block header and is unaffected by this bug) can be discarded by the miner's coordinator as "signers rejected" purely because of stale rejection weight that was never cleared after the responsible signer switched their vote to accept. This wedges the miner into never being able to push/confirm that specific block, forcing wasted signing rounds and needless re-proposals/tenure churn, and can be triggered by the ordinary, single-signer behavior of "reject, then re-evaluate and accept" that the signer state machine explicitly allows (`should_reevaluate_reject_reason`, `LocallyRejected -> LocallyAccepted`). No majority collusion is required to corrupt the tally itself - the corruption happens with just one signer's normal vote-flip; only flipping the final block outcome requires enough stale weight to cross the built-in 30% blocking-minority design threshold, which is a much lower bar than requiring a majority.

### Likelihood Explanation
Reject→accept transitions are a normal, documented part of signer operation (a signer can reject transiently, e.g. due to `ConnectivityIssues` or a validation timeout, and later accept once conditions clear), so this is reachable without any adversarial coordination - a single signer under ordinary network hiccups triggers the double count. It only takes a small number of such signers (whose combined weight crosses the blocking-minority fraction) to wedge a given block from the miner's perspective.

### Recommendation
In the `Accepted` branch of `stacks-node/src/nakamoto_node/stackerdb_listener.rs`, check and update `responded_signers`/rejection weight consistently with the `Rejected` branch: if the slot was previously counted toward `total_weight_rejected`, subtract that signer's weight from `total_weight_rejected` when they later accept (and vice versa for the reverse direction), so at most one side's tally ever holds a given signer's weight at a time. Alternatively, track responses per-slot as a single `enum { Accepted, Rejected }` and recompute `total_weight_approved`/`total_weight_rejected` from that authoritative map rather than incrementally, eliminating the possibility of stale/duplicated contributions.

### Proof of Concept
1. Miner proposes block `B`; signer `S` (weight `w`) initially rejects `B` (e.g., due to a transient `ConnectivityIssues`/timeout condition). `StackerDBListener` records `total_weight_rejected += w` and `responded_signers.insert(slot_S)`.
2. `S` re-evaluates (per `should_reevaluate_reject_reason` / `LocallyRejected -> LocallyAccepted`) and signs/broadcasts an `Accepted` response for the same block hash.
3. `StackerDBListener`'s `Accepted` handler only checks `gathered_signatures.contains_key(&slot_S)` (false), so it adds `total_weight_approved += w` as well - `S`'s weight is now counted on both sides, and `total_weight_rejected` is never reduced.
4. If enough other signers are also in this transient state (combined stale rejected weight > `total_weight - weight_threshold`), `signer_coordinator.rs`'s poll loop hits the rejection-threshold branch first and returns `NakamotoNodeError::SignersRejected`, abandoning block `B` even though `total_weight_approved` may already have reached (or would shortly reach) the real 70% signing threshold from genuinely current votes.

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
