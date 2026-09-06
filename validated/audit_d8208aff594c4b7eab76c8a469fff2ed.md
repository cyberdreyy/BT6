### Title
Signer vote-weight double counting: a signer's stale rejection weight is never cleared when that signer later accepts, corrupting the approved/rejected tally invariant - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListener` tallies `total_weight_approved` and `total_weight_rejected` per proposed block by de-duplicating on two *different* sets depending on the message kind: the `Accepted` arm de-dupes against `gathered_signatures` (a `BTreeMap<slot_id, signature>`), while the `Rejected` arm de-dupes against `responded_signers` (a `HashSet<slot_id>`) that is shared by both arms. Because the `Accepted` arm never checks `responded_signers` (only `gathered_signatures`) before adding weight, a signer who first rejects a proposal and later reconsiders and accepts it has their weight added to `total_weight_approved` on the accept, while their earlier weight contribution to `total_weight_rejected` is never removed. The same signer's weight ends up counted in both buckets simultaneously, exactly mirroring the reported bug class: a fee/adjustment computed for one accounting bucket (the burn amount) was never reflected consistently in the other bucket (the fee transferred to treasury), producing two sides of state that should be equal but aren't.

### Finding Description
`BlockStatus` (stacks-node/src/nakamoto_node/stackerdb_listener.rs:70-82) holds `responded_signers: HashSet<u32>`, `gathered_signatures: BTreeMap<u32, MessageSignature>`, `total_weight_approved`, and `total_weight_rejected`, all mutated by the same message-processing loop.

On `BlockResponse::Accepted`, weight is added only if the slot is not already present in `gathered_signatures`: [1](#0-0) 

On `BlockResponse::Rejected`, weight is added only if the slot is not already present in `responded_signers` (the same set that `Accepted` also populates): [2](#0-1) 

This creates an asymmetric guard:
- Accept → later Reject: correctly suppressed, because `responded_signers` already contains the slot from the earlier accept, so the reject's `insert` returns `false` and `total_weight_rejected` is not incremented.
- Reject → later Accept: **not** suppressed, because the accept only checks `gathered_signatures` (which doesn't yet contain the slot), so the accept succeeds and adds the signer's weight to `total_weight_approved`, while their earlier weight remains stuck in `total_weight_rejected` forever (nothing ever decrements it).

The invariant that should hold — each signer's weight counts toward at most one of `total_weight_approved`/`total_weight_rejected` at any time — is broken. This is the direct analog of `LiquidationLogic@_burnCollateralTokens`: a quantity computed for one side of an accounting relationship (`liquidationProtocolFeeAmount`) is applied to one bucket (the treasury transfer) but not consistently reflected in the other bucket (the collateral share burn), producing two states that silently diverge from an expected equality.

### Impact Explanation
`SignerCoordinator::get_block_status` (stacks-node/src/nakamoto_node/signer_coordinator.rs) makes the miner's finalization decision by checking rejection first, then approval: [3](#0-2) 

Because the reject-then-accept case leaves stale weight permanently stuck in `total_weight_rejected`, that phantom weight can push `total_weight_rejected.saturating_add(weight_threshold) > total_weight` (the blocking-minority condition) even after the same signers' current votes would otherwise legitimately reach the 70% approval threshold. Since the rejection branch is evaluated first and returns `NakamotoNodeError::SignersRejected` unconditionally once crossed, a perfectly valid, sufficiently-approved block can be permanently treated as globally rejected by the coordinator/miner. This is a liveness wedge: the node stops making progress on an otherwise valid block, matching the High-severity "wedged into never [accepting] valid blocks" class described in scope, achievable by ordinary vote reconsideration (no majority collusion needed — a single signer flipping its vote is sufficient to leave permanent stale weight).

### Likelihood Explanation
Vote reconsideration (reject → accept) is an expected, normal occurrence in this protocol: the signer-side state machine explicitly allows `LocallyRejected → LocallyAccepted` transitions on re-evaluation (see `BlockInfo::check_state`, stacks-signer/src/signerdb.rs:313-329, and `should_reevaluate_block`/`should_reevaluate_reject_reason` flows). Any signer that rejects a proposal (e.g., transient validation failure, stale chain tip) and later re-signs it upon reconsideration will trigger this stale-weight retention on the node/coordinator side, with no attacker or majority collusion required — it can happen from perfectly honest signer behavior.

### Recommendation
Make the de-duplication and weight-adjustment logic symmetric and consistent between the two arms:
- Before adding weight in the `Accepted` arm, also check (or clear) any pre-existing `responded_signers`/rejection-weight contribution from that slot, decrementing `total_weight_rejected` if the earlier response was a rejection.
- Alternatively, unify the bookkeeping into a single per-slot "current vote" (accept/reject) map, recomputing `total_weight_approved`/`total_weight_rejected` from that map's current contents rather than incrementally with independent, only-partially-shared dedupe sets, so a slot's weight can never simultaneously count in both totals.

### Proof of Concept
1. Miner proposes block `B`. Signer `S` (weight `w`) sends `BlockResponse::Rejected` for `B`. `StackerDBListener` processes it: `responded_signers.insert(S.slot)` succeeds → `total_weight_rejected += w` (stacks-node/src/nakamoto_node/stackerdb_listener.rs:515-518).
2. `S` re-evaluates and later sends `BlockResponse::Accepted` for the same `B` (e.g., after a retried validation). `StackerDBListener` processes it: `gathered_signatures.contains_key(S.slot)` is `false` (never populated by the reject path) → `total_weight_approved += w` (stacks-node/src/nakamoto_node/stackerdb_listener.rs:443-446).
3. Now `S`'s weight `w` is counted in both `total_weight_approved` and `total_weight_rejected` simultaneously; nothing in the codebase ever subtracts `w` back out of `total_weight_rejected`.
4. If cumulative stale rejection weight (from any signers who behaved this way) crosses `total_weight - weight_threshold`, `SignerCoordinator::get_block_status` (stacks-node/src/nakamoto_node/signer_coordinator.rs:509-519) returns `NakamotoNodeError::SignersRejected` for block `B` even though current signer support (per live votes) is sufficient to reach the 70% approval threshold — wedging the miner from finalizing a valid block.

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
