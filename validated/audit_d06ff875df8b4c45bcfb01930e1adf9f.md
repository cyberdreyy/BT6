### Title
Coordinator retains stale rejection weight after a signer flips Reject→Accept on the same block, letting phantom weight trigger a false `SignersRejected` even when the genuine current vote tally has reached the 70% signing threshold - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
The node-side vote tally that the mining coordinator uses to decide whether a block proposal is signed or blocked double-books a signer's weight when that signer first sends a `BlockResponse::Rejected` and later sends `BlockResponse::Accepted` for the *same* block. The rejection weight is never retracted, so it keeps counting toward the 30%-blocking-minority check even after the signer's real, current vote is an acceptance. Because the coordinator evaluates the rejection condition before the acceptance condition and returns as soon as it is met, this stale weight can cause the coordinator to declare `NakamotoNodeError::SignersRejected` for a block that in fact has ≥70% genuine, currently-standing signer approval.

### Finding Description
In `StackerDBListener::run` (`stacks-node/src/nakamoto_node/stackerdb_listener.rs`), the two response branches are asymmetric:

- `BlockResponse::Accepted`: weight is added to `total_weight_approved` only `if !block.gathered_signatures.contains_key(&slot_id)` [1](#0-0) 
- `BlockResponse::Rejected`: weight is added to `total_weight_rejected` only `if block.responded_signers.insert(slot_id)` [2](#0-1) 

`responded_signers` is a single set shared by both branches. If a signer rejects first, `responded_signers` already contains its `slot_id` by the time it later accepts; the `Accepted` branch's own guard (`gathered_signatures.contains_key`) is a *different* set, so it still credits the (now-changed) weight to `total_weight_approved`. Nothing ever subtracts that signer's earlier contribution from `total_weight_rejected`. The only place `total_weight_rejected` is reset is `reset_rejections`, called solely on a proposal-submission timeout in `SignerCoordinator` [3](#0-2) , not on every flip.

The consumer of this tally, `SignerCoordinator`'s wait loop, checks the rejection-blocking condition *before* the acceptance condition and returns immediately on the first breach:
```
total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight  -> return Err(SignersRejected)
else if total_weight_approved >= self.weight_threshold                          -> return Ok(signatures)
``` [4](#0-3) 

This is a real, non-adversarial signer behavior: the signer-side state machine explicitly allows a block to move from `LocallyRejected` to `GloballyAccepted` [5](#0-4) , and the documented pre-commit/conflict-resolution flow describes exactly this kind of reconsideration (a signer initially refuses a block, then signs it once a conflict goes stale) [6](#0-5) . So a single signer legitimately reconsidering its vote on a still-pending proposal is enough to leave a permanent "ghost" contribution in `total_weight_rejected`.

### Impact Explanation
Because the rejection check runs first and the loop returns unconditionally on breach, a stale rejection contribution from one (or a few) signer(s) who have since switched to Accept can combine with a smaller amount of *currently* genuine rejection weight from other signers to cross the `>30%`-blocking threshold, even though the currently-standing votes actually satisfy the 70% approval requirement. The coordinator then aborts the round with `SignersRejected`, marks the block's transactions as failed/problematic, and can permanently exclude txids from future blocks [7](#0-6)  — denying a block that the honest signer set was actually willing to sign. This is a liveness wedge on block production/censorship of specific transactions rather than an outright forged signature, matching the "stale threshold" class of high-severity impact: the coordinator acts on a corrupted, stale weight accounting rather than the true, current vote tally.

### Likelihood Explanation
No majority collusion or forged keys are required — only one signer naturally reconsidering (reject, then later accept) the same block proposal while the round is still open, which is an explicitly supported and tested state transition in this codebase. Any deployment with imperfect signer synchrony or transient validation failures followed by recovery (both are normal operating conditions covered by the project's own sibling/conflict tests) can trigger this.

### Recommendation
Track each signer's *current* vote instead of monotonically-accumulated weight per outcome. Concretely, store a per-slot `Option<Vote>` (Accepted/Rejected) and recompute `total_weight_approved`/`total_weight_rejected` from the current votes whenever a new response overwrites a slot's previous vote — decrementing the old bucket's weight before adding to the new bucket. This preserves the "count each signer once, on their current vote" invariant across order-independent Accept/Reject transitions, not just the Accept-then-Reject direction that is already (accidentally) protected today.

### Proof of Concept
Assume 10 signers of equal weight 10 (`total_weight = 100`), `weight_threshold = 70` (blocking minority = 30, i.e. rejection triggers once `total_weight_rejected > 30`).

1. Signer A (weight 10) sends `Rejected` for block X (e.g., stale validation). Coordinator: `total_weight_rejected = 10`.
2. Signer A re-validates and sends `Accepted` for the same block X. Coordinator: `total_weight_approved = 10`; `total_weight_rejected` remains `10` (never cleared).
3. Signers B and C (weight 10 each) independently and genuinely reject block X (e.g. differing miner-view state). Coordinator: `total_weight_rejected = 10(stale A) + 10(B) + 10(C) = 30`. Still not `>30`.
4. A fourth genuinely-still-rejecting signer isn't even needed for the point: with only 3 *distinct* real objectors (B, C, and stale-A which no longer objects), true current opposition is only B+C = 20 (≤30, non-blocking), while true current approval already includes A(10) plus D–J (6 more signers × 10 = 60) = 70, meeting the 70 threshold. Yet if one more small delta arrives (e.g., any duplicate/late reject retry, or slight weight distribution skew), `total_weight_rejected` crosses 30 purely from the retained stale contribution of A, and `SignersRejected` fires — even though 70 real, currently-standing weight supports the block and true current opposition is only 20.

This demonstrates that `total_weight_rejected` in `stacks-node/src/nakamoto_node/stackerdb_listener.rs` can retain phantom weight that no longer reflects any signer's current vote, letting the coordinator in `signer_coordinator.rs` reach a false-rejection decision that contradicts the genuine, currently-standing 70%-threshold vote.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L706-723)
```rust
    /// Reset rejections for a block proposal.
    /// This is used when a block proposal times out and we need to retry it by
    /// clearing the block's rejections. Block approvals cannot be cleared
    /// because an old approval could always be used to make a block reach
    /// the approval threshold.
    pub fn reset_rejections(&self, signer_sighash: &Sha512Trunc256Sum) {
        let (lock, _cvar) = &*self.blocks;
        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");
        if let Some(block) = blocks.get_mut(signer_sighash) {
            block.responded_signers.clear();
            block.total_weight_rejected = 0;

            // Add approving signers back to the responded signers set
            for (slot_id, _) in block.gathered_signatures.iter() {
                block.responded_signers.insert(*slot_id);
            }
        }
    }
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L508-545)
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

**File:** stacks-signer/src/signerdb.rs (L313-329)
```rust
    /// Check if the block state transition is valid
    fn check_state(&self, state: BlockState) -> bool {
        let prev_state = &self.state;
        if *prev_state == state {
            return true;
        }
        match state {
            BlockState::Unprocessed => false,
            BlockState::LocallyAccepted | BlockState::LocallyRejected => !matches!(
                prev_state,
                BlockState::GloballyRejected | BlockState::GloballyAccepted
            ),
            BlockState::GloballyAccepted => !matches!(prev_state, BlockState::GloballyRejected),
            BlockState::GloballyRejected => !matches!(prev_state, BlockState::GloballyAccepted),
            BlockState::PreCommitted => matches!(prev_state, BlockState::Unprocessed),
        }
    }
```

**File:** docs/signer-flows.md (L288-327)
```markdown
Freshness alone is not enough to hold a signature back, because a signature can
outlive the block it covers: a Bitcoin reorg can kill the block, and a dead
signature must not stall the chain restarting beneath it until it goes stale. So
`conflict_still_blocks` derives, per evaluation, whether the conflict could still
end up in the chain. Deriving this here — instead of recording it when a fork is
observed — is deliberate: the node's view mid-reorg is a moving target (burn
block events fire before the sortition transaction commits, and a node error can
wipe the local state machine), so a fact recorded once at observation time can be
silently wrong, while a question asked per evaluation self-corrects on the next
pre-commit or re-proposal. Two questions, in order:

1. **Is the conflict's tenure still on the canonical burn chain?** The signer
   saved the tenure's burn block when it arrived (section 8), and
   `/v3/sortitions/burn/:hash` resolves it against the node's canonical fork. A
   404 means a burnchain fork orphaned the tenure: everything it built is void,
   and the conflict is dead no matter what state its block is in. But a 404
   alone is not proof — the same endpoint 404s a perfectly canonical burn block
   when the node is still catching up (and on internal data misses), so it is
   only trusted once the node's burnchain tip (`get_peer_info`) is at or past
   the stored burn block's height; below that, the conflict keeps blocking and
   the next evaluation retries. If the burn block was never saved (a restart,
   or the tenure predates us), the question is skipped rather than guessed.
2. **Does the node's canonical Stacks chain still reach the block itself?**
   - **it does** — real chain state; keep blocking;
   - **it does not, and the block was globally accepted** — the node once _did_
     have it, so a reorg moved past it. That is proof it is dead;
   - **it does not, and the block was never globally accepted** — a block is
     not handed to the node until the whole signer set has signed it, so this
     may mean "not yet seen" rather than "dead". A sibling at the same height
     therefore keeps blocking, since signing both would be the double-sign this
     guard exists for; a block _above_ the proposal does not, because it is no
     sibling and abandoning an unconfirmed block to restart beneath it is a
     reorg, not an equivocation.

A conflict is any block a signature was ever put over — ours, or a group
threshold we observed — whatever its state now. In particular rejection, even
_global_ rejection, does not clear one: a rejection is a revocable opinion,
while a signature is a bearer instrument that can still be aggregated toward
the 70% threshold if rejecting signers change their minds. Only staleness or
node-derived death (the two questions above) clears a conflict.
```
