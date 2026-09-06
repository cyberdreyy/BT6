This confirms the vulnerability. `BlockStatus` at [1](#0-0)  tracks `responded_signers`, `gathered_signatures`, `total_weight_approved`, and `total_weight_rejected` as independent add-only accumulators with no reconciliation between the two weight pools when the same signer's vote flips.

### Title
A signer's changed vote is double-counted across both accept and reject weight pools, inflating `total_weight_approved`/`total_weight_rejected` beyond the true tally - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
The `StackerDBListener` tallies signer votes for a proposed block into two independent, add-only counters: `total_weight_approved` (gated by membership in `gathered_signatures`) and `total_weight_rejected` (gated by membership in `responded_signers`). Because these are two different sets, a single signer that first rejects and later accepts the same block (or vice versa) has its weight added to both pools, with no subtraction ever performed. This is structurally identical to the referenced M-1 finding: a value is only ever incremented and never written down when the underlying position changes, so the aggregate used for a threshold decision becomes inflated relative to reality.

### Finding Description
In the `Rejected` branch, weight is added to `total_weight_rejected` only the first time a slot id is inserted into `responded_signers`: [2](#0-1) 

In the `Accepted` branch, weight is added to `total_weight_approved` based on a *different* condition — whether the slot id is already present in `gathered_signatures` — and `responded_signers.insert(slot_id)` is called unconditionally afterward, independent of whether this is a "new" acceptance: [3](#0-2) 

Because the "have we counted this signer's weight in pool X" gate for accept (`gathered_signatures`) and the gate for reject (`responded_signers`) are two disjoint sets, the code never checks "did this signer already contribute weight to the *other* pool." A single signer, using nothing but their own StackerDB slot, can:

1. Send `BlockResponse::Rejected` for a given `signer_signature_hash` first. `responded_signers` gains the slot id, `total_weight_rejected += weight`.
2. Later send `BlockResponse::Accepted` for the *same* block hash (this is a legitimate re-evaluation path per `docs/signer-flows.md` section 2's `LocallyRejected --> LocallyAccepted: re-evaluated` transition, so a real signer can organically do this, and a malicious signer can trivially replay/overwrite their own slot to do it at will). Since `gathered_signatures` does not yet contain this slot id, the `Accepted` branch's guard is satisfied and `total_weight_approved += weight` fires again.

The signer's weight is now counted in *both* `total_weight_rejected` and `total_weight_approved` simultaneously, with no decrement ever applied to the stale pool. This breaks the "aggregated-weight vs verified-accepts" equality: the aggregated weight no longer reflects the verified, current set of votes.

### Impact Explanation
This is consumed directly by `SignerCoordinator::get_block_status` in the polling loop, which checks rejection-crosses-threshold and acceptance-crosses-threshold against the same inflated counters: [4](#0-3) 

A single signer flipping its vote can inflate both pools simultaneously, letting the coordinator observe `total_weight_approved >= self.weight_threshold` (pushing/adopting a block) even though a portion of that weight also sits, uncounted-for-removal, in `total_weight_rejected` reflecting a rejection that was never actually retracted from the tally, or vice versa (causing a spurious global rejection). This is exactly the "rejection recounted as an accept" pattern called out as a Critical-tier impact: a rejection's weight is never cleared, and an accept from the same signer is layered on top rather than replacing it, so the coordinator's decision no longer reflects the verified current state of signer votes.

### Likelihood Explanation
This requires only a single signer (one StackerDB slot) sending two ordinary, protocol-legal messages (`Rejected` then `Accepted`, or `Accepted` then `Rejected`) for the same block hash — no majority collusion, no key compromise, and no exotic network condition. The re-evaluation path (`LocallyRejected → LocallyAccepted`) documented in `docs/signer-flows.md` shows this vote flip is a normal, expected occurrence in the v0 signer's own state machine, not an edge case that requires malice — a benign signer can trigger it merely by re-evaluating a block after conditions change.

### Recommendation
Track a single per-slot vote outcome (e.g. `HashMap<u32, VoteOutcome>` where `VoteOutcome` is `Accepted(weight)` or `Rejected(weight)`) instead of two independently-gated add-only counters. When a new message from a slot id supersedes a previous one, subtract the previous contribution from its pool before adding the new contribution to the other pool, so `total_weight_approved + total_weight_rejected` (restricted to slots that have actually responded) never exceeds the true tally of currently-held votes.

### Proof of Concept
1. Node proposes block `B` and registers it in `self.blocks` via `SignerCoordinator`/`StackerDBListener` bookkeeping.
2. Signer `S` (slot id `k`, weight `w`) sends `SignerMessageV0::BlockResponse(BlockResponse::Rejected(...))` for `B`'s `signer_signature_hash`. In `stackerdb_listener.rs`, `responded_signers.insert(k)` returns `true`, so `total_weight_rejected += w` (lines 515-518).
3. Signer `S` re-evaluates (or replays/overwrites its own StackerDB slot) and sends `SignerMessageV0::BlockResponse(BlockResponse::Accepted(...))` for the same `B`. In the `Accepted` branch, `gathered_signatures.contains_key(&k)` is `false` (never touched by the reject path), so `total_weight_approved += w` fires again (lines 443-446), and `responded_signers.insert(k)` is a no-op since `k` is already present.
4. Now `S`'s weight `w` is counted in both `total_weight_rejected` and `total_weight_approved`. If other signers' weights bring either counter near threshold, the coordinator in `get_block_status` (`signer_coordinator.rs` lines 509-545) can reach a decision using a tally that double-counts `S`, causing either a spurious rejection despite an outstanding accept from `S`, or a push/adoption decision while `S`'s rejection weight is still silently retained in `total_weight_rejected`.

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
