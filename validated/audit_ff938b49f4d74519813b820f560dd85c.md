### Title
Signer weight double-counted across `total_weight_approved` and `total_weight_rejected` when a single signer reverses its block response, allowing the node's coordinator to act on a stale/inflated aggregated weight - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListener` tallies two independent weight counters per block, `BlockStatus::total_weight_approved` and `BlockStatus::total_weight_rejected` [1](#0-0) , gated by two *different* de-duplication sets: the `Accepted` branch gates on `gathered_signatures.contains_key(&slot_id)`, while the `Rejected` branch gates on `responded_signers.insert(slot_id)` [2](#0-1) [3](#0-2) . When a signer first rejects and later reverses to accept the same block (a flow the signer protocol explicitly supports via reconsideration of certain reject reasons), the reject weight is never deducted, so the signer's weight ends up counted in *both* totals. This is structurally the same root cause as `DepositManager::_refundEntryFee` not deducting `referralRewards` that `_payEntryFee` had added: a state-reversing event fails to undo the bookkeeping performed by the original event, because the two paths use inconsistent dedup keys/gates.

### Finding Description
`BlockStatus` tracks `responded_signers`, `gathered_signatures`, `total_weight_approved`, and `total_weight_rejected` per proposed block, aggregated by the block-producing node's `StackerDBListener` as it receives `BlockResponse` messages from signers over StackerDB [1](#0-0) .

- On `BlockResponse::Rejected`, the code adds the signer's weight to `total_weight_rejected` **only if `responded_signers.insert(slot_id)` returns true** (i.e., first time this slot is seen at all) [3](#0-2) .
- On `BlockResponse::Accepted`, the code adds the signer's weight to `total_weight_approved` **only if `gathered_signatures.contains_key(&slot_id)` is false**, i.e. it checks a completely different map, not `responded_signers` [2](#0-1) .

Sequence that breaks the "aggregated weight reflects verified accepts/rejects" invariant:
1. Signer S (slot `k`, weight `w`) sends `Rejected` for block B. `responded_signers.insert(k)` → `true` (first occurrence) → `total_weight_rejected += w`.
2. Signer S later re-evaluates and sends `Accepted` for the *same* block B (the stacks-signer explicitly supports revising a prior rejection into an acceptance for certain reject reasons, per `should_reevaluate_reject_reason`/`should_reevaluate_block` in `stacks-signer/src/v0/signer.rs`, and this reversed message is broadcast over StackerDB like any other response). In the `Accepted` branch, `gathered_signatures.contains_key(k)` is `false` (S never appeared in `gathered_signatures` before), so `total_weight_approved += w` executes. `total_weight_rejected` is never decremented.

Result: `total_weight_approved + total_weight_rejected` for this block can exceed the true participating weight, with signer S's weight double-counted into both the approval and rejection tallies simultaneously. Note the reverse order (Accept-then-Reject) is *not* vulnerable, because the `Rejected` branch's gate (`responded_signers.insert`) was already flipped to `false` by the earlier Accept path (which also inserts into `responded_signers`), so only the Reject→Accept ordering triggers the flaw — exactly mirroring the report's directional asymmetry (add-without-corresponding-subtract on the reversing action).

This inflated `BlockStatus` is consumed directly by the miner's `SigningCoordinator::get_block_status` loop, which treats it as authoritative aggregated weight: it declares the block globally rejected once `total_weight_rejected + weight_threshold > total_weight` [4](#0-3) , or accepted once `total_weight_approved >= weight_threshold` [5](#0-4) . Because a single signer's weight can be latently counted in the rejection tally after it has actually voted to accept, the coordinator's stale `total_weight_rejected` can cross the blocking threshold and cause `NakamotoNodeError::SignersRejected` even though the true, current set of votes should have permitted acceptance — an "aggregated-weight vs verified-accepts" equality break driven purely by ordinary signer reconsideration, not by any signer misbehaving or requiring a majority.

The only remediation path present, `reset_rejections`, clears `total_weight_rejected` and `responded_signers` but is invoked only on the coordinator's own retry/timeout logic [6](#0-5) , not on receipt of a reversing `Accepted` message, so it does not protect the normal reconsideration flow described above.

### Impact Explanation
This causes the miner node to act on a stale/inflated aggregated weight when deciding whether a block proposal is approved or rejected. A single honest signer legitimately reconsidering its vote (Reject → Accept, a flow the signer software itself implements) permanently and silently inflates `total_weight_rejected` for that block for the lifetime of the `BlockStatus` entry (until `reset_rejections` is separately triggered by an unrelated timeout). This can push the block's rejection tally over the 30%-blocking-minority threshold purely from stale weight, causing the coordinator to spuriously treat a block as `SignersRejected` and abandon it, degrading liveness of block production without requiring any adversarial majority — matching the "acting on a stale reward set/threshold" class of High-impact findings.

