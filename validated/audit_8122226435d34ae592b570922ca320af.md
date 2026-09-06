### Title
Stale rejection weight is never cleared when a signer flips from Reject to Accept on the same block, letting rejection weight linger past its 30% blocking threshold and wedge miner block-acceptance - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListener::poll` tracks two independent counters per proposal, `total_weight_approved` and `total_weight_rejected`, keyed off a `responded_signers` set that is only used to prevent a *duplicate* count in each individual branch, never to reconcile a vote change between branches. A signer transitioning from `LocallyRejected` back to `LocallyAccepted` — a transition the signer's own state machine explicitly allows (`docs/signer-flows.md` §2: `LocallyRejected --> LocallyAccepted : re-evaluated`, backed by `should_reevaluate_reject_reason` in `stacks-signer/src/v0/signer.rs:2706-2739`) — causes the miner-side coordinator to add that signer's weight to `total_weight_approved` while never removing it from `total_weight_rejected`.

### Finding Description
In `stacks-node/src/nakamoto_node/stackerdb_listener.rs`:

- On `BlockResponse::Rejected`, weight is added once, gated only by `responded_signers.insert(slot_id)`: [1](#0-0) 

- On `BlockResponse::Accepted`, weight is added if the signer isn't already in `gathered_signatures`, with **no check or adjustment against `total_weight_rejected` or the `responded_signers` set from a prior rejection**: [2](#0-1) 

There is no code path anywhere in `stackerdb_listener.rs` or `signer_coordinator.rs` that subtracts from `total_weight_rejected` when a signer later accepts the same block; the only place `total_weight_rejected` is ever reset is `reset_rejections`, which fires solely on a coordinator-side `SignatureTimeout`, not on a per-signer vote flip: [3](#0-2) 

This exactly mirrors the external report's root cause: a partial/incomplete state transition (partial liquidation / vote-flip) fails to update a running accumulator (`accLoanRatePerSeconds` / `total_weight_rejected`), so a value computed against the *old* state is reused after the state has legitimately moved on, corrupting a downstream equality check (fee owed vs. actually accrued / rejected weight vs. actually still-rejecting signers).

On the signer side, this vote flip is not a hypothetical: `should_reevaluate_reject_reason` explicitly marks several reject reasons (`NotFoundError`, `UnknownParent`, `ConnectivityIssues`, `NoSortitionView`, `NoSignerConsensus`, etc.) as re-evaluable, and `docs/signer-flows.md` documents the resulting `LocallyRejected -> LocallyAccepted` transition as a first-class, expected path: [4](#0-3) [5](#0-4) 

A single signer legitimately going through reject → accept for a transient reason (e.g. `ConnectivityIssues`, `NotFoundError`) therefore leaves its weight permanently counted in `total_weight_rejected` on the miner/coordinator side for the remainder of that proposal's lifetime, while also being counted in `total_weight_approved`.

### Impact Explanation
`signer_coordinator.rs` uses `block_status.total_weight_rejected` to decide whether the block should be abandoned as rejected by a blocking minority: [6](#0-5) 

Because rejection weight from a signer that has since flipped to accept is never retired, the aggregated "rejected" weight the coordinator reads is a stale superset of who is *currently* rejecting. With multiple flip events (or repeated re-proposals with transient reject reasons), this stale weight can accumulate toward the `>30%` blocking-minority threshold even though the *live* set of rejecting signers is well under it, causing the miner to spuriously abort a validly-approvable block (`NakamotoNodeError::SignersRejected`) or to stall waiting on a rejection threshold that can never legitimately be cleared except via the timeout-driven `reset_rejections` path. This breaks the equality "aggregated rejection weight == weight of signers currently rejecting," which the coordinator relies on as the sole gate for both the accept and reject decisions. It is a liveness degradation for block production rather than a consensus-safety violation (no invalid/non-canonical block is ever signed), so it is best characterized as a bounded-impact liveness bug in the miner-side vote tally, not the Critical/High categories of the rules (no invalid signature is produced, no wedge is permanent — it self-heals on `SignatureTimeout` via `reset_rejections`, and a new proposal round starts fresh `BlockStatus`).

### Likelihood Explanation
No majority collusion, key access, or privileged capability is required — this is triggerable by any single signer going through a normal, protocol-sanctioned reject→accept transition, driven entirely by ordinary node conditions (e.g., a signer temporarily missing a burn view / sortition and then catching up, which is exactly the `NotFoundError`/`ConnectivityIssues`/`UnknownParent` re-evaluable class the signer code explicitly anticipates). It requires no active malice, only ordinary asynchronous timing between signers and the burnchain view, making it plausibly frequent in production under transient network/node hiccups.

### Recommendation
When processing `BlockResponse::Accepted` for a `slot_id` that is already present in `responded_signers` from a prior rejection, subtract that signer's weight from `total_weight_rejected` (and, symmetrically, guard the `Rejected` branch against a signer that has already accepted, which is already handled correctly via the `responded_signers.insert` check). Track vote state per-signer (e.g. an enum `Accepted`/`Rejected` keyed by `slot_id`) rather than two independently-incremented weight totals, so the two thresholds are always computed from a single consistent source of truth.

### Proof of Concept
1. A miner proposes block `B`.
2. Signer `S` (with weight `w`) rejects `B` with a re-evaluable reason, e.g. `RejectReason::ValidationFailed(ValidateRejectCode::NotFoundError)` (its own burn view is momentarily behind) — `stackerdb_listener.rs` records `total_weight_rejected += w` and `responded_signers.insert(S)`, per [1](#0-0) .
3. `S`'s node catches up; per `should_reevaluate_reject_reason`, `S`'s local signer state machine re-evaluates and transitions `LocallyRejected -> LocallyAccepted`, sending a fresh `BlockResponse::Accepted` for the same `signer_signature_hash`.
4. `stackerdb_listener.rs` sees `slot_id` for `S` not yet in `gathered_signatures`, so it adds `total_weight_approved += w` per [2](#0-1) , but `total_weight_rejected` still includes `S`'s weight `w` from step 2 — nothing decremented it.
5. Repeat with additional signers exhibiting the same transient reject→accept flip (a realistic scenario during a burnchain-tip catch-up race across several signers) until `total_weight_rejected.saturating_add(weight_threshold) > total_weight` becomes true in `signer_coordinator.rs` (line 509-519), even though the live set of currently-rejecting signers is far below the 30% blocking minority — the coordinator aborts with `NakamotoNodeError::SignersRejected` despite the block having (or being about to have) enough live approvals.

Note: I was not able to fully trace every long-running integration test in `stacks-node/src/tests/signer/` to confirm whether an existing test already exercises and asserts on this exact stale-rejection-weight scenario; the code-level gap (no decrement path for `total_weight_rejected` on a vote flip) is directly confirmed from the source, but empirical confirmation via a live multi-signer harness run was not performed in this analysis.

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

**File:** stacks-signer/src/v0/signer.rs (L2705-2739)
```rust
/// Determine if a block should be re-evaluated based on its rejection reason˝
fn should_reevaluate_reject_reason(block_info: &BlockInfo) -> bool {
    if let Some(reject_reason) = &block_info.reject_reason {
        match reject_reason {
            RejectReason::ValidationFailed(ValidateRejectCode::UnknownParent)
            | RejectReason::ValidationFailed(ValidateRejectCode::NotFoundError)
            | RejectReason::NoSortitionView
            | RejectReason::ConnectivityIssues(_)
            | RejectReason::TestingDirective
            | RejectReason::InvalidTenureExtend
            | RejectReason::ConsensusHashMismatch { .. }
            | RejectReason::NoSignerConsensus
            | RejectReason::NotRejected
            | RejectReason::Unknown(_) => true,
            RejectReason::ValidationFailed(_)
            | RejectReason::RejectedInPriorRound
            | RejectReason::SortitionViewMismatch
            | RejectReason::ReorgNotAllowed
            | RejectReason::InvalidBitvec
            | RejectReason::PubkeyHashMismatch
            | RejectReason::InvalidMiner
            | RejectReason::NotLatestSortitionWinner
            | RejectReason::InvalidParentBlock
            | RejectReason::DuplicateBlockFound
            | RejectReason::IrrecoverablePubkeyHash
            | RejectReason::ProblematicTransactions
            | RejectReason::ProposalTooOld => {
                // No need to re-validate these types of rejections.
                false
            }
        }
    } else {
        false
    }
}
```

**File:** docs/signer-flows.md (L142-145)
```markdown
    Unprocessed --> LocallyRejected : mark_locally_rejected
    PreCommitted --> LocallyRejected : mark_locally_rejected
    LocallyRejected --> LocallyAccepted : re-evaluated
    LocallyAccepted --> LocallyRejected : re-evaluated
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
