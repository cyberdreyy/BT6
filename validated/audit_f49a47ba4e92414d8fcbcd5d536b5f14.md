## Title
`FailedTxInfo` per-txid rejection weight is never reset alongside `total_weight_rejected`, letting a single signer's repeated rejections accumulate unbounded weight and trigger a permanent txid ban - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListenerComms::reset_rejections` is called by the `SignerCoordinator` every time a block proposal round times out, in order to let signers reconsider and re-reject a re-proposed block. It clears `responded_signers` and zeroes `total_weight_rejected`, but it does **not** clear the per-txid `failed_txids` map (`FailedTxInfo::total_weight` / `problematic_weight`). Because `responded_signers` is wiped, the very same signer is free to have their rejection re-tallied into `failed_txids` on the next round, while the old tally for that txid is never subtracted out. Across repeated timeout/retry cycles this lets a single signer's weight accumulate into a per-txid running total that has no relation to the actual distinct signing weight backing it, eventually exceeding `blocking_minority` and causing the txid to be permanently excluded network-wide - the exact same "asymmetric increment vs. non-reset decrement" accounting bug as the reported `netAssetDeposits` issue, just manifesting as counter inflation rather than underflow.

### Finding Description
`BlockStatus` tracks two logically-coupled counters for a rejected proposal: [1](#0-0) 

- `total_weight_rejected`: overall rejection weight, gated by `responded_signers` (each slot counted once).
- `failed_txids: HashMap<Txid, FailedTxInfo>`: per-txid weight, also gated by the same `responded_signers.insert(slot_id)` check.

Both are incremented together, guarded by the *same* `if block.responded_signers.insert(slot_id)` gate: [2](#0-1) 

When the miner-side coordinator times out waiting for a decision, it calls `reset_rejections`, intending to let the same block be retried and signers to re-vote: [3](#0-2) 

This function clears `responded_signers` (re-adding only slots that had *approved*, per the "approvals cannot be undone" comment) and zeroes `total_weight_rejected`. It never touches `block.failed_txids`. That breaks the invariant that made the two counters consistent: after a reset, a signer who rejected in round 1 can reject again in round 2 (or round 3, ...). Each time, `responded_signers.insert(slot_id)` returns `true` again (since it was cleared), so:
- `total_weight_rejected` is correctly rebuilt for the *current* round only (bounded by real distinct weight, since it resets to 0 first), but
- `failed_txids[txid].total_weight` / `problematic_weight` keep accumulating **on top of** their pre-reset values, with no corresponding reset.

Over N timeout/retry cycles, a single persistently-rejecting signer of weight `w` can drive `failed_txids[txid].total_weight` to `N * w`, which is used, uncapped by any real total-weight bound, in the ban decision: [4](#0-3) 

`info.total_weight > blocking_minority` and `info.problematic_weight > blocking_minority` are meant to represent ">30% of *real, current* signer weight agrees this txid is bad." Because the per-txid tally is never reset while the qualifying `responded_signers` set is, that guarantee no longer holds: after enough retries even one dissenting signer's weight can exceed `blocking_minority`, causing `permanently_excluded_txids` to include a transaction that in reality only a small minority (or a single signer) ever flagged.

### Impact Explanation
This causes a liveness/censorship problem in the block-building pipeline: a single signer (not a majority) can force `permanently_excluded_txids` to include an arbitrary transaction it dislikes, purely by repeatedly rejecting proposals across retries (each retry is driven by the coordinator's own timeout logic, not by anything the attacker directly controls beyond continuing to reject). This is a miscounted response ("rejection weight recounted/inflated across resets") that lets weight thresholds meant to require broad signer agreement be satisfied by one persistent minority signer, matching the "rejection recounted" class of issue and the "wedged into acting on a stale/incorrect threshold" liveness category.

### Likelihood Explanation
Reaching this requires only: (1) a proposal that repeatedly times out (a routine occurrence whenever a blocking minority — or even less, given this bug — disagrees, controlled purely by `block_rejection_timeout_steps`/`rejections_timeout`), and (2) one signer continuing to reject the *same* txid/block across those retries. No majority, no other signer's key, and no special access are needed — this is directly reachable by any single participating signer through ordinary protocol messages.

### Recommendation
Reset (or otherwise decay) `failed_txids` in `reset_rejections` whenever `responded_signers`/`total_weight_rejected` are reset, so that the per-txid tally always reflects only the current round's distinct rejecting weight — mirroring the external report's fix of keeping the two accounting variables (increment path and decrement/reset path) consistent rather than letting one be transient and the other cumulative.

### Proof of Concept
1. Miner proposes block B containing tx T.
2. Signer S (weight `w`, well under `blocking_minority` alone) rejects B citing T as `ProblematicTransaction`. `failed_txids[T].total_weight = w`, `problematic_weight = w`.
3. Coordinator's wait times out (`rejections_timer.elapsed() > rejections_timeout`); `reset_rejections` is called — `responded_signers` and `total_weight_rejected` reset to 0, but `failed_txids[T]` retains `w`/`w`.
4. Miner re-proposes the same (or an updated) block containing T; S rejects again citing T. Because `responded_signers` was cleared, the gate re-fires: `failed_txids[T].total_weight` becomes `2w`, `problematic_weight` becomes `2w`.
5. Repeat steps 3-4 enough times that `n*w > blocking_minority` (`total_weight - weight_threshold`), even though only signer S (weight `w << blocking_minority`) ever objected.
6. `signer_coordinator.rs`'s rejection-handling branch now marks T as permanently excluded via `permanently_excluded_txids`, based on an inflated, cross-round-accumulated weight that never represented real concurrent signer agreement.

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L60-82)
```rust
/// Tracks per-txid rejection data from signers
#[derive(Debug, Clone, Default)]
pub struct FailedTxInfo {
    /// The total weight of signers who reported this txid as failed
    pub total_weight: u32,
    /// The weight of signers who specifically reported this txid as
    /// genuinely problematic (e.g. DDoS vector, parse error, Clarity crash)
    pub problematic_weight: u32,
}

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-546)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);

                            // Track transactions that failed validation, accumulating
                            // per-txid signer weight and whether any signer flagged
                            // the tx as genuinely problematic.
                            if let Some(txid) = &rejected_data.response_data.failed_txid {
                                match &rejected_data.reason_code {
                                    RejectCode::ValidationFailed(
                                        ValidateRejectCode::BadTransaction
                                        | ValidateRejectCode::ProblematicTransaction,
                                    ) => {
                                        let info =
                                            block.failed_txids.entry(txid.clone()).or_default();
                                        info.total_weight =
                                            info.total_weight.saturating_add(signer_entry.weight);
                                        if matches!(
                                            rejected_data.reason_code,
                                            RejectCode::ValidationFailed(
                                                ValidateRejectCode::ProblematicTransaction
                                            )
                                        ) {
                                            info.problematic_weight = info
                                                .problematic_weight
                                                .saturating_add(signer_entry.weight);
                                        }
                                    }
                                    _ => {}
                                }
                            }
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L706-723)
```rust
    /// Reset rejections for a block proposal.
    /// This is used when a block proposal times out and we need to retry it by
    /// clearing the block's rejections. Block approvals cannot be cleared
    /// because an old approval could always be used to make a block reach
    /// the approval threshold.
    pub fn reset_rejections(&self, signer_sighash: &Sha512Trunc256Sum) {
        let (lock, _cvar) = &*self.blocks;
        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");
        if let Some(block) = blocks.get_mut(signer_sighash) {
            block.responded_signers.clear();
            block.total_weight_rejected = 0;

            // Add approving signers back to the responded signers set
            for (slot_id, _) in block.gathered_signatures.iter() {
                block.responded_signers.insert(*slot_id);
            }
        }
    }
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-535)
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
```
