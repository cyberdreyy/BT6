### Title
Stale rejection weight is never reversed when a signer later accepts, causing an approved block to be misclassified as rejected - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`BlockStatus` in `stackerdb_listener.rs` tracks `total_weight_approved` and `total_weight_rejected` as two independently-accumulating, monotonically increasing counters (`saturating_add`, never decremented). Nothing removes a signer's weight from `total_weight_rejected` when that same signer later sends a valid `BlockResponse::Accepted` for the same block, even though the signer-side state machine (`stacks-signer/src/signerdb.rs`) explicitly allows a block to move `LocallyRejected -> LocallyAccepted` on re-evaluation. Because the coordinator's threshold check in `signer_coordinator.rs` evaluates the (never-decreasing) rejection tally before the approval tally, a block that legitimately reaches the 70% approval threshold via signers who had previously rejected it can still be discarded as `SignersRejected`.

### Finding Description
`BlockStatus` (`stacks-node/src/nakamoto_node/stackerdb_listener.rs:70-82`) holds `responded_signers` (gates the rejection tally) and `gathered_signatures` (gates the approval tally) as two separate, disjoint gating structures keyed by `slot_id`: [1](#0-0) 

When an `Accepted` message arrives, the code only checks `!block.gathered_signatures.contains_key(&slot_id)` before adding the signer's weight to `total_weight_approved`: [2](#0-1) 

When a `Rejected` message arrives, it only checks `block.responded_signers.insert(slot_id)` before adding the signer's weight to `total_weight_rejected`: [3](#0-2) 

Neither branch clears or decrements the *other* counter for that signer. So if a signer's slot first sends `Rejected` (weight added to `total_weight_rejected`) and later sends `Accepted` for the same `signer_signature_hash` (weight added to `total_weight_approved`), that signer's weight now sits in both tallies permanently — `total_weight_rejected` is never reduced.

This is the same bug class as the external report's `rebase()`: the accounting can only move a value up, never down/reverse, even though the real-world event (a signer legitimately changing its vote, analogous to a slashing/negative event) requires a decrease. The signer's own state machine explicitly permits this reconsideration: [4](#0-3) 

The miner-side coordinator's threshold logic in `signer_coordinator.rs` assumes `total_weight_rejected` reflects the *current* set of rejecting signers and checks it before the approval branch: [5](#0-4) 

Because rejection is checked first and its weight can never fall back down, a block that later gathers a legitimate ≥70% `total_weight_approved` (including from signers who flipped from reject to accept) can still be forced into the `SignersRejected` branch by the stale, never-decremented `total_weight_rejected` value from those same signers' earlier rejection.

### Impact Explanation
This causes the miner's `wait_for_block_status_and_maybe_broadcast` (in `signer_coordinator.rs`) to reach an incorrect, final decision (`Err(NakamotoNodeError::SignersRejected { .. })`) about a block that in fact reached legitimate consensus acceptance. This is a rejection-vs-acceptance miscount: a genuinely accepted block (weight recomputed correctly on the accept side) is discarded because the rejection side's weight was never allowed to decrease when the underlying signer's opinion changed. This matches the "rejection recounted as acceptance" class of critical outcome in reverse form — an already-approved outcome is wrongly recounted as rejected — and can also wedge liveness for that tenure (temporarily/permanently excluding transactions, per the surrounding code, or forcing the miner to abandon a validly-signed block).

### Likelihood Explanation
No majority collusion or private key access beyond the affected signer(s) themselves is required. Any single signer (or a small number of signers whose combined weight crosses the ~30% blocking-minority) reconsidering their vote — an explicitly-supported, ordinary path in the signer's own state machine (`LocallyRejected -> LocallyAccepted`) — triggers the stale accounting. This can happen during normal operation whenever chain state changes cause a signer to re-evaluate a previously-rejected proposal (e.g. `check_block_against_signer_db_state` passing on re-check), making the likelihood medium-to-high rather than requiring an adversarial majority.

### Recommendation
Make the two tallies mutually exclusive and reversible: when processing a `BlockResponse::Accepted` for a signer, if that signer's slot is already present in `responded_signers`/counted in `total_weight_rejected`, subtract their weight from `total_weight_rejected` before adding it to `total_weight_approved` (and symmetrically for a late-arriving `Rejected` overriding a prior `Accepted`, if that is intended to be permitted at all). Alternatively, track a per-signer "current vote" map and recompute both aggregate weights from that map on every update rather than accumulating separate saturating counters that can never decrease.

### Proof of Concept
1. Coordinator has `weight_threshold` = 70% of `total_weight`; assume a set of signers S with weight w_S ≈ 31% (a blocking minority) and the remaining signers R with weight w_R ≈ 69%.
2. Miner proposes block B. Signers in S validate B against a since-changed chainstate and initially return `Rejected` (e.g. transient `check_block_against_signer_db_state` failure). `stackerdb_listener.rs` records `total_weight_rejected = w_S` (~31%), inserting S's slot ids into `responded_signers`.
3. `signer_coordinator.rs`'s wait loop observes `total_weight_rejected.saturating_add(weight_threshold) > total_weight` is not yet true (31% + 70% may or may not exceed 100% depending on exact weights) — assume just under, so the loop keeps waiting.
4. Chain state advances; S's `check_block_against_signer_db_state` now passes, so per the documented `LocallyRejected -> LocallyAccepted` re-evaluation path, S now sends `Accepted` for the same `signer_signature_hash`.
5. `stackerdb_listener.rs`'s Accepted handler adds S's weight to `total_weight_approved` (now `total_weight_approved = w_S + w_R_partial`), but does NOT remove `w_S` from `total_weight_rejected`, which remains at ~31%.
6. Once R's signers also accept, `total_weight_approved` legitimately reaches the 70% threshold. But on the very next status poll, the coordinator's rejection check (`stacks-node/src/nakamoto_node/signer_coordinator.rs:509-513`) still sees the stale `total_weight_rejected` (~31%) satisfying `saturating_add(weight_threshold) > total_weight`, and — because this branch is evaluated before the approval branch — returns `Err(SignersRejected { .. })`, discarding a block that had, in fact, reached full valid signer consensus.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-464)
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
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-518)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);
```

**File:** docs/signer-flows.md (L142-145)
```markdown
    Unprocessed --> LocallyRejected : mark_locally_rejected
    PreCommitted --> LocallyRejected : mark_locally_rejected
    LocallyRejected --> LocallyAccepted : re-evaluated
    LocallyAccepted --> LocallyRejected : re-evaluated
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
