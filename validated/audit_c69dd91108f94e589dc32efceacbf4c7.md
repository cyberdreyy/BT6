### Title
Signer weight double-counted across mutually-exclusive accept/reject tallies in the node's `StackerDBListener` - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
The miner-side `StackerDBListener` tallies block-acceptance and block-rejection weight for a proposed block into two counters, `total_weight_approved` and `total_weight_rejected`, guarded by two *different* de-duplication sets (`gathered_signatures` for accepts, `responded_signers` for the "already responded" check). Because a single signer can emit both a `BlockResponse::Accepted` and a `BlockResponse::Rejected` message for the same `signer_signature_hash` (e.g. by flip-flopping, or a byzantine/faulty signer intentionally sending both), that signer's weight can end up added to *both* tallies, breaking the invariant that `total_weight_approved` and `total_weight_rejected` partition the signer set's weight.

### Finding Description
In the message-processing loop of `stackerdb_listener.rs`:

- The `Accepted` arm only skips re-adding weight if the slot is already present in `gathered_signatures`: [1](#0-0) 
- The `Rejected` arm instead gates its weight addition on `responded_signers.insert(slot_id)`, a *different* set: [2](#0-1) 

The accept path inserts into `responded_signers` too (line 465), but it never checks it before adding weight, and the reject path never checks `gathered_signatures`. Consequently, if a signer's `Reject` message for a block arrives first (weight added to `total_weight_rejected`, slot recorded in `responded_signers`), and the same signer later broadcasts an `Accept` for the same block (a valid, freshly-verified signature over the same `signer_signature_hash`), the accept arm's guard (`!block.gathered_signatures.contains_key(&slot_id)`) is still satisfied — the slot isn't in `gathered_signatures` yet — so the code proceeds to add `signer_entry.weight` to `total_weight_approved` as well. The signer's weight is now counted in both `total_weight_approved` and `total_weight_rejected` simultaneously.

This breaks the "aggregated-weight vs verified-accepts equality" the coordinator relies on in `signer_coordinator.rs`, which drives the miner's accept/reject decision purely from these two weight counters: [3](#0-2) 

### Impact Explanation
The miner's coordinator loop treats `total_weight_rejected + weight_threshold > total_weight` as a rejection verdict and `total_weight_approved >= weight_threshold` as an acceptance verdict. Because these two counters are not kept mutually exclusive, a signer's weight can leak into the wrong bucket. This does not let a forged signature enter the assembled signature set (the block-accept branch still only aggregates verified signatures keyed by `gathered_signatures`), but it corrupts the weight-threshold bookkeeping the coordinator relies on to decide whether the network reached 70% approval or a blocking >30% rejection — i.e. an accounting bug straddling the "aggregated-weight vs verified-accepts" equality that the task explicitly calls out. Depending on the exact weights near the threshold boundary, this can cause a premature/incorrect accept-vs-reject verdict at the miner, which is a liveness/consensus-accounting risk rather than a direct block-signing safety break by a signer.

### Likelihood Explanation
Requires only a single signer (not a majority) to send both an `Accepted` and a `Rejected` message for the same block over StackerDB — something that can legitimately happen on retries/reconnects, or be intentionally triggered by one faulty/byzantine signer, satisfying the "one-slot miner (plus gossip)" trigger bar without needing another signer's key or majority collusion.

### Recommendation
Use a single per-slot state (e.g. one `HashMap<u32, Vote>` recording exactly one of {Accepted, Rejected} per `slot_id`) rather than two independently-guarded sets, and only add weight to a counter once that slot's final vote is recorded, removing/replacing the weight from the opposite counter (or refusing to switch votes) if a signer's vote changes for the same block.

### Proof of Concept
1. Miner proposes block `B` with `signer_signature_hash = H`.
2. Signer `S` (weight `w`) sends `BlockResponse::Rejected{ signer_signature_hash: H, ... }`. `stackerdb_listener` records `responded_signers.insert(S)` and `total_weight_rejected += w` per lines 515-518.
3. Signer `S` subsequently (re)sends `BlockResponse::Accepted{ signer_signature_hash: H, signature: valid_sig, ... }` for the same block (e.g. after re-evaluating, or maliciously). Because `gathered_signatures` does not yet contain `S`'s slot, the guard at line 443 passes and `total_weight_approved += w` is executed as well (lines 443-465).
4. Both `total_weight_approved` and `total_weight_rejected` now include `w` for the same signer, inflating the sum of the two tallies beyond `total_weight` and corrupting the threshold comparisons consumed in `signer_coordinator.rs` lines 509-545. [4](#0-3)

### Citations

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
