### Title
Signer response tally in `StackerDBListener` double-counts a signer's weight when a rejection is later followed by acceptance for the same block — ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
The miner-side `StackerDBListener::poll_for_block_status` (accessed via `handle_block_response`-equivalent match arms) tallies `BlockStatus.total_weight_approved` and `BlockStatus.total_weight_rejected` for a given block sighash using two different, asymmetric de-duplication sets. This lets a single signer's weight be counted in *both* the approved and rejected totals for the same block when that signer rejects first and later accepts (a flow the signer explicitly supports for "reconsiderable" rejections), producing a stale, inflated rejected-weight aggregate that the `SignerCoordinator` on the miner side then uses to decide whether the block was globally rejected and which transactions to (temporarily/permanently) exclude — an "aggregated-weight vs verified-accepts" equality break analogous to the wfCash mismatch between minted shares and actually-received assets.

### Finding Description
In `handle_block_response`'s `Accepted` arm, the guard that decides whether to add the signer's weight to `total_weight_approved` is membership in `block.gathered_signatures`: [1](#0-0) 

Both `gathered_signatures` and `responded_signers` are updated unconditionally afterward: [2](#0-1) 

In the `Rejected` arm, the guard is instead membership in the *shared* `responded_signers` set: [3](#0-2) 

The two arms use different sets for their "have I already counted this signer" checks, but both write into the same `responded_signers` set. This creates an asymmetry:

- **Accept → Reject** (safe): Accept inserts into `responded_signers`. The later Reject's guard (`responded_signers.insert`) returns `false`, so the rejection weight is never added — correct, single counting.
- **Reject → Accept** (broken): Reject inserts into `responded_signers` and adds weight to `total_weight_rejected`. The later Accept's guard checks `gathered_signatures` (still empty for this signer) and returns `true`, so the signer's weight is *also* added to `total_weight_approved`. `total_weight_rejected` is never decremented — there is no code path anywhere in this file that removes or corrects a signer's weight from `total_weight_rejected` once added, short of `reset_rejections`, which only fires on a coordinator-side response timeout.

The signer side of the codebase explicitly supports a legitimate reject→accept transition for the *same* block sighash: `stacks-signer/src/v0/signer.rs` notes "we do not change our votes on rejected blocks unless we receive a new block proposal for it and the reject reason allows us to reconsider" (around `handle_block_pre_commit`), i.e., a re-proposal of the identical block content (same `signer_signature_hash`) can be reconsidered and accepted after an earlier rejection. When that happens, the corresponding `BlockStatus` entry on the miner accumulates *both* the stale rejected weight and the new approved weight for that one signer, permanently (for the lifetime of that `BlockStatus` entry).

### Impact Explanation
`SignerCoordinator::get_block_status` (miner side) drives its accept/reject decision purely from these two aggregates: [4](#0-3) 

Because `total_weight_rejected` can retain a signer's weight even after that same signer accepts, the miner can reach `total_weight_rejected.saturating_add(weight_threshold) > total_weight` — and thus report `SignersRejected` — using weight that no longer represents a live rejection. The same stale weight also feeds the per-txid `failed_txids` accounting used to permanently/temporarily exclude transactions from future proposals (lines 521–535), so a transaction can be banned based on a rejection a signer has since retracted. This is a liveness wedge on block/transaction inclusion driven by a broken aggregated-weight-vs-verified-accepts invariant, matching the report's required impact class of a miscounted response leading to an incorrect consensus-adjacent decision.

### Likelihood Explanation
No malicious majority or key compromise is required. A single honest signer flipping its vote from reject to accept on a re-proposed, identical block (a flow the codebase itself documents as legitimate) is sufficient to trigger the double count. This can happen in ordinary operation whenever a proposal is rejected for a reconsiderable reason and then re-sent unchanged.

### Recommendation
Use one unified de-duplication mechanism (e.g., a single `HashMap<u32, ResponseKind>` keyed by slot id) for both accept and reject tallying, so a signer's most recent response replaces rather than adds to its prior contribution: when a signer's response changes from rejected to accepted (or vice versa), subtract the previously counted weight from the old bucket before adding it to the new one.

### Proof of Concept
1. Miner proposes block B (sighash `H`) to signer set.
2. Signer S (weight W) responds `Rejected(H)` for a reconsiderable reason. `StackerDBListener` records: `responded_signers = {S}`, `total_weight_rejected += W`.
3. Miner re-proposes the same block content (same sighash `H`, e.g. after adjusting an unrelated txid set that does not affect the sighash, or simply resubmitting after the reject reason is invalidated). Signer S reconsiders and responds `Accepted(H)`.
4. In the `Accepted` arm, `block.gathered_signatures.contains_key(&S_slot)` is `false` (S never accepted before), so `total_weight_approved += W` is executed.
5. Result: `BlockStatus` for `H` now shows `total_weight_rejected = W` and `total_weight_approved = W` simultaneously from the same signer, even though S's final, current vote is "accept." `SignerCoordinator::get_block_status` uses the inflated `total_weight_rejected` in its rejection-threshold and txid-exclusion checks as if W is still an active, live rejection weight.

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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-522)
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
```
