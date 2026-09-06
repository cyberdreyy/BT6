### Title
Miner-side vote tally never clears a stale rejection when the same signer later accepts, letting one signer's weight count on both sides of the threshold check - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener` maintains two independent weight counters per proposed block, `total_weight_approved` and `total_weight_rejected`, that `SignerCoordinator` uses to decide whether a block has reached the 70% signing threshold or the >30% blocking-rejection threshold. The code that adds weight into the `Accepted` bucket only guards against re-adding a given signer's weight by checking `gathered_signatures.contains_key(&slot_id)` — it never checks or clears the signer's prior entry in `responded_signers`/`total_weight_rejected`. So a signer that sends `Rejected` and later sends `Accepted` for the exact same `signer_signature_hash` has its weight counted in *both* totals, permanently. This breaks the assumption (relied on by `SignerCoordinator::wait_for_signer_block_status`) that `total_weight_approved` and `total_weight_rejected` are built from disjoint signer sets summing to at most `total_weight`.

### Finding Description
In the `Accepted` branch of the StackerDB message loop, the weight is added under a guard on the `gathered_signatures` map, not on `responded_signers`: [1](#0-0) 

Compare with the `Rejected` branch, which gates weight addition on `block.responded_signers.insert(slot_id)`: [2](#0-1) 

Because these are two separate maps (`gathered_signatures` vs `responded_signers`) tracking essentially the same "have we heard from this slot" fact but gating two different counters independently, a signer that rejects and then later accepts the same block hash:
1. First message (`Rejected`): `responded_signers.insert(slot_id)` succeeds → `total_weight_rejected += weight`.
2. Second message (`Accepted`) for the *same* `signer_signature_hash`: `gathered_signatures.contains_key(&slot_id)` is `false` (this map was never touched by the rejection path) → `total_weight_approved += weight` as well.

`total_weight_rejected` is never decremented, and there is no code path anywhere in this listener that removes a signer's earlier rejection weight once it switches to acceptance. The two counters are consumed independently by `SignerCoordinator::wait_for_signer_block_status`: [3](#0-2) 

The reject branch is evaluated first and unconditionally uses the (now permanently inflated, stale-inclusive) `total_weight_rejected`:
```
if block_status.total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight { ... return Err(SignersRejected...) }
else if block_status.total_weight_approved >= self.weight_threshold { ... return Ok(...) }
```
So even after a signer legitimately reverses its opinion (the signer-side protocol explicitly allows this — see `docs/signer-flows.md`'s statement that "a rejection is a revocable opinion" and the pre-commit re-evaluation flow that can lead a signer from a rejection-adjacent hold state back to signing), the node keeps that signer's weight permanently on the rejection side. This can push `total_weight_rejected` over the blocking-minority threshold purely from stale, superseded votes, causing the coordinator to declare the block `SignersRejected` and permanently/temporarily ban transactions (`permanently_excluded_txids`/`temporarily_excluded_txids`) based on `failed_txids` weight that itself is never invalidated when the reporting signer changes its mind: [4](#0-3) 

### Impact Explanation
This is a liveness wedge on the miner/coordinator side: a well-behaved miner can be driven to reject a block (and permanently exclude transactions from future proposals) even though the actual, current signer set would have reached the 70% signing threshold, because one signer's now-superseded rejection is never cleared from the tally and coincidentally combines with real rejections from other signers to cross the >30% blocking threshold. It does not require a majority of signers — a single signer flipping its vote (via retried/duplicated StackerDB messages or a genuine revocable-opinion state change) is sufficient to leave stale weight in `total_weight_rejected` that never resets, and this stale weight can combine with any minority of honest rejecting signers to falsely cross 30%.

### Likelihood Explanation
The signer-side state machine explicitly treats rejection as revocable and documents scenarios where a signer rejects/holds and later signs the same block (see `docs/signer-flows.md`, sections 5–6, "a rejection is a revocable opinion... can still be aggregated toward the 70% threshold if rejecting signers change their minds"). StackerDB message replay/re-delivery on reconnect or restart is a normal gossip mechanism, so a slot's `Rejected` message being processed before/again alongside a later `Accepted` message for the same block hash is a realistic, non-malicious sequence, not requiring majority collusion or another party's key. I was not able to fully confirm within the available tool budget whether `stacks-signer/src/v0/signer.rs`'s local state machine ever actually re-sends both a rejection and later an acceptance for the *identical* `signer_signature_hash` (versus only for distinct competing proposals), which is the remaining open question for definitively pinning likelihood; this should be verified against `BlockInfo::check_state`/`mark_locally_rejected`/`mark_locally_accepted` transition rules in `stacks-signer/src/signerdb.rs`.

### Recommendation
Use a single authoritative "have we recorded a decision from this signer for this block" map (or check `responded_signers` in the `Accepted` branch, and vice versa `gathered_signatures` in the `Rejected` branch) so that a signer's weight can never simultaneously occupy both `total_weight_approved` and `total_weight_rejected` for the same block. When a signer's later message supersedes an earlier one, subtract the earlier contribution before/while adding the new one, and likewise re-evaluate/clear `failed_txids` weight attributed to a signer that has since reversed its rejection.

### Proof of Concept
1. Miner proposes block B; `total_weight = 100`, `weight_threshold = 70`.
2. Signer S1 (weight 25) sends `BlockResponse::Rejected` for B → `total_weight_rejected = 25`, `responded_signers = {S1}`.
3. S1 later (e.g., after a re-evaluation, or via a resent/duplicated StackerDB message) sends `BlockResponse::Accepted` for the *same* B → since `gathered_signatures` does not yet contain S1's slot, `total_weight_approved += 25` (now 25), and `total_weight_rejected` is left unchanged at 25.
4. Two more signers, S2 and S3 (weight 6 each, distinct from S1), independently and honestly reject B for unrelated reasons → `total_weight_rejected = 25 + 6 + 6 = 37 > total_weight - weight_threshold (30)`.
5. `SignerCoordinator::wait_for_signer_block_status` sees `total_weight_rejected.saturating_add(70) > 100` → returns `Err(SignersRejected)`, even though S1 has already switched to `Accepted` and the true rejecting weight (S2+S3=12) is far below the 30% blocking minority.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-519)
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
