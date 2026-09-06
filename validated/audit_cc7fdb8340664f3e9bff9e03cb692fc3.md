### Title
Signer vote flip (Reject → Accept) is double-counted across both weight tallies in the miner's `StackerDBListener`, letting a stale rejection wedge a block that has legitimately reached the signing threshold - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener::run_loop` (the coordinator's per-block vote tally, analogous to the C4 report's per-pool reward tracking against a shared budget) maintains two independent weight counters per proposed block, `total_weight_approved` and `total_weight_rejected`, inside a single shared `BlockStatus`. A signer's weight is supposed to be attributed to whichever verdict it currently holds. Instead, the `Accepted` branch adds a signer's weight to `total_weight_approved` whenever that signer's slot is missing from `gathered_signatures`, without checking whether that same signer already contributed weight to `total_weight_rejected` earlier. The protocol explicitly allows a signer to reject a block and later sign the same block hash once the rejection reason "has since gone away" (`docs/signer-flows.md` §0, §6), so this vote-flip is a normal, expected, single-signer/gossip-only event, not an attacker-only edge case.

### Finding Description
The relevant tally logic is: [1](#0-0) 

which increments `total_weight_approved` guarded only by `!block.gathered_signatures.contains_key(&slot_id)`, and [2](#0-1) 

which increments `total_weight_rejected` guarded only by `block.responded_signers.insert(slot_id)` returning `true` (i.e., first response ever seen from that slot).

Sequence that breaks the invariant:
1. Signer `S` (weight `w`) sends `Rejected` for block `B` first. `responded_signers.insert(slot_id)` succeeds (first time), so `total_weight_rejected += w`.
2. Later, `S` legitimately reconsiders (per the documented "repeat my earlier answer unless the reason I rejected has since gone away" flow) and sends `Accepted` for the same `B`.
3. The `Accepted` handler only checks `gathered_signatures.contains_key(&slot_id)`, which is empty for `S` (its only prior message was a rejection, tracked in a different set). The check passes, so `total_weight_approved += w` as well.
4. `S`'s weight `w` is now counted in **both** `total_weight_approved` and `total_weight_rejected` for the same block, even though `S` has exactly one current opinion (Accept). The invariant that `total_weight_approved + total_weight_rejected` reflects each signer's *latest* verdict, bounded by `total_weight`, is broken - this is the direct analog of the LoopFi vault stealing from a shared `availableRewards()` pool because per-pool bookkeeping didn't stay consistent with the shared resource.

This tally is consumed by the miner's coordinator loop: [3](#0-2) 

The rejection branch (`total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight`) is evaluated *before* the acceptance branch. Because `total_weight_rejected` is monotonically non-decreasing and never corrected when a signer flips to Accept, stale rejection weight from signers who have since signed can push the miner over the "blocking minority" threshold even though the same weight (and possibly more) has already reached the real 70% signing threshold in `total_weight_approved`.

### Impact Explanation
This is a liveness wedge on the node/coordinator side: a block that legitimately reaches the 70% signing threshold (`total_weight_approved >= weight_threshold`) can instead be reported as `NakamotoNodeError::SignersRejected` because stale, superseded rejection weight from one or more signers who flipped to Accept is never removed from `total_weight_rejected`. The miner then excludes transactions and treats the tenure as if a real blocking minority rejected the block, when in fact no such live blocking minority exists - the same signer's weight is being double-spent across two mutually exclusive tallies. This falls under the "signer/coordinator wedged such that valid, sufficiently-signed blocks are not accepted" liveness class.

### Likelihood Explanation
Triggering this requires only a single signer to send `Rejected` for a block and later send `Accepted` for the exact same `signer_signature_hash` - a flow the signer's own state machine explicitly supports and documents (rejection reasons can "go away", allowing the same block to later be signed). No majority collusion, no other signer's key, and no malicious behavior are required; ordinary signer re-evaluation traffic delivered over StackerDB gossip is sufficient to desynchronize the two counters at the miner.

### Recommendation
Track a single current verdict (and its weight) per responding slot instead of two independently-incremented totals. When an `Accepted` message for a slot arrives, if that slot previously contributed weight to `total_weight_rejected`, subtract it there before adding to `total_weight_approved` (and symmetrically for a `Rejected` arriving after an `Accepted`). Concretely, replace `gathered_signatures`/`responded_signers` bookkeeping with a single `HashMap<slot_id, Verdict>` and recompute `total_weight_approved`/`total_weight_rejected` from that map, or explicitly decrement the previous tally whenever a slot's verdict changes.

### Proof of Concept
1. Miner proposes block `B` (sighash `H`) to a signer set that includes signer `S` with weight `w`, `total_weight = T`, `weight_threshold = 0.7T`.
2. `S` sends `BlockResponse::Rejected` for `H` (e.g., a transient chainstate mismatch). `StackerDBListener` executes:
   - `block.responded_signers.insert(slot_id)` → `true` → `total_weight_rejected += w`.
3. The rejection condition resolves (per the documented "reason has since gone away" flow) and `S` sends `BlockResponse::Accepted` for the same `H`.
4. `StackerDBListener` executes:
   - `!block.gathered_signatures.contains_key(&slot_id)` → `true` (never populated by the earlier rejection) → `total_weight_approved += w`.
5. Now `total_weight_rejected` still includes `w`, and `total_weight_approved` also includes `w`. If enough other signers accept normally, `total_weight_approved` reaches `weight_threshold` while `total_weight_rejected`, inflated by `S`'s stale rejection weight, simultaneously satisfies `total_weight_rejected + weight_threshold > total_weight` in `SignerCoordinator::get_block_status`, causing the reject branch to fire first and the miner to treat a properly-signed block as globally rejected. [1](#0-0) [2](#0-1) [3](#0-2)

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
