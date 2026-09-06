### Title
Stale rejection weight is never retracted when a signer later accepts the same block, letting a superseded rejection block a validly-signed proposal - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener::run` tallies signer `BlockResponse` gossip into two independent counters, `total_weight_approved` and `total_weight_rejected`, guarded by two *different* de-duplication sets. The `Rejected` branch gates on `block.responded_signers.insert(slot_id)` [1](#0-0) , while the `Accepted` branch gates on the unrelated `block.gathered_signatures.contains_key(&slot_id)` [2](#0-1) . A signer who first rejects a block (weight added to `total_weight_rejected`, slot recorded in `responded_signers`) and later legitimately accepts the same `signer_signature_hash` (e.g. after a transient `ConnectivityIssues`/re-evaluation causes the signer to flip from reject to accept) has its weight added to `total_weight_approved` as well, with nothing ever subtracted from `total_weight_rejected`. The stale rejection weight is retained forever for that block.

### Finding Description
The coordinator's rejection/acceptance verdict in `SignerCoordinator::get_block_status` checks the reject condition before the accept condition every loop iteration:
```
total_weight_rejected + weight_threshold > total_weight  => SignersRejected
else if total_weight_approved >= weight_threshold        => Ok(signatures)
``` [3](#0-2) 

Because `total_weight_rejected` can contain weight from a signer who has since accepted the very same block, `total_weight_rejected + total_weight_approved` is no longer bounded by `total_weight`: a signer's weight can be double-booked, once as "rejected" (stuck forever) and once as "approved". Since the reject branch is evaluated first, a block that has in fact reached the 70% approval threshold can still be reported as `SignersRejected` purely because of leftover, superseded rejection weight from a signer who has already reversed their vote. This is exactly the "rejection recounted"-class miscount the equivalence in the prompt calls out — here manifesting as a stale rejection surviving past the point it should have been superseded by that signer's acceptance, corrupting the aggregated-weight vs. verified-accepts equality the coordinator relies on.

The rejected branch's own weight-tracking is internally consistent (it never re-adds a signer already in `responded_signers`, whether that signer had previously rejected or accepted) [4](#0-3) ; the asymmetry is specific to the accepted branch never checking or clearing the signer's prior rejection.

### Impact Explanation
This breaks the aggregated-weight vs. verified-accepts equality that `get_block_status` relies on to decide whether a block was actually signed by ≥70% weight. A validly, fully-signed block (one that legitimately crosses the signing threshold) can be spuriously classified as `SignersRejected` due to phantom, un-retracted rejection weight from a signer who has since signed. This triggers `NakamotoNodeError::SignersRejected`, causing the miner to discard the block, potentially permanently/temporarily exclude transactions based on the stale per-txid rejection tallies (`permanently_excluded_txids`/`temporarily_excluded_txids`) [5](#0-4) , and stall block production on a block that had, in fact, already gathered enough signatures — a liveness wedge on the mining/coordination path.

### Likelihood Explanation
No majority of signers, no other signer's private key, and no auth token is required: this can be triggered by the natural (and expected-to-be-supported, per the documented reject-reason-reconsideration flow in `docs/signer-flows.md`) behavior of a single signer whose response to the same `signer_signature_hash` transitions from `Rejected` to `Accepted` — for example after `ConnectivityIssues`, a stale-chainstate rejection reason that is later resolved, or normal reconsideration logic in `handle_block_response`/`determine_response` [6](#0-5) . Any signer (even a low-weight one) can also deliberately exploit this ordering to keep its rejection weight "banked" against a block indefinitely while additionally signing it.

### Recommendation
Track a single per-slot "last response" state (accept vs reject) for each block instead of two independently-gated counters, and derive `total_weight_approved`/`total_weight_rejected` from that authoritative map so that a later response of one kind retracts the signer's weight from the other bucket. Concretely, when handling `Accepted`, check `responded_signers` (not `gathered_signatures`) to decide whether to add weight, and if the signer was previously counted in `total_weight_rejected` (and vice versa for the `Rejected` branch), subtract the stale weight before adding it to the new bucket.

### Proof of Concept
1. Node proposes block B with `signer_signature_hash = H`.
2. Signer S (weight `w`) sends `BlockResponse::Rejected(H, ConnectivityIssues)`. `stackerdb_listener` records `responded_signers += {S}`, `total_weight_rejected += w` [1](#0-0) .
3. S re-evaluates (transient issue resolved, or S is malicious and simply changes its mind) and sends `BlockResponse::Accepted(H, sig)` for the same `H`.
4. In the `Accepted` branch, `gathered_signatures` does not yet contain S's slot, so `total_weight_approved += w` is applied unconditionally, with no corresponding decrement of `total_weight_rejected` [7](#0-6) .
5. Now `total_weight_approved + total_weight_rejected > total_weight` is possible; if enough other signers legitimately accept to cross `weight_threshold`, the coordinator's loop in `get_block_status` still evaluates the reject branch first: `total_weight_rejected.saturating_add(weight_threshold) > total_weight` can be true purely from S's stale rejection weight plus other signers' genuine rejections/late responses, returning `Err(SignersRejected)` even though the block actually has ≥70% real signature weight recorded in `gathered_signatures`.

### Citations

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

**File:** stacks-signer/src/v0/signer.rs (L1782-1797)
```rust
    /// Handle block response messages from a signer
    fn handle_block_response(
        &mut self,
        stacks_client: &StacksClient,
        block_response: &BlockResponse,
        sortition_state: &mut Option<SortitionsView>,
    ) {
        match block_response {
            BlockResponse::Accepted(accepted) => {
                self.handle_block_signature(stacks_client, sortition_state, accepted);
            }
            BlockResponse::Rejected(block_rejection) => {
                self.handle_block_rejection(block_rejection, sortition_state);
            }
        };
    }
```