### Likelihood Explanation
This requires no cooperation from other signers and no majority: a single signer that first rejects a proposal (e.g., due to a transiently-failing chainstate check) and later legitimately reconsiders to accept it (a case the signer protocol explicitly plans for via reject-reason reconsideration) is sufficient to trigger the double count. Because vote reconsideration is a designed, expected behavior of the signer set (not an edge case), the ordering Reject→Accept can arise organically under normal network conditions (e.g. slow validation, transient chain state, or reorg-related rejections that later resolve), making this readily reachable in production.

### Recommendation
Use a single, consistent per-slot vote-tracking structure (e.g., store the last known vote kind and weight per `slot_id`) instead of two independently-gated counters. When a signer's response for a block changes from Rejected to Accepted (or vice versa), the aggregator must first subtract the weight previously attributed to the old vote before adding the weight for the new vote, mirroring how `_refundEntryFee` should deduct the referral reward that `_payEntryFee` had added. Concretely, in `stackerdb_listener.rs`, gate both the `Accepted` and `Rejected` branches on the *same* per-slot "current vote" map, and on a vote flip: decrement the counter for the prior vote kind and increment the counter for the new one, keeping `total_weight_approved + total_weight_rejected` bounded by the true count of distinct, currently-held votes.

### Proof of Concept
1. Node proposes block B; `StackerDBListenerComms::insert_block` initializes `BlockStatus { responded_signers: {}, gathered_signatures: {}, total_weight_approved: 0, total_weight_rejected: 0, .. }` [7](#0-6) .
2. Signer S (slot `k`, weight `w`) sends `BlockResponse::Rejected` for B (e.g., its chainstate re-check temporarily fails). Listener: `responded_signers.insert(k)` → true → `total_weight_rejected = w` [3](#0-2) .
3. S later re-evaluates the same block (the chainstate condition that caused the rejection resolves, or the reject reason is one the signer is designed to reconsider) and sends `BlockResponse::Accepted` for B. Listener: `gathered_signatures.contains_key(k)` is false → `total_weight_approved = w`; `gathered_signatures.insert(k, sig)`; `responded_signers.insert(k)` (no-op, already present) [2](#0-1) .
4. `BlockStatus` for B now reports `total_weight_approved = w` **and** `total_weight_rejected = w` simultaneously — `w` is double-counted across both tallies from a single signer's weight, even though S currently only has one live vote (Accept).
5. `SigningCoordinator::get_block_status` observes this combined status; depending on other signers' weights, the stale `total_weight_rejected` contribution from S can push `total_weight_rejected + weight_threshold > total_weight`, causing the miner to return `NakamotoNodeError::SignersRejected` for a block that a correct tally (reflecting S's actual current Accept vote) would not have rejected [8](#0-7) .

Note: I was not able to execute this scenario in a live cluster from this environment; the analysis is derived from static code inspection of `stackerdb_listener.rs` and `signer_coordinator.rs`, and from the documented, explicitly-supported "signer reconsiders a block proposal it previously rejected" behavior referenced in the CHANGELOG entry for reject-reason reconsideration [9](#0-8) .

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L691-704)
```rust
impl StackerDBListenerComms {
    /// Insert a block into the block status map with initial values.
    pub fn insert_block(&self, block: &NakamotoBlockHeader) {
        let (lock, _cvar) = &*self.blocks;
        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");
        let block_status = BlockStatus {
            responded_signers: HashSet::new(),
            gathered_signatures: BTreeMap::new(),
            total_weight_approved: 0,
            total_weight_rejected: 0,
            failed_txids: HashMap::new(),
        };
        blocks.insert(block.signer_signature_hash(), block_status);
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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L487-540)
```rust
            if rejections != block_status.total_weight_rejected {
                rejections = block_status.total_weight_rejected;
                let (rejections_step, new_rejections_timeout) = self
                    .block_rejection_timeout_steps
                    .range((Included(0), Included(rejections)))
                    .last()
                    .ok_or_else(|| {
                        NakamotoNodeError::SigningCoordinatorFailure(
                            "Invalid rejection timeout step function definition".into(),
                        )
                    })?;
                rejections_timeout = new_rejections_timeout;
                info!("Number of received rejections updated, resetting timeout";
                                    "rejections" => rejections,
                                    "rejections_timeout" => rejections_timeout.as_secs(),
                                    "rejections_step" => rejections_step,
                                    "rejections_threshold" => self.total_weight.saturating_sub(self.weight_threshold));

                counters.set_miner_current_rejections_timeout_secs(rejections_timeout.as_secs());
                counters.set_miner_current_rejections(rejections);
            }

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
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L541-545)
```rust
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```

**File:** stacks-signer/CHANGELOG.md (L180-180)
```markdown
- For some rejection reasons, a signer will reconsider a block proposal that it previously rejected ([#5880](https://github.com/stacks-network/stacks-core/pull/5880))
```
