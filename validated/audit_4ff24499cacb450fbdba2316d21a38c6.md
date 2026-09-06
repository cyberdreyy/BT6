### Title
Stale rejection weight is never cleared when a signer flips to Accept for the same block, letting `SignerCoordinator::get_block_status` wrongly declare `SignersRejected` on a block that has (or would have) reached the acceptance threshold - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener` tallies `total_weight_approved` and `total_weight_rejected` independently per `BlockStatus`, gated by two different sets (`gathered_signatures` vs `responded_signers`). When a signer first sends a `Rejected` response and later sends an `Accepted` response for the *same* `signer_signature_hash` (which the signer protocol explicitly allows via re-evaluation of a previously-rejected block, per `should_reevaluate_reject_reason`/`should_reevaluate_block`), the signer's weight is added to `total_weight_approved` on the second message but is **never removed** from `total_weight_rejected`. The two weight counters are not mutually exclusive per signer, breaking the "aggregated-weight vs verified-accepts" equality that `SignerCoordinator::get_block_status` relies on.

### Finding Description
`BlockStatus` in `stacks-node/src/nakamoto_node/stackerdb_listener.rs` tracks: [1](#0-0) 

For an `Accepted` message, the weight-add is gated on `!block.gathered_signatures.contains_key(&slot_id)`: [2](#0-1) 

For a `Rejected` message, the weight-add is gated on `block.responded_signers.insert(slot_id)` returning `true` (first time seen): [3](#0-2) 

Because the `Accepted` branch checks `gathered_signatures` (empty until an accept is actually recorded) rather than `responded_signers`, a signer that first rejects and later accepts the same block hash will:
1. On `Reject`: `responded_signers.insert(slot_id)` → `true` → `total_weight_rejected += weight`.
2. On later `Accept` for the same hash: `gathered_signatures` still does not contain `slot_id` → `total_weight_approved += weight` is applied again.

The reject-side weight is never decremented or removed when the signer later accepts, so the same signer's weight persists simultaneously in both `total_weight_rejected` and `total_weight_approved`.

This is reachable without a majority: it only requires one signer (their own key, own valid signature) to send two responses over time for the same block, which the signer state machine intentionally permits when a rejection reason is re-evaluable (`should_reevaluate_reject_reason`) and the block is reproposed with the identical content/hash, or when validation/consensus state changes between the two evaluations.

### Impact Explanation
`SignerCoordinator::propose_block`/`get_block_status` checks the *rejection* condition before the *acceptance* condition: [4](#0-3) 

Because stale reject weight from a signer who has since accepted is never purged (only a full-timeout `reset_rejections` clears the tally, not a per-signer correction), the aggregated `total_weight_rejected` can cross the blocking-minority threshold using weight that no longer reflects that signer's actual (accepted) vote. When this happens, the coordinator returns `Err(NakamotoNodeError::SignersRejected { .. })` and gives up on the block/proposal — even though the true, current tally of accepts (including the flipped signer) may have reached or been close to the 70% acceptance threshold. This is a liveness/self-DoS wedge on the miner's block-assembly path: a legitimately signable block can be discarded, forcing needless re-proposals and excluding transactions based on stale minority-reject accounting rather than the signers' current, verified state.

### Likelihood Explanation
Requires only ordinary protocol behavior from a single signer (no majority, no other signer's key, no auth bypass): reject a block, then later accept the identical block hash after re-evaluation. The signer flow explicitly supports this re-evaluation path (`should_reevaluate_reject_reason`, `should_reevaluate_block`, `handle_block_pre_commit` and `store_and_process_block_signature` re-processing paths in `stacks-signer/src/v0/signer.rs`), and StackerDB slot chunk overwrites make it straightforward for the coordinator's listener to observe both messages for the same slot/hash. No special timing or race beyond normal operation is needed, though it does require the reject-then-accept interleaving to occur before a timeout resets the tally.

### Recommendation
Track per-signer vote state (e.g., `HashMap<slot_id, VoteKind>` or gate both `Accepted` and `Rejected` branches on a single unified "last known response" set) so that a signer's weight is attributed to exactly one side (approved or rejected) at any time. When a signer's second, differing response for the same block hash arrives, subtract the previous contribution from the stale bucket before adding it to the new one, ensuring `total_weight_approved` and `total_weight_rejected` reconcile with the current, most-recent verified response per signer.

### Proof of Concept
1. Coordinator sends `BlockProposal` for block `B` (hash `H`) to signer set with weight distribution `{S1: w1, S2: w2, ..., Sn: wn}`, threshold `T = 70%` of total weight.
2. Signer `S1` initially rejects `B` (e.g., due to a transient validation issue) → `handle_block_response` in `stackerdb_listener.rs` processes `Rejected`, `total_weight_rejected += w1` (via `responded_signers.insert` returning true).
3. `S1`'s local state re-evaluates `B` (same hash `H`) after conditions change (`should_reevaluate_reject_reason` allows it) and sends `Accepted` for `H`.
4. Listener processes `Accepted`: `gathered_signatures` does not yet contain `S1`'s slot, so `total_weight_approved += w1` is also applied — `w1` is now double-booked into both totals.
5. If enough other signers also reject (independently, honestly) such that `total_weight_rejected.saturating_add(weight_threshold) > total_weight` becomes true purely because `w1` is still counted on the reject side, `get_block_status` returns `SignersRejected` and the miner abandons `B`, even though `S1`'s true, current vote is Accept and the real accept tally (including `w1`) may already be at or near `T`.

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L70-82)
```rust
#[derive(Debug, Clone)]
pub struct BlockStatus {
    /// Set of the slot ids of signers who have responded
    pub responded_signers: HashSet<u32>,
    /// Map of the slot id of signers who have signed the block and their signature
    pub gathered_signatures: BTreeMap<u32, MessageSignature>,
    /// Total weight of signers who have signed the block
    pub total_weight_approved: u32,
    /// Total weight of signers who have rejected the block
    pub total_weight_rejected: u32,
    /// Per-txid rejection tracking from signers
    pub failed_txids: HashMap<Txid, FailedTxInfo>,
}
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
