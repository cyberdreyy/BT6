### Title
Independent dedup keys for accept/reject tallies let a signer's stale rejection weight persist after it later accepts the same block, permanently inflating `total_weight_rejected` - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener` tracks two independent weight tallies per block — `total_weight_approved` and `total_weight_rejected` — gated by two *different* dedup sets: the reject path dedups on `responded_signers`, while the accept path dedups on `gathered_signatures`. A signer that first rejects a block and later reconsiders and signs it (a code path the signer explicitly supports) gets its weight added to `total_weight_rejected` and *also* to `total_weight_approved`, with no mechanism to retract the earlier rejection weight. This is the direct analog of the EigenPod report's "no check that a stale balance/state was reconciled before further accounting" — here the stale rejection tally is never zeroed out or re-verified against the signer's latest vote.

### Finding Description
In the rejection handler, a signer's weight is added to `total_weight_rejected` only once, gated by insertion into `responded_signers`: [1](#0-0) 

In the acceptance handler (same `BlockStatus` struct, same block), the weight is added to `total_weight_approved` gated by a *separate* set, `gathered_signatures`: [2](#0-1) 

then `responded_signers.insert(slot_id)` is called unconditionally right after (line 465), but that insertion is redundant for accounting purposes — the weight-adding branch for acceptance was already gated on `gathered_signatures`, which was empty for this signer's first-ever *signature* even though it is not empty for `responded_signers` (already set by their earlier rejection). There is no code path anywhere in this file that subtracts a signer's weight from `total_weight_rejected` when that same signer subsequently accepts (or vice versa).

The signer client itself documents that reconsideration after an initial rejection is a legitimate, supported transition, not an anomaly:
> "we do not change our votes on rejected blocks unless we receive a new block proposal for it and the reject reason allows us to reconsider" (`stacks-signer/src/v0/signer.rs`, around the pre-commit re-check comment). [3](#0-2) 

So a signer flipping Reject → Accept for the same `signer_signature_hash` is a normal occurrence (e.g., re-evaluated after a pre-commit threshold re-check passes), not solely an adversarial one. When it happens, the coordinator ends up with `total_weight_approved + total_weight_rejected` exceeding what any consistent, single-vote-per-signer tally should produce, because the same signer's weight is counted in both buckets simultaneously with no reconciliation.

### Impact Explanation
The consumer of this state, `SignerCoordinator::get_block_status`, checks the rejection condition *before* the acceptance condition: [4](#0-3) [5](#0-4) 

Because `total_weight_rejected` never decreases once a signer's earlier rejection weight has been recorded — even after that same signer later signs the block — a block that has genuinely reached the 70% approval threshold in real time can still simultaneously appear to have crossed the `>30%` "blocking minority" rejection threshold, purely from stale, superseded rejection weight. Since the rejection branch is evaluated first and returns `Err(NakamotoNodeError::SignersRejected { .. })`, this can cause the miner to give up on a block that in fact has (or would have) sufficient real signer support — a liveness wedge on the node side of the coordinator logic that can block otherwise-valid blocks from ever being accepted, matching the "High - wedged into never accepting a valid block" impact class.

### Likelihood Explanation
This requires only a single signer (not a majority) to exhibit the explicitly-supported reject-then-reconsider-and-accept sequence for the same block hash, which the signer code's own comments describe as an intended, reachable transition (not a corrupted/faulty state). No control of another signer's key, StackerDB internals, or majority coordination is needed — one signer's ordinary vote flip is sufficient to desynchronize the two tallies.

### Recommendation
Use a single, unified dedup/accounting structure keyed by `slot_id` that records each signer's *current* vote (Accept/Reject) and weight, and when a signer's vote changes, subtract its weight from the previous bucket before adding it to the new one (or recompute both weights from the single latest-vote-per-signer map before every threshold check), instead of maintaining `total_weight_approved` and `total_weight_rejected` as independently-gated running sums.

### Proof of Concept
1. Signer S (weight `w`) sends `BlockResponse::Rejected` for block `B`. `responded_signers.insert(S)` succeeds → `total_weight_rejected += w`.
2. Later, S reconsiders (as `signer.rs`'s pre-commit re-check comment describes as a supported transition) and sends `BlockResponse::Accepted` for the *same* `B`. `gathered_signatures.contains_key(S)` is `false` (S never signed before) → `total_weight_approved += w` executes.
3. `total_weight_rejected` still includes `w` from step 1 — it is never decremented.
4. If enough other signers push `total_weight_approved` to/above `weight_threshold`, and this stale `w` (plus other stale rejecters) pushes `total_weight_rejected + weight_threshold > total_weight`, `get_block_status` (checked in rejection-first order) returns `SignersRejected`, discarding a block that in fact had legitimate 70% support.

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-446)
```rust
                        if !block.gathered_signatures.contains_key(&slot_id) {
                            block.total_weight_approved = block
                                .total_weight_approved
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-518)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-signer/src/v0/signer.rs (L1323-1331)
```rust
        if !block_info.valid.unwrap_or(false) {
            // We received a pre-commit for a block that we have not validated or we have already marked this block as invalid.
            // We should not do anything further as we do not know what our response should be and we do not change our votes on rejected
            // blocks unless we receive a new block proposal for it and the reject reason allows us to reconsider.
            debug!(
                "{self}: Received a pre-commit for a block that we have not determined to be valid: {:?}. Doing nothing...", block_info.valid
            );
            return;
        }
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-519)
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
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L541-545)
```rust
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```
