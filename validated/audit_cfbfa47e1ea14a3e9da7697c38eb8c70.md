### Title
Node-side stackerdb listener double-counts a signer's weight in both the approval and rejection tallies when a signer flips its vote - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener::run` tracks per-block consensus with a shared `BlockStatus` struct containing `gathered_signatures`, `responded_signers`, `total_weight_approved`, and `total_weight_rejected`. The guard that prevents double-counting an `Accepted` message uses the `gathered_signatures` map, while the guard for a `Rejected` message uses the shared `responded_signers` set. These two independent guards are never reconciled against each other, so a signer that first rejects a block and later legitimately switches to accepting the same block (a flow explicitly supported by the signer protocol's reconsideration logic) has its weight permanently retained in `total_weight_rejected` *and* newly added to `total_weight_approved`.

### Finding Description
In the `BlockResponse::Rejected` branch, weight is added to `total_weight_rejected` guarded only by `block.responded_signers.insert(slot_id)` returning `true` (i.e., first time this slot has responded at all): [1](#0-0) 

In the `BlockResponse::Accepted` branch, weight is added to `total_weight_approved` guarded only by `block.gathered_signatures.contains_key(&slot_id)`, an entirely separate map, and there is no code that removes or adjusts `total_weight_rejected` when the same slot subsequently accepts: [2](#0-1) 

Because `responded_signers` is inserted unconditionally by the Accepted branch too, but the Accepted-side weight-guard checks `gathered_signatures` instead of `responded_signers`, the two branches are not mutually exclusive in the "reject-then-accept" order: a slot that rejects first (added to `responded_signers`, weight counted in `total_weight_rejected`) can later accept (weight added to `total_weight_approved`, since `gathered_signatures` did not yet contain that slot) without the earlier rejected weight ever being retracted.

This reject→accept sequence is not hypothetical inside this codebase: the v0 signer explicitly supports reconsidering a prior rejection and switching to acceptance for the same block via `should_reevaluate_reject_reason`/`should_reevaluate_block`, which can re-drive `determine_response`/`handle_block_pre_commit` and ultimately produce a fresh `Accepted` broadcast after a prior `Rejected` broadcast for the identical `signer_signature_hash`: [3](#0-2) 

Notably, the signer's own local database (`signerdb.rs`) *does* handle this transition correctly by deleting any prior rejection row when a signature is recorded: [4](#0-3) 
but the node-side `StackerDBListener`'s in-memory `BlockStatus` tally has no equivalent reconciliation, so the phantom rejected weight is never cleared.

### Impact Explanation
`SignerCoordinator::wait_for_signatures` (or equivalent polling loop) makes its accept/reject decision purely from these two counters: [5](#0-4) 
Because `total_weight_rejected` is never decremented once a signer switches to acceptance, that stale rejected weight remains available to combine with genuinely-rejecting signers, and can push `total_weight_rejected + weight_threshold > total_weight` even though the signer whose weight is being (still) counted as a rejector has actually endorsed the block. This lets the miner incorrectly treat a block as globally rejected (`NakamotoNodeError::SignersRejected`) despite sufficient real signer support — a liveness wedge on block production. It also breaks the invariant that `total_weight_approved` and `total_weight_rejected` represent disjoint, mutually-exclusive signer weight (the sum of the two counters can now exceed `total_weight` for a single reward cycle's signer set), i.e., it is a miscounted response scenario where a signer's most recent response is not correctly reflected in the aggregated tally.

### Likelihood Explanation
Only a single ordinary signer (one slot) needs to send a `Rejected` message for a block and then, following the protocol's own supported reconsideration path, a subsequent `Accepted` for the same `signer_signature_hash`. No majority collusion, no key compromise, and no auth-token/local access is required — an honest signer that legitimately reconsiders its vote (or a signer deliberately crafting this exact message order) triggers the stale double count.

### Recommendation
In `StackerDBListener`'s message-processing loop, make the weight bookkeeping for `Accepted` and `Rejected` mutually exclusive per slot: before adding rejected weight, check (and if necessary clear) any prior `gathered_signatures` entry for that slot and correspondingly decrement `total_weight_approved`; symmetrically, when an `Accepted` message arrives for a slot already present in the rejected tally, decrement `total_weight_rejected` for that slot's weight before adding it to `total_weight_approved`. Track weight per-slot (e.g., a `HashMap<slot_id, Vote>` where `Vote` is `Accepted` or `Rejected`) and recompute the two aggregate totals from that map rather than maintaining two independently-guarded running sums.

### Proof of Concept
1. Node signer set includes signer `S` at slot `k` with weight `w`.
2. Miner proposes block `B` with hash `H`.
3. `S` rejects `B` (e.g., transient `ConnectivityIssues`/re-evaluable reason). `StackerDBListener` records: `responded_signers = {k}`, `total_weight_rejected += w`.
4. `S`'s local signer, per `should_reevaluate_reject_reason`, later reconsiders and accepts `B` for the same `signer_signature_hash`, broadcasting `BlockResponse::Accepted`.
5. `StackerDBListener` processes the `Accepted` message: `gathered_signatures` does not yet contain `k`, so `total_weight_approved += w` is applied. `total_weight_rejected` is left unchanged at its prior value including `w`.
6. Now `total_weight_approved + total_weight_rejected` exceeds what a single, consistent vote from `S` should contribute; further, if other genuinely rejecting signers push `total_weight_rejected` such that `total_weight_rejected + weight_threshold > total_weight`, `SignerCoordinator` will abort the block as rejected even though `S`, whose weight is inflating the rejected tally, actually endorsed it in its latest response.

I was not able to execute this in a live test harness (no code execution access in this mode); the analysis is based on static review of `stacks-node/src/nakamoto_node/stackerdb_listener.rs` and the corresponding signer-side reconsideration logic in `stacks-signer/src/v0/signer.rs`. Confirming the exact frequency/practical exploitability under production timing (e.g., how often a reconsideration reject→accept flip actually occurs) would benefit from a live/integration test run.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-518)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-signer/src/v0/signer.rs (L1505-1532)
```rust
        if !should_reevaluate_reject_reason(block_info) {
            if block_info.state == BlockState::PreCommitted {
                // We validated this block but haven't signed it. Signing requires the
                // pre-commit threshold and the conflict checks in `handle_block_pre_commit`.
                // Re-broadcast our pre-commit and re-run that evaluation instead of
                // responding with a signature directly, so a re-proposed block can't
                // bypass those checks.
                info!(
                    "{self}: received a block proposal for a block we have pre-committed to but not signed. Re-evaluating the pre-commit.";
                    "signer_signature_hash" => %signer_signature_hash,
                    "block_id" => %block_info.block.block_id(),
                    "block_height" => block_info.block.header.chain_length,
                    "burn_height" => block_proposal.burn_height,
                    "consensus_hash" => %block_info.block.header.consensus_hash
                );
                self.send_block_pre_commit(signer_signature_hash.clone());
                let address = self.stacks_address.clone();
                self.handle_block_pre_commit(
                    stacks_client,
                    sortition_state,
                    &address,
                    &signer_signature_hash,
                );
                return false;
            }
            if let Some(block_response) = self.determine_response(block_info) {
                self.send_block_response(&block_info.block, block_response);
                return false;
```

**File:** stacks-signer/src/signerdb.rs (L1877-1880)
```rust
        // Remove any block rejection entry for this signer and block hash
        let del_qry = "DELETE FROM block_rejection_signer_addrs WHERE signer_signature_hash = ?1 AND signer_addr = ?2";
        let del_args = params![block_sighash, signer_addr.to_string()];
        self.db.execute(del_qry, del_args)?;
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
