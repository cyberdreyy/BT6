### Title
Miner-side StackerDB tally double-counts a signer's weight across both acceptance and rejection pools when a signer flips its vote - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener` maintains two independently-accumulated weight counters per block, `total_weight_approved` and `total_weight_rejected`, exactly like the `calcFee` pattern in the referenced report where multiple fee components are computed separately instead of being kept mutually exclusive. The `Rejected` handler guards against double counting only within its own bucket (`responded_signers.insert(slot_id)`), and the `Accepted` handler guards only within its own bucket (`gathered_signatures.contains_key(&slot_id)`). Neither handler checks whether the *other* bucket already recorded the same signer, so a signer that rejects and later accepts (or vice versa, subject to ordering) has its weight added to both totals, inflating the combined tally beyond what `self.total_weight` should allow.

### Finding Description
In `stackerdb_listener.rs`, on `BlockResponse::Accepted`: [1](#0-0) 
weight is added to `total_weight_approved` guarded solely by `!block.gathered_signatures.contains_key(&slot_id)`, then `block.responded_signers.insert(slot_id)` is set.

On `BlockResponse::Rejected`: [2](#0-1) 
weight is added to `total_weight_rejected` guarded solely by `block.responded_signers.insert(slot_id)` returning true (i.e., first response ever from that slot).

If a signer first sends `Rejected` (weight added to `total_weight_rejected`, `responded_signers` now contains the slot), and later sends `Accepted` for the same block, the `Accepted` branch's guard (`gathered_signatures.contains_key`) is false (nothing has been inserted there yet), so the signer's weight is *also* added to `total_weight_approved`. The same unit of weight now lives in both pools simultaneously, which the two threshold checks in `signer_coordinator.rs` treat as independent: [3](#0-2) 

This breaks the intended invariant that `total_weight_approved` and `total_weight_rejected` are drawn from disjoint sets of signers summing to at most `total_weight`. Notably, the protocol team already recognized and fixed this exact class of bug on the signer's own local ledger (`SignerDb::add_block_rejection_signer_addr` refuses to record a rejection once a signature already exists for that signer/block) and documented it explicitly: [4](#0-3) [5](#0-4) 
but the equivalent guard was never applied to the miner-side `StackerDBListener` tally, which independently re-derives weight totals from the raw StackerDB message stream rather than from the signer's already-deduplicated ledger.

### Impact Explanation
A single non-conforming/byzantine signer (no majority needed) can cause the miner's in-memory tally to hold inflated, mutually-inconsistent totals: `total_weight_rejected` retains stale weight from a signer that has since reversed to `Accepted`, while that same weight is simultaneously credited toward `total_weight_approved`. Because the rejection-threshold check in `SigningCoordinator::wait_for_signatures` (lines 509–513) is evaluated ahead of the approval check (line 541) on every loop iteration, a rejection tally inflated by weight that no longer reflects the signer's current (accepting) vote can drive the coordinator into `Err(NakamotoNodeError::SignersRejected)` and cause it to discard transactions via `temporarily_excluded_txids`/`permanently_excluded_txids`, even when the currently expressed sentiment of signers is sufficient for a valid 70% acceptance. This is a liveness/miscounting defect in the node-side signer coordinator that stalls or misdirects block assembly without requiring any majority collusion — matching the report's bug class of independently-computed components that should have been mutually exclusive compounding into an inflated aggregate.

### Likelihood Explanation
Triggerable by exactly one signer flipping its vote (reject → accept) for a given block, which can occur naturally (e.g., re-evaluation after a timeout, reconsideration of a previously-rejected reason) or be intentionally induced by a malicious signer. No special privileges, majority, or node access are required — only the ability to send two StackerDB messages for the same `signer_signature_hash`.

### Recommendation
In `stackerdb_listener.rs`, track a single per-slot "final response" state (or check both `gathered_signatures` and the rejected-txid/weight bookkeeping) so that a slot's weight is removed from whichever bucket it was previously counted in before being added to the new bucket, mirroring the guard already implemented in `SignerDb::add_block_rejection_signer_addr` (`stacks-signer/src/signerdb.rs:1929-1940`). At minimum, the `Accepted` handler should check `responded_signers`/rejection state and subtract prior rejection weight (or refuse to flip) exactly as the fixed signer-side ledger does, ensuring `total_weight_approved + total_weight_rejected <= total_weight` always holds.

### Proof of Concept
1. Miner proposes block B; `StackerDBListener` initializes `BlockStatus` for B with `total_weight_approved = 0`, `total_weight_rejected = 0`.
2. Signer S (weight w) sends `SignerMessageV0::BlockResponse(BlockResponse::Rejected(...))` for B.
   - `responded_signers.insert(slot_S)` → true → `total_weight_rejected += w`.
3. Signer S later sends `SignerMessageV0::BlockResponse(BlockResponse::Accepted(...))` for the same B (e.g., after re-evaluating and deciding to sign).
   - Guard checked is `!block.gathered_signatures.contains_key(&slot_S)` → true (S's slot isn't in `gathered_signatures` yet) → `total_weight_approved += w`.
4. Now `total_weight_rejected` and `total_weight_approved` both include `w`, so `total_weight_approved + total_weight_rejected` can exceed `self.total_weight`, and stale rejection weight from S persists in `total_weight_rejected` even though S currently accepts — able to trigger `SignersRejected` in `signer_coordinator.rs` on data that no longer reflects the real vote distribution.

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

**File:** stacks-signer/src/signerdb.rs (L1929-1940)
```rust
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

**File:** stacks-signer/CHANGELOG.md (L132-135)
```markdown
### Changed

- Do not count both a block acceptance and a block rejection for the same signer/block. Also ignore repeated responses (mainly for logging purposes).
- Database schema updated to version 16
```
