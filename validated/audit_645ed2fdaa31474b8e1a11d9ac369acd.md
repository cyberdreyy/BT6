## Analog Found

### Title
Stale rejection weight is never cleared when a signer reconsiders and accepts, letting `StackerDBListener`/`SignerCoordinator` double-count a signer's weight and trigger a false `SignersRejected` — ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
The Sherlock report's root cause is a "check-before-increase" ordering bug: an accumulator is compared to a threshold *before* it is updated with funds that would actually clear the threshold, so real available weight is not accounted for at decision time. The analogous flaw here is on the opposite side of the same class of bug: the node's per-block weight tallies in `StackerDBListener` are incremented on every new vote but are **never decremented/cleared** when a signer's vote changes from `Rejected` to `Accepted` for the same block, so a stale rejection weight is retained and combined with fresh rejection weight from other signers to cross a threshold it should not cross.

### Finding Description
`BlockStatus` tracks, per block signature hash, `responded_signers`, `gathered_signatures`, `total_weight_approved`, and `total_weight_rejected` [1](#0-0) .

When a `BlockAccepted` message arrives, the code adds to `total_weight_approved` only if the signer's slot is not already present in `gathered_signatures`, and unconditionally inserts the slot into `responded_signers`: [2](#0-1) 

When a `BlockResponse::Rejected` message arrives, the code adds to `total_weight_rejected` gated only on `responded_signers.insert(slot_id)` returning `true` (i.e., first time this signer has responded at all): [3](#0-2) 

Because `responded_signers` is shared and never partitioned by vote kind, and because there is no code path that *subtracts* a signer's weight from `total_weight_rejected` when that same signer later sends an `Accepted` message, the following sequence is possible:

1. Signer S sends a `Rejected` for block B. `responded_signers.insert(slot)` → `true`. `total_weight_rejected += weight(S)`.
2. The signer-side state machine legitimately allows reconsidering a previously rejected block ("`should_reevaluate_reject_reason`" flow documented in `docs/signer-flows.md` section 3) and later signs/accepts the same block, broadcasting `BlockAccepted`.
3. On the node side, the `Accepted` handler checks `!gathered_signatures.contains_key(&slot)`, which is still `true` (S was never added to `gathered_signatures` by the rejection path), so `total_weight_approved += weight(S)` is applied.
4. `total_weight_rejected` is **never reduced**; S's earlier rejection weight remains permanently baked into the block's rejection tally even though S now supports the block.

This breaks the aggregated-weight-vs-verified-votes equality: the tally no longer reflects the actual, current set of distinct signers rejecting the block. `SignerCoordinator::get_block_status` uses this stale, monotonically-non-decreasing `total_weight_rejected` to decide whether the blocking minority (`>30%`) has been reached, checked *before* the acceptance-threshold branch: [4](#0-3) 

If enough *other* signers subsequently reject, their genuine rejection weight combines with S's stale, no-longer-valid rejection weight to cross the blocking-minority threshold, even though S has since switched to supporting the block. The coordinator then returns `NakamotoNodeError::SignersRejected` and abandons a block that a correct, up-to-date tally would not have blocked.

### Impact Explanation
This is a liveness/aggregation-integrity bug reachable by a single signer's normal, permitted vote reconsideration (reject → accept) combined with any other signer's rejection — no majority collusion is required. It causes the miner-side coordinator to act on a stale/incorrect aggregated weight, wrongly concluding a blocking rejection majority exists and abandoning a proposal that could otherwise have reached the real 70% approval threshold. This matches the "acting on stale ... threshold" class of High-severity node-side aggregation flaws: the equality between "verified, current accept/reject weight" and "weight the coordinator decides on" is broken.

### Likelihood Explanation
Reject→accept reconsideration is an explicitly supported and documented signer behavior (`should_reevaluate_reject_reason`), so the triggering sequence (one signer flips vote, then any other signer legitimately rejects) is a normal, low-cost occurrence rather than a contrived edge case, and requires only one flipping signer plus any additional genuine rejector — well under a majority.

### Recommendation
When a signer's vote for a given block transitions from `Rejected` to `Accepted` (or vice versa), the `BlockStatus` tallies must be reconciled instead of purely additive: either subtract the signer's weight from `total_weight_rejected` when moving to `total_weight_approved` (and symmetrically the other direction), or track each signer's current vote kind and recompute `total_weight_approved`/`total_weight_rejected` from the current set of votes rather than accumulating unconditionally per message.

### Proof of Concept
Weight distribution: total=100, `weight_threshold`=70 (blocking minority = 30).
1. Signer S (weight 20) sends `Rejected` for block B → `total_weight_rejected` = 20 (`responded_signers` = {S}).
2. Signer S reconsiders and sends `Accepted` for B → passes the `!gathered_signatures.contains_key` check, `total_weight_approved` = 20; `total_weight_rejected` remains 20 (never decremented).
3. Signer T (weight 15, genuinely rejecting) sends `Rejected` for B → `responded_signers.insert(T)` succeeds → `total_weight_rejected` = 20 (stale, from S) + 15 (T) = 35.
4. `signer_coordinator::get_block_status` evaluates `total_weight_rejected.saturating_add(weight_threshold) > total_weight` → `35 + 70 = 105 > 100` → `true`, returning `Err(NakamotoNodeError::SignersRejected { .. })`, even though the only signer currently rejecting is T (15%, well under the 30% blocking minority), because S's stale rejection weight was never removed. [5](#0-4) [6](#0-5)

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-518)
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

                        if block.total_weight_approved >= self.weight_threshold {
                            // Signal to anyone waiting on this block that we have enough signatures
                            cvar.notify_all();
                        }

                        // Update the idle timestamp for this signer
                        self.update_idle_timestamp(
                            signer_pubkey.clone(),
                            tenure_extend_timestamp,
                            signer_entry.weight,
                        );

                        // Update the read-count timestamp for this signer
                        self.update_read_count_timestamp(
                            signer_pubkey,
                            read_count_extend_timestamp,
                            signer_entry.weight,
                        );
                    }
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
