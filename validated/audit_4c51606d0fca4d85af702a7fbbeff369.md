## Analysis

The reachable analog of the TensorFlow bug class (missing bounds/state validation on an aggregating counter, causing values to silently corrupt) exists in the node-side `StackerDBListener`, which tracks per-block signer weight tallies used by the mining coordinator to decide whether a block proposal has been globally accepted or rejected. [1](#0-0) [2](#0-1) 

### Title
Stale rejection weight is never retracted when a signer later accepts the same block, corrupting the miner's approved/rejected weight tally - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener` maintains two independent, monotonically-increasing counters per tracked block: `total_weight_approved` and `total_weight_rejected` [3](#0-2) . When a `BlockResponse::Rejected` arrives, the signer's weight is added to `total_weight_rejected` and its slot is recorded in `responded_signers` [4](#0-3) . If that same signer later re-evaluates and sends `BlockResponse::Accepted` for the identical block (the v0 signer explicitly supports this: `should_reevaluate_block` / `should_reevaluate_reject_reason` route a stale rejection back through evaluation and allow it to flip to acceptance), the `Accepted` branch only checks `gathered_signatures.contains_key(&slot_id)` before adding the weight to `total_weight_approved` [5](#0-4) . It never inspects or clears the signer's prior contribution to `total_weight_rejected`. The result: that signer's weight is now counted simultaneously in both buckets.

### Finding Description
The equality that should hold is that each signer's weight is attributable to at most one live disposition (approve xor reject) at any time — i.e. `total_weight_approved + total_weight_rejected` should never exceed the true weight of signers who currently hold that position. The code breaks this: a signer that rejects then later accepts (a state transition the v0 signer's re-evaluation logic explicitly permits, see `docs/signer-flows.md` §3/§6 and `should_reevaluate_reject_reason`) leaves its weight permanently double-booked — present in `total_weight_rejected` from the old vote and added again to `total_weight_approved` from the new one, with no corresponding decrement.

This is directly analogous to the reported CWE-120/787 class: an aggregation/counter (`total_weight_rejected`) is updated without validating that the entry it represents is still "live" for that side of the tally, letting the counter drift out of sync with the real, current state of votes it's supposed to summarize — the exact bug shape of `SparseCountSparseOutput` accepting an unvalidated/stale index and corrupting adjacent memory/counts.

### Impact Explanation
The miner's `SignerCoordinator::get_block_status` decision loop relies purely on these two counters: rejection is declared once `total_weight_rejected.saturating_add(weight_threshold) > total_weight` [6](#0-5) , and acceptance is declared once `total_weight_approved >= weight_threshold` [7](#0-6) . Because `total_weight_rejected` is stale and never retracted, a set of signers who initially rejected but later switched to accepting the same proposal can cause the coordinator to spuriously declare `NakamotoNodeError::SignersRejected` (or delay acceptance detection) even though those signers' *current* position, added to other accepting signers, is enough to legitimately clear the 70% threshold. This wedges tenure progress for that block: the miner treats a proposal that is (or is about to be) validly signed as terminally rejected, discarding it and excluding transactions, purely due to leftover accounting from votes that no longer reflect the signers' live state. `reset_rejections` only clears this on a full rejection-timeout cycle [8](#0-7) , so the corrupted state can persist and repeatedly mislead the coordinator across timeout iterations until that reset fires.

This is a liveness-class defect: it does not let an invalid/non-canonical block get signed (the cryptographic `verify_signer_signatures` check on the real assembled header at `stackslib/src/chainstate/nakamoto/mod.rs:1097-1190` is unaffected and still requires genuine, currently valid signatures), but it can wedge a miner's local decision loop into treating a legitimately-signable block as rejected based on stale counter state, matching the "High" bucket ("a signer/miner wedged ... acting on a stale ... threshold").

### Likelihood Explanation
A single miner, by re-sending (re-proposing) the identical block proposal after it has already collected some rejections whose cause is transient (e.g. `NoSignerConsensus`, `ConnectivityIssues`, or a reason the v0 signer classifies as re-evaluable via `should_reevaluate_reject_reason`), can reliably induce honest signers to flip their vote from reject to accept for the same `signer_signature_hash`, triggering this double-count without needing control of any signer key, a majority of signers, or StackerDB-transport tampering — purely by leveraging the signer set's own legitimate re-evaluation behavior described in `docs/signer-flows.md`.

### Recommendation
When processing a `BlockResponse::Accepted` for a slot that is present in `responded_signers` as a prior rejecter (i.e., not yet in `gathered_signatures` but weight already counted in `total_weight_rejected`), first `saturating_sub` that signer's weight from `total_weight_rejected` before adding it to `total_weight_approved`. Symmetrically, the `Rejected` branch should subtract stale approved weight if a signer flips from accept to reject (though the docs suggest acceptance is meant to be terminal once counted — that assumption should be made an explicit invariant enforced here, not merely assumed). At minimum, track a single per-slot "current disposition" enum rather than two independently-incremented saturating counters, so the two tallies cannot both include the same signer's weight simultaneously.

### Proof of Concept
1. Miner proposes block `B`. `insert_block` initializes `total_weight_approved = 0`, `total_weight_rejected = 0` [9](#0-8) .
2. Signer `S` (weight `w`) evaluates `B`, hits a transient/re-evaluable rejection reason, and broadcasts `BlockResponse::Rejected` for `B`. Listener: `total_weight_rejected += w`, `responded_signers.insert(S)` [4](#0-3) .
3. Miner re-sends the identical, unchanged `B` (still same `signer_signature_hash`). Signer `S`'s local re-evaluation logic (`should_reevaluate_reject_reason`) determines the rejection reason no longer holds and re-validates `B`, this time signing and broadcasting `BlockResponse::Accepted`.
4. Listener processes the `Accepted`: since `gathered_signatures` does not yet contain `S`'s slot, `total_weight_approved += w` is applied [5](#0-4) . `total_weight_rejected` is left unchanged at its old value including `w`.
5. Now `total_weight_approved + total_weight_rejected > total_weight` (or, more directly, the "rejected" bucket over-represents the real current oppose-weight by `w`), so a subsequent `total_weight_rejected.saturating_add(weight_threshold) > total_weight` check in `get_block_status` can fire and declare the block globally rejected even though `S`'s real, current vote is "accept," corrupting the miner's decision despite no signer ever holding two live votes simultaneously.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L486-518)
```rust
                    SignerMessageV0::BlockResponse(BlockResponse::Rejected(rejected_data)) => {
                        let (lock, cvar) = &*self.blocks;
                        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");

                        let Some(block) = blocks.get_mut(&rejected_data.signer_signature_hash)
                        else {
                            info!(
                                "StackerDBListener: Received rejection for block that we did not request. Ignoring.";
                                "signer_signature_hash" => %rejected_data.signer_signature_hash,
                                "slot_id" => slot_id,
                                "signer_set" => self.signer_set,
                            );
                            continue;
                        };

                        let rejected_pubkey = match rejected_data.recover_public_key() {
                            Ok(rejected_pubkey) => {
                                if rejected_pubkey != signer_pubkey {
                                    warn!("StackerDBListener: Recovered public key from rejected data does not match signer's public key. Ignoring.");
                                    continue;
                                }
                                rejected_pubkey
                            }
                            Err(e) => {
                                warn!("StackerDBListener: Failed to recover public key from rejected data: {e:?}. Ignoring.");
                                continue;
                            }
                        };

                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L692-704)
```rust
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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-518)
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
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L541-545)
```rust
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```
