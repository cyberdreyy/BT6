### Title
Signer's rejection weight persists in `total_weight_rejected` after switching to acceptance, allowing a false global-rejection tally - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener::run` accumulates `total_weight_approved` and `total_weight_rejected` on `BlockStatus` using two independently-gated counters. The rejection path gates on `responded_signers.insert(slot_id)`, while the acceptance path gates on `gathered_signatures.contains_key(&slot_id)`. If a signer's rejection is processed first, then that signer changes its mind and later signs the very same proposal (a legitimate, supported flow in this codebase), the node adds the signer's weight to `total_weight_approved` on the later message, but never removes it from `total_weight_rejected`. The signer's weight is left "double-booked" in both aggregates, exactly analogous to the reported bug class where a running removal amount fails to net out a portion already accounted for elsewhere.

### Finding Description
`BlockStatus` tracks `total_weight_approved`, `total_weight_rejected`, `responded_signers`, and `gathered_signatures`. [1](#0-0) 

On an `Accepted` message, the dedup guard is `!block.gathered_signatures.contains_key(&slot_id)`; only then is `signer_entry.weight` added to `total_weight_approved`, and only afterward is the slot inserted into both `gathered_signatures` and `responded_signers`: [2](#0-1) 

On a `Rejected` message, the dedup guard is instead `block.responded_signers.insert(slot_id)`; only then is `signer_entry.weight` added to `total_weight_rejected`: [3](#0-2) 

Because these are two separate gates over two separate keys (`gathered_signatures` vs. `responded_signers`), a signer that rejects and is later re-processed as an acceptance for the *same* `block_sighash` passes both gates: `responded_signers.insert` returned `true` on the rejection (adding weight to `total_weight_rejected`), and `gathered_signatures.contains_key` is still `false` when the later acceptance arrives (since only the rejection path touched `responded_signers`, not `gathered_signatures`), so the acceptance path adds the same signer's weight to `total_weight_approved` as well. Nothing in the acceptance path subtracts the previously counted weight from `total_weight_rejected`.

This is directly analogous to the reported `removePriceImpactOpenInterest()` bug: a running aggregate (`total_weight_rejected`) is supposed to reflect only signers who currently oppose the block, but the code never nets out the portion that has since transitioned to a different bucket (approval) — just as the OI report's `expiredOiUsd` should have been subtracted (`min(deltaOiUsd, positionSizeUsd - expiredOiUsd)`) but wasn't for partial reductions.

That a signer can legitimately reject a proposal and later sign the identical proposal (same `signer_signature_hash`) after re-evaluation is a supported code path on the signer side — rejections are explicitly re-evaluable (`should_reevaluate_reject_reason`, `should_reevaluate_block`) and a signer can move from `LocallyRejected` to `LocallyAccepted` for the same block. `reset_rejections` (used on the coordinator's own timeout retries) also explicitly documents that "approvals cannot be cleared because an old approval could always be used to make a block reach the approval threshold" — i.e., the code authors were aware that stale entries persist and only handled the retry/reset scenario for stale *approvals*, not the reject→accept transition for a single signer. [4](#0-3) 

### Impact Explanation
The miner's `SignCoordinator::run` treats `total_weight_rejected` as the authoritative measure of blocking-minority opposition and rejects the block outright as soon as it crosses the threshold, aborting mining of that proposal: [5](#0-4) 

Because a signer that switched from reject to accept still contributes stale weight to `total_weight_rejected`, the aggregate can overstate real opposition. In a scenario where several signers initially reject a proposal for a transient/fixable reason and then re-evaluate and sign it (a supported, non-malicious flow), their stale rejection weight remains in the tally alongside their legitimate approval weight elsewhere. This can push `total_weight_rejected` past the blocking-minority threshold even though the *current* set of dissenting signers is smaller, causing the coordinator to declare the proposal rejected (`SignersRejected`) when in truth enough weight has, or would, approve it. This wedges block production for that proposal despite sufficient real support — a liveness impact (the miner is prevented from reaching a valid signed block that the actual current signer state would otherwise allow), reachable without requiring a majority of colluding signers, only a set of signers exhibiting the normal reject-then-reconsider pattern.

### Likelihood Explanation
Medium: this doesn't require a malicious majority or any key compromise — it requires only the codebase's own supported "signer rejects, then reconsiders and signs the same block" flow to occur for enough signers (or repeatedly for the same signer across retries) that stale rejected weight crosses the 30% blocking threshold while genuine current rejection is lower. The re-evaluation flow exists specifically to let signers reconsider rejections, making this a realistic operational path rather than a purely theoretical one.

### Recommendation
When processing an `Accepted` message for a slot that is already present in `responded_signers` due to a prior rejection, subtract that signer's weight from `total_weight_rejected` (and, symmetrically, when handling a rejection for a slot already counted in `total_weight_approved`, either reject the transition or subtract from `total_weight_approved`), so at most one of the two aggregates ever holds a given signer's weight at a time — mirroring the `min(deltaOiUsd, positionSizeUsd - expiredOiUsd)` style fix: net out the weight against whatever bucket previously counted it before adding it to the new one.

### Proof of Concept
1. Node proposes block B (`block_sighash` = H); `insert_block` initializes `BlockStatus` with all counters at 0. [6](#0-5) 
2. Signer S (weight w) sends `BlockResponse::Rejected` for H. `responded_signers.insert(S)` succeeds → `total_weight_rejected += w`.
3. Signer S later re-evaluates and signs the identical block B (same H), broadcasting `BlockResponse::Accepted`. `gathered_signatures.contains_key(S)` is false (only touched by the accept path) → `total_weight_approved += w`. `S` is now counted in both `total_weight_rejected` and `total_weight_approved`.
4. If other signers' combined rejection weight plus S's stale `w` crosses `total_weight - weight_threshold`, `store_and_process_block_signature`'s node-side counterpart (`SignCoordinator::run`) returns `Err(NakamotoNodeError::SignersRejected)` for block B even though S (and possibly enough other weight) has since approved it, blocking mining of an otherwise valid, sufficiently-supported proposal.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-519)
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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-540)
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
```
