### Title
Signer's rejection weight is never retracted when the same signer later accepts, allowing `total_weight_approved` and `total_weight_rejected` to double-count a single signer's weight - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListener::run` maintains two independent tallies for a proposed block, `total_weight_approved` and `total_weight_rejected`, which the mining coordinator (`SignerCoordinator::get_block_status`) treats as mutually exclusive partitions of the total signer weight when deciding whether a block is globally accepted or globally rejected. A single signer (one StackerDB slot) can violate that exclusivity by sending a `Rejected` message and later an `Accepted` message for the *same* block: the reject path guards on `responded_signers`, while the accept path guards on the disjoint `gathered_signatures` map, so the signer's weight gets added to `total_weight_rejected` and is never removed, then gets added again to `total_weight_approved` when the later accept arrives.

### Finding Description
In the message-handling loop, the `Accepted` branch only skips re-adding weight if the slot is already present in `gathered_signatures`: [1](#0-0) 

The `Rejected` branch instead guards on the separate `responded_signers` set: [2](#0-1) 

Both branches insert into `responded_signers` (accept does so at line 465, reject does so implicitly via the `.insert()` guard), but `gathered_signatures` is only ever touched by the accept branch. Consequently:

- If a signer sends **Accept then Reject**, the reject guard `block.responded_signers.insert(slot_id)` returns `false` (already present from the accept), so the second message is correctly not tallied — no bug in this direction.
- If a signer sends **Reject then Accept**, the reject correctly adds the signer's weight to `total_weight_rejected` and marks `responded_signers`. When the accept later arrives, the guard checks `!block.gathered_signatures.contains_key(&slot_id)`, which is still `true` (that map was never touched by the reject path), so the signer's weight is *also* added to `total_weight_approved`. The weight is never subtracted from `total_weight_rejected`.

This breaks the invariant, relied upon by `SignerCoordinator::get_block_status`, that each signer contributes weight to at most one of the two tallies: [3](#0-2) 

After the double-count, `total_weight_approved + total_weight_rejected` can exceed `total_weight`. The stale rejection weight left behind by a signer who has since switched to accepting the block also pollutes the reject-driven `failed_txids` bookkeeping used to build `temporarily_excluded_txids`/`permanently_excluded_txids`: [4](#0-3) 

This is the direct analog of the reported `collectedEther` bug: an accounting variable (`total_weight_rejected`) is incremented for an action whose real, final effect (the signer's ultimate acceptance) should have superseded/netted it out, but the code never performs the compensating subtraction, so the ledger no longer reflects the true state of a single contributor.

### Impact Explanation
A single one-slot signer (no majority, no other key needed) can make the reject tally artificially "stick" even after switching to acceptance. Because `get_block_status`/the stackerdb listener check the rejection condition (`total_weight_rejected.saturating_add(weight_threshold) > total_weight`) using this now-permanently-inflated value, a signer's transient/reconsidered rejection continues to count against the block even after that same signer has legitimately signed an acceptance for it. This can push an otherwise-approvable block toward the "globally rejected" branch (or artificially bias `failed_txids` weight used for excluding transactions from future proposals) using weight that no longer represents a live rejection — a rejection being counted as if it still stands even though it has been effectively superseded by an accept, undermining the intended one-signer/one-vote accounting the 70%/30% thresholds are built on. This matches the "rejection recounted"/miscounted-response class of impact: the tally used to reach the accept/reject decision no longer equals the real, current set of distinct dissenting signers.

### Likelihood Explanation
No majority collusion is required — a single signer flipping from `Rejected` to `Accepted` for the same `signer_signature_hash` is sufficient, and such a flip is plausible both from an adversarial signer (deliberately manipulating the tally) and from a benign signer's local state machine re-evaluating and later signing after an earlier rejection (e.g., timeout/transient failure followed by a successful validation). The vulnerable code path is exercised on every ordinary `BlockResponse` handling cycle, requiring no special network conditions.

### Recommendation
Track each signer's *current* stance in a single map (e.g., `HashMap<slot_id, Vote>` where `Vote` is `Accepted(weight, signature)` or `Rejected(weight)`), and when a new response supersedes a prior one, subtract the old contribution from its respective tally before adding the new one to the other. Concretely, before adding weight in the `Accepted` branch, check whether `slot_id` already contributed to `total_weight_rejected` and subtract that signer's weight from `total_weight_rejected` in that case (and symmetrically for `Rejected` after a prior `Accepted`, even though today's guard already prevents that direction). This restores the invariant that `total_weight_approved + total_weight_rejected` never exceeds `total_weight` and that each signer's weight reflects only their latest response.

### Proof of Concept
1. Configure a reward set/threshold as in `StackerDBListener` (`total_weight`, `weight_threshold` per `NakamotoBlockHeader::compute_voting_weight_threshold`).
2. Have signer `S` (slot `k`, weight `w`) send `SignerMessageV0::BlockResponse(BlockResponse::Rejected(...))` for a given `block_sighash`. Observe `total_weight_rejected += w` at [2](#0-1) .
3. Have the same signer `S` then send `SignerMessageV0::BlockResponse(BlockResponse::Accepted(...))` for the same `block_sighash`. Because `gathered_signatures` does not yet contain slot `k`, `total_weight_approved += w` also occurs at [5](#0-4) .
4. Inspect `BlockStatus` for that hash: `total_weight_rejected` still includes `w` even though `S` is now recorded as having accepted, and `total_weight_approved + total_weight_rejected` exceeds what a correct one-vote-per-signer accounting would produce, demonstrating the stale/duplicated weight.

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
