### Title
Signer weight double-counted across the accept and reject tallies in `StackerDBListener` — ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`BlockStatus` maintains two counters, `total_weight_approved` and `total_weight_rejected`, that are supposed to be a disjoint partition of signer weight for a given block. The guard that prevents double counting on the acceptance path only checks `gathered_signatures` (which records signed acceptances), not the shared `responded_signers` set that is also written to by the rejection path. A signer that first rejects and later accepts the same block (a normal, miner-triggerable re-evaluation scenario) has its weight added to `total_weight_rejected` and then, on the later acceptance, added again to `total_weight_approved`, because the acceptance-side idempotency check never looks at the fact that this signer already contributed weight on the rejection side.

### Finding Description
`BlockStatus` is defined at [1](#0-0) 
with a single shared `responded_signers: HashSet<u32>` used by both response kinds, and a separate `gathered_signatures: BTreeMap<u32, MessageSignature>` used only for acceptances.

On the acceptance path, weight is only added if the slot is not already in `gathered_signatures`: [2](#0-1) 

On the rejection path, weight is added if the slot is not already in `responded_signers`: [3](#0-2) 

This is exactly the C-02 pattern: an entity (here, a signer's weight) is added into a second bucket without checking whether it is already accounted for in another bucket that is supposed to be mutually exclusive with it. Just as the "top contributors" list assumed the new contributor wasn't already present before evicting the lowest, this code assumes a signer hasn't already voted on this block via the *other* response kind before crediting its weight again.

Concretely: if signer S first sends `BlockResponse::Rejected` for block B, `responded_signers.insert(slot_id_S)` succeeds and `total_weight_rejected += weight_S`. If S later (after the miner re-sends/re-proposes the same block, which is standard behavior — see `handle_block_proposal` / `should_reevaluate_block` in `stacks-signer/src/v0/signer.rs`, and the documented re-evaluation flow in `docs/signer-flows.md`) sends `BlockResponse::Accepted` for the same block, the check `!block.gathered_signatures.contains_key(&slot_id)` is true (S never accepted before), so `total_weight_approved += weight_S` fires too. Now S's weight is counted in *both* `total_weight_approved` and `total_weight_rejected`, breaking the equality that the coordinator relies on: aggregated approved+rejected weight is supposed to reflect disjoint sets of signer weight, but now the sum can exceed `total_weight`.

### Impact Explanation
`SignerCoordinator::get_block_status` uses these two counters independently to decide whether a block is accepted or rejected: [4](#0-3) 
Because a flip-flopping signer's weight is not removed from `total_weight_rejected` when it later contributes to `total_weight_approved` (or vice versa), the node can reach the acceptance weight threshold using weight that is simultaneously still "spent" against the rejection tally, and reach the rejection threshold using weight that was already promised to acceptance. This corrupts the aggregated-weight-vs-verified-accepts equality the coordinator depends on to decide block acceptance/rejection, which can push a block over (or block it from) the 70%/30% thresholds using inflated, double-counted weight rather than a genuine disjoint tally of votes.

### Likelihood Explanation
No majority collusion or privileged access is required. Re-proposal of a block by the miner after a rejection is a routine, expected event (documented and tested elsewhere, e.g. `signers_reprocess_late_block_proposals_pre_commits` in `stacks-node/src/tests/signer/v0/signers_consider_late_proposals.rs`), and an individual signer legitimately changing its verdict from Rejected to Accepted on re-evaluation is a normal code path (`should_reevaluate_block`). The miner fully controls when/whether to resend a proposal, and gossip of the resulting `BlockResponse` messages is all that's needed to trigger the double count — no special signer collusion needed.

### Recommendation
Make the acceptance and rejection weight accounting mutually exclusive: before crediting weight on either path, check whether the signer's slot has already contributed weight to the *other* bucket, and if so, subtract/adjust rather than additively crediting both. E.g., track a per-slot "last recorded weighted outcome" and only ever have at most one of `total_weight_approved`/`total_weight_rejected` reflect that signer's weight at a time, mirroring the C-02 fix pattern of checking prior membership before mutating a bucket that is supposed to be disjoint from another.

### Proof of Concept
1. Configure a signer set with a threshold requiring 70% weight, with signer S holding weight `w_S`.
2. Miner proposes block B; signer S evaluates and rejects (e.g., transient chainstate check failure) → `BlockResponse::Rejected` is gossiped and processed by `StackerDBListener`: `responded_signers.insert(slot_S)`, `total_weight_rejected += w_S` (stackerdb_listener.rs:515-518).
3. Miner re-sends the identical block proposal (standard retry/re-proposal behavior). Signer S re-evaluates via `should_reevaluate_block`/pre-commit path and this time accepts, broadcasting `BlockResponse::Accepted`.
4. `StackerDBListener` processes the acceptance: `gathered_signatures.contains_key(slot_S)` is `false` (S never accepted before), so `total_weight_approved += w_S` fires (stackerdb_listener.rs:443-465), even though `w_S` is already counted in `total_weight_rejected`.
5. `total_weight_approved + total_weight_rejected` now exceeds the real total signer weight for this block by `w_S`; `get_block_status` in `signer_coordinator.rs` can reach the acceptance (or rejection) threshold using this double-counted weight.

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
