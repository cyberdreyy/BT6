### Title
Signer vote flip (reject → accept) double-counts weight and poisons the rejection tally, causing the miner to spuriously treat a validly-signed block as rejected - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener::run` tallies signer weight for a proposed block into two independent counters, `total_weight_approved` and `total_weight_rejected`, gated by two different, unrelated sets (`gathered_signatures` and `responded_signers`). A signer that rejects a block and later legitimately reconsiders and accepts it gets its weight added to *both* counters, and the stale rejected-weight is never removed. This can make the miner's rejection-threshold check fire even though the block has since gathered enough real signer support, wedging the miner into abandoning a valid block.

### Finding Description
In `stacks-node/src/nakamoto_node/stackerdb_listener.rs`, `BlockStatus` tracks two separate collections: [1](#0-0) 

For a `BlockResponse::Accepted` message, weight is only added to `total_weight_approved` if the slot is not already present in `gathered_signatures`: [2](#0-1) 

For a `BlockResponse::Rejected` message, weight is only added to `total_weight_rejected` if the slot is being inserted into `responded_signers` for the *first time ever* (regardless of vote direction): [3](#0-2) 

Because `responded_signers` is populated by *both* branches (the accept branch also calls `block.responded_signers.insert(slot_id)` at line 465), a Reject→Accept sequence for the same `signer_signature_hash` from the same signer slot behaves as follows:
1. Reject arrives first: `responded_signers.insert(slot_id)` returns `true` → `total_weight_rejected += weight`.
2. Accept arrives later: `gathered_signatures` does **not** contain `slot_id` (it was never populated on the reject path) → the accept-path guard is satisfied → `total_weight_approved += weight` as well.

The earlier contribution to `total_weight_rejected` is never subtracted. The reverse order (Accept→Reject) is correctly guarded, since the reject branch checks `responded_signers`, which the accept branch already populated — but the accept branch's guard (`gathered_signatures`) is not populated by the reject branch, so the asymmetry only protects one direction.

This is not a hypothetical scenario: the signer intentionally reconsiders certain rejection reasons and later signs the same block, as encoded in `should_reevaluate_reject_reason`: [4](#0-3) 

(also documented in the CHANGELOG: "For some rejection reasons, a signer will reconsider a block proposal that it previously rejected") [5](#0-4) 

so a signer transitioning from `Rejected` to `Accepted` for the very same block hash is an expected, reachable code path requiring no signer collusion or majority — a single signer's normal re-evaluation (or a malicious signer deliberately flip-flopping) is sufficient.

The consumer of these tallies, `SignerCoordinator::get_block_status`, checks the rejection threshold *before* the approval threshold: [6](#0-5) 

Because `total_weight_rejected` retains stale weight from signers who have since accepted, it can independently cross `total_weight - weight_threshold` (via this signer plus any other genuinely-still-rejecting or similarly-poisoned signers), causing the coordinator to return `NakamotoNodeError::SignersRejected` even though the block has actually gathered enough approving weight from the current votes of the signer set.

### Impact Explanation
This breaks the intended equality between "aggregated weight tallies" and "the set of signers who currently, validly endorse/reject the block." A single signer's weight can simultaneously inflate both the approve and the reject buckets for the same block, and the stale reject weight is permanently stuck. The miner-side coordinator can then declare a genuinely, validly-signed block as rejected (`SignersRejected`), forcing it to discard the block, exclude transactions, and potentially build a different (non-canonical relative to what signers actually support) block for the tenure — a liveness wedge reachable without any majority collusion, matching the "aggregated-weight vs verified-accepts" equality-break / rejection-recounted class of bug.

### Likelihood Explanation
Moderate-to-high. It requires only one signer to reject a block for a re-evaluable reason (e.g. `UnknownParent`, `NoSignerConsensus`, `ConnectivityIssues`, which are explicitly re-evaluated per `should_reevaluate_reject_reason`) and later accept the same block hash — an intended, documented signer behavior that can be triggered by ordinary network/timing variance (parent-block propagation delay) or deliberately by a single Byzantine signer. No majority of signers or special access is needed.

### Recommendation
Track a single current vote per signer slot instead of two independently-additive weight counters guarded by different sets. For example, store `HashMap<u32, Vote>` where `Vote` is `Accepted(weight)` or `Rejected(weight)`; when a new message for a slot arrives with a different vote than previously recorded, subtract the old vote's weight from its bucket before adding the new vote's weight to the new bucket. This guarantees `total_weight_approved + total_weight_rejected` never exceeds `total_weight` for a given block and that the tallies always reflect only the latest, current view of the signer set.

### Proof of Concept
1. Miner proposes block `B` with `signer_signature_hash = H`. `StackerDBListener` initializes `BlockStatus` for `H`.
2. Signer `S` (slot `k`, weight `w`) evaluates `B`, does not yet see its parent processed, and sends `BlockResponse::Rejected(H, reason=UnknownParent)`.
   - In `stackerdb_listener.rs` reject branch: `responded_signers.insert(k)` → `true` ⇒ `total_weight_rejected += w`.
3. Shortly after, `S`'s node processes the parent block; per `should_reevaluate_reject_reason`, `S` re-evaluates `B` and now sends `BlockResponse::Accepted(H, signature)`.
   - In `stackerdb_listener.rs` accept branch: `gathered_signatures.contains_key(k)` is `false` (never touched by the reject branch) ⇒ `total_weight_approved += w` as well.
4. Now `total_weight_rejected` for `H` still contains `w` from step 2, even though `S` has since accepted `B`. If enough other signers are in a similarly stale rejected state (or `S`'s weight alone is enough given the reward-set distribution), `SignerCoordinator::get_block_status` evaluates `total_weight_rejected.saturating_add(weight_threshold) > total_weight` as `true` and returns `Err(NakamotoNodeError::SignersRejected{..})`, even though the current, live vote of every signer (including `S`) may satisfy the approval threshold.

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

**File:** stacks-signer/CHANGELOG.md (L176-180)
```markdown
## [3.1.0.0.8.0]

### Changed

- For some rejection reasons, a signer will reconsider a block proposal that it previously rejected ([#5880](https://github.com/stacks-network/stacks-core/pull/5880))
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
