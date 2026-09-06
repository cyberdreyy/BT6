### Title
Reject-then-accept flip lets a signer's weight be double-counted in both `total_weight_rejected` and `total_weight_approved` - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
The node-side StackerDB listener that the mining coordinator uses to tally signer responses guards duplicate *acceptances* with one set (`gathered_signatures`) and duplicate *rejections* with a different set (`responded_signers`), but only the reject path checks `responded_signers` before adding weight. A signer that first rejects and later reconsiders and accepts (an explicitly supported, non-malicious protocol flow) gets its weight added to `total_weight_rejected` *and* later to `total_weight_approved`, permanently. This breaks the implicit invariant that a signer's weight can only ever sit in one bucket, which `signer_coordinator.rs` depends on when deciding whether to treat a block as rejected or accepted.

### Finding Description
`stackerdb_listener.rs` maintains a `BlockStatus`-like structure per proposed block with `gathered_signatures`, `responded_signers`, `total_weight_approved`, and `total_weight_rejected`.

- On `BlockResponse::Accepted`, weight is added only if `!block.gathered_signatures.contains_key(&slot_id)`: [1](#0-0) 

- On `BlockResponse::Rejected`, weight is added only if `block.responded_signers.insert(slot_id)` (i.e., first response ever from that slot): [2](#0-1) 

Because the Accepted branch never consults `responded_signers`, and the Rejected branch never consults `gathered_signatures`, the two tallies are not mutually exclusive:

1. Signer S rejects first: `responded_signers.insert(slot)` succeeds → `total_weight_rejected += w`.
2. Signer S later reconsiders and accepts the same (or a re-proposed) block: `gathered_signatures.contains_key(slot)` is still false (rejection never touched it), so `total_weight_approved += w` as well, and `gathered_signatures`/`responded_signers` get updated again (no-ops for the set).

The result: the same signer's weight `w` is now counted in *both* `total_weight_rejected` and `total_weight_approved` simultaneously and forever, so `total_weight_approved + total_weight_rejected` can exceed `self.total_weight`. Note the reverse order (accept-then-reject) is correctly guarded, since `responded_signers.insert` on the reject path is a no-op once already set by the accept path - the asymmetry is one-directional but that direction is exactly the one the protocol encourages: "For some rejection reasons, a signer will reconsider a block proposal that it previously rejected" (see `stacks-signer/CHANGELOG.md`).

The coordinator that consumes this state checks the rejection condition *before* the approval condition: [3](#0-2) 

so a stale rejection weight that the signer has since retracted by signing can still push `total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight`, causing the miner to conclude the block was rejected by a blocking minority even though the real, current weight of unresolved rejecters is smaller than that threshold (the flipped signer's weight is simultaneously present in the approve bucket, meaning it should not still count against the block).

### Impact Explanation
This is a liveness wedge in the miner/coordinator's view of consensus, not a majority-controlled attack: any single signer who legitimately reconsiders (a normal, documented signer behavior) creates a permanent inconsistency between the two weight tallies used by `signer_coordinator.rs` to decide `SignersRejected` vs. accepted. Because the rejection branch is checked first in `get_block_status`, a block that has in fact accumulated enough real approving weight can still be reported to the miner as rejected (`NakamotoNodeError::SignersRejected`), causing the miner to discard/replace transactions or the whole proposal unnecessarily. This matches the "wedge the state machine"/aggregated-weight-vs-verified-accepts class of bug from the analog report (Mochi's `ReferralFeePoolV0` failing to reduce/zero-out the balance on claim), where a tally is never properly retracted when the underlying state changes, letting stale weight be double-counted.

### Likelihood Explanation
High likelihood of occurrence in normal operation: the codebase's own CHANGELOG documents that signers are expected to reconsider certain rejections and later sign, and this exact reject→accept ordering is the one direction not protected by the de-dup guard. No majority coalition, no compromised keys, and no exploitation of `auth_token`/local access is required - a single signer following the intended "reconsider and sign" flow triggers it, and it is reachable purely through message gossip over StackerDB observed by the coordinator/listener.

### Recommendation
Use a single shared guard (e.g., check and update `responded_signers` in both the Accepted and Rejected branches, or track a per-slot `enum { Approved, Rejected }` and only adjust weight when the value changes, decrementing the old bucket and incrementing the new one) so that a signer's weight is moved between `total_weight_approved` and `total_weight_rejected` rather than accumulating in both. This mirrors the fix already applied on the persistent signer-side bookkeeping in `stacks-signer/src/signerdb.rs`'s `add_block_rejection_signer_addr`, which explicitly refuses to record a rejection once a signature exists for that signer/block: [4](#0-3) 
The same mutual-exclusion guarantee needs to be applied to the ephemeral, node-side tally in `stackerdb_listener.rs`.

### Proof of Concept
1. Node proposes block B; signer S (weight `w`) sends `BlockResponse::Rejected` for B. `stackerdb_listener.rs` records `responded_signers.insert(S)` and `total_weight_rejected += w`. [5](#0-4) 
2. S subsequently reconsiders (per the documented "reconsider a block proposal that it previously rejected" behavior) and sends `BlockResponse::Accepted` for the same `signer_signature_hash`. Since `gathered_signatures` has no entry for S's slot, the Accepted branch adds weight again: `total_weight_approved += w`. [1](#0-0) 
3. `total_weight_rejected` and `total_weight_approved` now both include `w`; their sum can exceed `self.total_weight`.
4. In `signer_coordinator.rs::get_block_status`, the rejection-threshold check (`total_weight_rejected.saturating_add(weight_threshold) > total_weight`) is evaluated before the approval check, so this stale, un-retracted rejection weight can cause the coordinator to return `NakamotoNodeError::SignersRejected` for a block that, net of S's actual current vote, may already have enough approving weight. [3](#0-2)

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L486-519)
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

**File:** stacks-signer/src/signerdb.rs (L1922-1940)
```rust
    /// Record an observed block rejection_signature
    pub fn add_block_rejection_signer_addr(
        &self,
        block_sighash: &Sha512Trunc256Sum,
        addr: &StacksAddress,
        reject_reason: RejectReasonPrefix,
    ) -> Result<bool, DBError> {
        // If this signer/block already has a signature, do not allow a rejection
        let sig_qry = "SELECT EXISTS(SELECT 1 FROM block_signatures WHERE signer_signature_hash = ?1 AND signer_addr = ?2)";
        let sig_args = params![block_sighash, addr.to_string()];
        let exists = self.db.query_row(sig_qry, sig_args, |row| row.get(0))?;
        if exists {
            warn!("Cannot add block rejection because a signature already exists.";
                "signer_signature_hash" => %block_sighash,
                "signer_address" => %addr,
                "reject_reason" => ?reject_reason
            );
            return Ok(false);
        }
```
