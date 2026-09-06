### Title
Signer vote-flip (Reject→Accept) causes double-counting of signer weight in the mining coordinator's approval/rejection tally - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener` maintains two independent running counters per block, `total_weight_approved` and `total_weight_rejected`, inside `BlockStatus` [1](#0-0) . These counters are incremented but never reconciled/decremented when a single signer changes its vote from `Rejected` to `Accepted` for the same block, causing that signer's weight to be counted in **both** totals simultaneously. This mirrors the reported bug class ("total counter not adjusted when a per-participant entry changes/disappears"), and it lets the sum `total_weight_approved + total_weight_rejected` exceed `total_weight` — breaking the equality the miner's `SignerCoordinator` relies on to decide whether a block was approved or rejected.

### Finding Description
In the `Accepted` branch, duplicate-add protection is keyed off `gathered_signatures`:
```
if !block.gathered_signatures.contains_key(&slot_id) {
    block.total_weight_approved = block.total_weight_approved.saturating_add(signer_entry.weight);
    ...
}
block.gathered_signatures.insert(slot_id, signature);
block.responded_signers.insert(slot_id);
``` [2](#0-1) 

In the `Rejected` branch, duplicate-add protection is keyed off a different set, `responded_signers`:
```
if block.responded_signers.insert(slot_id) {
    block.total_weight_rejected = block.total_weight_rejected.saturating_add(signer_entry.weight);
    ...
}
``` [3](#0-2) 

Because `Rejected` marks `responded_signers` (not `gathered_signatures`), a signer who first sends a rejection and later changes its mind and sends a valid acceptance is **not** blocked by the `gathered_signatures.contains_key(&slot_id)` check in the `Accepted` branch (that map is still empty for this signer). The acceptance therefore adds the signer's weight to `total_weight_approved` on top of the weight that already sits in `total_weight_rejected` from the earlier rejection — the rejection weight is never subtracted. (The reverse order, Accept-then-Reject, IS correctly guarded, because `responded_signers` is already set by the time the rejection message is processed — this asymmetry confirms the guard was intended to prevent exactly this kind of double count but was wired inconsistently between the two branches.)

`SignerCoordinator::run` (the caller) treats these two counters as if they partition the signer set and checks rejection first:
```
if block_status.total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight {
    ... return Err(NakamotoNodeError::SignersRejected { ... });
} else if block_status.total_weight_approved >= self.weight_threshold {
    ... return Ok(block_status.gathered_signatures.values().cloned().collect());
}
``` [4](#0-3) 

Because the rejected tally can retain "phantom" weight from a signer who has since actually accepted, `total_weight_rejected` can cross the blocking-minority threshold even though the real, current approval state (reflected correctly in `gathered_signatures`/`total_weight_approved`) has already reached the 70% signing threshold. The stale rejection weight is checked and can trip `SignersRejected` before the (also correct) approval branch is ever evaluated.

### Impact Explanation
This is a liveness wedge in the block-production coordinator: a block that legitimately has enough real acceptances to be signed can be spuriously discarded by the miner because of leftover weight from a signer's earlier, superseded rejection. This matches the "High" category — the coordinator acts on a stale/inflated threshold total rather than the true current signer state, degrading the miner's ability to move forward with an actually-approved block, and can also cause it to needlessly mark transactions problematic/excluded via the `failed_txids` accounting that is gated on the same inflated `total_weight_rejected`/blocking-minority computation [5](#0-4) .

### Likelihood Explanation
Triggerable by a single signer (no majority required): send a `BlockResponse::Rejected` for a proposal, then send a valid `BlockResponse::Accepted` for the same `signer_signature_hash` — both are ordinary, individually-valid signer messages a lone signer can produce over StackerDB (e.g., after re-evaluating the proposal, which the signer state machine explicitly allows, per `LocallyRejected --> LocallyAccepted` transitions documented for the signer side). No cooperation from other signers or from the miner is needed; only the ordering of the two messages as received by `StackerDBListener` matters.

### Recommendation
Use a single, consistent per-slot vote-state map (or reuse `responded_signers`/`gathered_signatures` symmetrically) to decide, for both branches, whether this is a genuinely new response versus a vote flip. On a flip, subtract the signer's weight from the previous category's total before adding it to the new one, e.g.:
```
if let Some(prev) = block.gathered_signatures.remove(&slot_id) {
    block.total_weight_approved = block.total_weight_approved.saturating_sub(signer_entry.weight);
}
// existing rejection add logic
```
and symmetrically clear/decrement `total_weight_rejected` when a later acceptance arrives for a slot that was previously counted as rejected.

### Proof of Concept
1. Miner opens a `SignerCoordinator`/`StackerDBListener` session for a block proposal with `total_weight` = 100, `weight_threshold` = 70 (70%).
2. Signer A (weight 31) sends `BlockResponse::Rejected` → `responded_signers.insert(A)` succeeds → `total_weight_rejected = 31`.
3. Enough other signers (weight 69 total) accept normally → `total_weight_approved = 69` (still below 70, held pending).
4. Signer A reconsiders and sends a valid `BlockResponse::Accepted` for the same block → `gathered_signatures.contains_key(A)` is false (A never appeared there) → `total_weight_approved = 69 + 31 = 100`, while `total_weight_rejected` remains `31` (never decremented).
5. `SignerCoordinator::run` evaluates the rejection branch first: `31 + 70 = 101 > 100` → returns `Err(SignersRejected)`, even though `total_weight_approved` (100) already exceeds the 70% threshold and A's real, current vote is "accept." The block is discarded despite having a legitimate quorum of acceptances.

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
