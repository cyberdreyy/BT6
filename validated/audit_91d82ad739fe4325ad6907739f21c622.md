### Title
Stale rejection weight is never cleared when a signer switches to Accept, causing the node-side coordinator to double-count a signer's weight and misconstrue an achieved acceptance threshold as a block rejection - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListener::run_loop` (message-handling body, `stacks-node/src/nakamoto_node/stackerdb_listener.rs`) maintains a single `BlockStatus` per proposal with two independent weight accumulators, `total_weight_approved` and `total_weight_rejected`, gated by two *different* dedup keys. The `Accepted` branch gates its weight addition on `gathered_signatures` (keyed by signature), while the `Rejected` branch gates its weight addition on the shared `responded_signers` set. Because `Accepted` unconditionally inserts into `responded_signers` too, an Accept-then-Reject sequence from the same signer is correctly blocked from double counting. But the reverse sequence, Reject-then-Accept (a signer changing its mind, which is an explicitly supported flow in the signer's own state machine, see `reject_then_accept` in `stacks-signer/src/signerdb.rs`), is **not** blocked: the earlier rejection weight is never removed, and the later acceptance is freely added because `gathered_signatures` did not yet contain that slot. The signer's weight ends up counted in both tallies simultaneously, breaking the implicit invariant `total_weight_approved + total_weight_rejected ≤ total_weight` and allowing a stale, superseded rejection to combine with other signers' rejections to falsely cross the `>30%`-weight rejection bar, even though the real, current votes have already reached the 70% acceptance threshold.

### Finding Description
Relevant code, `stacks-node/src/nakamoto_node/stackerdb_listener.rs`:

Accept path (lines 443-465): [1](#0-0) 
```
if !block.gathered_signatures.contains_key(&slot_id) {
    block.total_weight_approved = block.total_weight_approved.saturating_add(signer_entry.weight);
    ...
}
block.gathered_signatures.insert(slot_id, signature);
block.responded_signers.insert(slot_id);
```

Reject path (lines 515-518): [2](#0-1) 
```
if block.responded_signers.insert(slot_id) {
    block.total_weight_rejected = block.total_weight_rejected.saturating_add(signer_entry.weight);
    ...
}
```

The Accept branch's weight-add is gated on `gathered_signatures` (a map keyed by slot, populated only by Accepted messages), while the Reject branch's weight-add is gated on `responded_signers` (a set shared between both branches). This is an inconsistency in "accounting basis" directly analogous to the audited bug: one code path (`burn`) used a live/inclusive quantity (`balance`, which still contains un-deducted fees) while the other path (`mint`) used the deducted/canonical quantity (`reserve`), producing a mismatch that could be exploited repeatedly. Here:

- Order A: Accept → Reject. `responded_signers` already contains the slot from the Accept, so the later Reject's `insert()` returns `false` and its weight is correctly *not* added. Consistent.
- Order B: Reject → Accept. `responded_signers` already contains the slot from the Reject (weight already added to `total_weight_rejected`), but `gathered_signatures` does **not** yet contain the slot, so the Accept branch's condition (`!contains_key`) is true and the weight is added *again*, this time to `total_weight_approved`. The earlier `total_weight_rejected` contribution is never subtracted.

This is consumed by `SignerCoordinator::get_block_status` in `stacks-node/src/nakamoto_node/signer_coordinator.rs`, which checks the rejection-crossing condition before the acceptance condition on every poll iteration: [3](#0-2) 

Because the rejection branch is evaluated first, a phantom/stale rejection weight (from a signer who has since legitimately switched to Accept) can combine with genuine rejections from other signers to cross `total_weight_rejected.saturating_add(weight_threshold) > total_weight` before the loop ever reaches the `total_weight_approved >= weight_threshold` branch — even in a state where the *current* votes of the signer set have already reached the real 70% acceptance supermajority.

### Impact Explanation
This wedges block production on the node/coordinator side: a proposal that has genuinely accumulated enough live signer weight to be pushed can instead be treated as `NakamotoNodeError::SignersRejected`, causing the miner to give up on the block, potentially permanently/temporarily excluding transactions based on the corrupted `failed_txids` weight bookkeeping (which uses the same `responded_signers`-gated logic), and forcing a retry/reorg cycle. This matches the "liveness wedge" impact category (a valid, sufficiently-signed block never gets pushed by the miner because the coordinator's aggregated tally diverges from the verified per-signer accept/reject state) rather than a direct chain-consensus safety violation, since the canonical signature-count validation (`NakamotoBlockHeader::verify_signer_signatures` in `stackslib/src/chainstate/nakamoto/mod.rs`) is unaffected and would still accept a block if it were ever actually pushed. The bug is confined to the miner-local `StackerDBListener`/`SignerCoordinator` bookkeeping used to decide when/whether to push a block.

### Likelihood Explanation
Any single signer (well within a "one signer" trigger budget, no majority required) can produce this by rejecting a proposal for a transient/racy reason and then re-evaluating to Accept on a later re-broadcast/pre-commit re-check, a flow the signer-side state machine explicitly supports (`reject_then_accept`, per `stacks-signer/src/signerdb.rs` tests). Combined with the ordinary background rejection weight from other signers approaching (but individually not exceeding) the 30% blocking minority, this can tip the coordinator's rejection condition over the top purely from a stale accounting entry, independent of a genuine loss of live acceptance weight.

### Recommendation
Make the two branches use one consistent dedup/accounting basis, mirroring "use reserves (canonical, up-to-date) not raw/stale balances" from the original report:
- On receipt of an `Accepted` message, if the slot is present in a "rejected" tracking set/weight, subtract that signer's weight from `total_weight_rejected` before/along with adding it to `total_weight_approved` (and vice versa for a late `Rejected` after an `Accepted`, which is already blocked but should be made symmetric/explicit).
- Alternatively, track a single `HashMap<slot_id, Vote>` (Approved(sig) | Rejected(reason)) as the source of truth, and recompute `total_weight_approved`/`total_weight_rejected` from that map on each update, rather than maintaining two independently-gated running totals with different dedup keys.

### Proof of Concept
1. Coordinator opens a `BlockStatus` for proposal `P` with `total_weight = W`, `weight_threshold = 0.7W`.
2. Signer `S` (weight `w_S`) initially rejects `P` (e.g., transient state-machine mismatch): `responded_signers.insert(S)` succeeds, `total_weight_rejected += w_S`.
3. `S` re-evaluates (per `stacks-signer` `reject_then_accept` semantics) and sends `Accepted` for the same `P`: `gathered_signatures` does not contain `S`, so `total_weight_approved += w_S` executes; `responded_signers.insert(S)` is a no-op (already present) but `total_weight_rejected` is never decremented.
4. Enough other signers reject `P` for unrelated reasons such that `total_weight_rejected_other + w_S` crosses `> W - 0.7W` even though real current rejecters (excluding the stale `S` entry) do not.
5. In `SignerCoordinator::get_block_status`, the rejection branch (`total_weight_rejected.saturating_add(weight_threshold) > total_weight`) is checked before the acceptance branch, so the coordinator returns `NakamotoNodeError::SignersRejected` for `P`, discarding a proposal whose live, current signer votes may have already reached the 70% acceptance supermajority.

Note: I was not able to trace every downstream consumer of `failed_txids`/`SignersRejected` beyond `signer_coordinator.rs` within the indexed context, so the exact operational fallout (e.g., precise retry/backoff behavior) beyond "the miner treats the proposal as rejected and moves on" is inferred from the visible code rather than fully traced end-to-end.

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
