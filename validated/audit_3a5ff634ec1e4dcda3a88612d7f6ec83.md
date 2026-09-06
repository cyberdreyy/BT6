Based on my analysis, I found a valid analog: a permanent, unresettable weight leak between the reject/accept tallies the node-side coordinator uses to decide a block's fate, directly analogous to the CDS report's pattern of a value getting stuck in the wrong running-total bucket.

### Title
Stale per-signer rejection weight is never cleared when a signer flips from Reject to Accept, letting `total_weight_rejected` permanently over-count and force a valid block into `SignersRejected` - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener::handle_block_response` (in `stacks-node/src/nakamoto_node/stackerdb_listener.rs`) accumulates two independent running totals per block, `total_weight_approved` and `total_weight_rejected`, that the mining coordinator (`SignCoordinator::get_block_status` in `stacks-node/src/nakamoto_node/signer_coordinator.rs`) treats as mutually exclusive buckets of the same fixed signer-weight pool when deciding whether a block proposal is accepted, rejected, or still pending. Just like the CDS report where a profit amount was folded into `totalCdsDepositedAmount` without ever being reconciled against the individual depositor ledger, here a signer's weight can be added into `total_weight_rejected` and then, when that same signer legitimately changes its vote to Accept, added *again* into `total_weight_approved` — without the corresponding entry ever being removed from `total_weight_rejected`. The dedup guards for the two buckets are asymmetric and do not share state, so the equality "each signer's weight counts toward at most one live bucket" is broken.

### Finding Description
For the Accepted case: [1](#0-0) 
the dedup check is `!block.gathered_signatures.contains_key(&slot_id)`.

For the Rejected case: [2](#0-1) 
the dedup check is `block.responded_signers.insert(slot_id)`.

Both branches also insert the slot into `responded_signers` [3](#0-2) 
but only the Accepted branch inserts into `gathered_signatures`. Consequently:
- Accept → Reject: the second (reject) message is correctly ignored, since `responded_signers` already contains the slot.
- Reject → Accept: the second (accept) message is *not* ignored, since `gathered_signatures` does not yet contain the slot, so the signer's weight is added to `total_weight_approved` while its earlier contribution to `total_weight_rejected` is never removed.

This reject→accept transition is not a hypothetical or malicious-only path — the signer's own re-evaluation logic explicitly allows a stale rejection to be reconsidered and superseded by acceptance of the same block. `should_reevaluate_reject_reason` in `stacks-signer/src/v0/signer.rs` marks several reject reasons (e.g. `ValidationFailed(NotFoundError)`, `ValidationFailed(UnknownParent)`, `ConnectivityIssues`, `NoSortitionView`) as re-evaluable: [4](#0-3) 
and this is exercised by existing test scenarios where a signer rejects due to a transient missing-burn-view/Bitcoin-block condition and later reconsiders and accepts the same proposal (`stacks-node/src/tests/signer/v0/missing_burn_block_proposal.rs`, `stacks-node/src/tests/signer/v0/reprocess_block_proposals.rs`).

The stale weight has no path to be cleaned up: `total_weight_rejected` is only ever `saturating_add`'ed to, and the only reset is `reset_rejections`, which fires solely on a coordinator-side timeout when resending a proposal, not on a signer's vote flip.

The coordinator then consumes this corrupted state: [5](#0-4) 
The rejection-crosses-blocking-minority check (`total_weight_rejected.saturating_add(weight_threshold) > total_weight`) is evaluated *before* the acceptance check (`total_weight_approved >= weight_threshold`), so once enough weight is stuck (falsely) in `total_weight_rejected` from signers who have since switched to Accept, the miner can be told the block is `SignersRejected` even though live signer intent (both current accepts and the "flipped" signer) actually clears the 70% acceptance threshold.

### Impact Explanation
This breaks the equality the report's bug class targets: the aggregated weight tallies used to decide a block's fate no longer reflect the true, current sum of individual signer votes — one signer's weight is double-booked across mutually exclusive buckets. The practical consequence is a liveness wedge on valid blocks: the mining coordinator can declare `SignersRejected` for a block that a real supermajority of current signer weight would accept, forcing the miner to abandon a valid/canonical proposal and potentially exclude transactions or reorg into a new proposal unnecessarily. This matches the "High" impact class of a wedge causing a signer/coordinator to act on a stale weight/threshold.

### Likelihood Explanation
No majority collusion or malicious signer is required. A single ordinary signer transiently rejecting a proposal for a re-evaluable reason (e.g., a burn-view not yet caught up, or a not-found chainstate error) and then legitimately reconsidering and accepting the same block — a scenario the codebase itself has dedicated tests for — is sufficient to leave stale weight in `total_weight_rejected` forever for that block hash.

### Recommendation
Make the two buckets mutually exclusive and reconciled on vote change: track a single `HashMap<slot_id, Vote>` (or clear/adjust the opposite bucket's weight) instead of two independently-guarded accumulators, so that when a signer's vote for a given block transitions from Reject to Accept (or vice versa), its weight is moved rather than added into both totals.

### Proof of Concept
1. Signer S (weight w) sends `BlockResponse::Rejected` for block B with a re-evaluable reason (e.g. `ValidationFailed(NotFoundError)`) → `handle_block_response` adds w to `total_weight_rejected`, inserts S's slot into `responded_signers` only.
2. The underlying condition resolves (e.g., burn block catches up) and S's local state machine reconsiders and re-sends `BlockResponse::Accepted` for the same B (this is exactly the flow validated by `signer_reevaluates_proposal_with_missing_burn_view` / `signers_reprocess_bitcoin_block_not_found_proposals`).
3. In `handle_block_response`, the Accepted branch checks `!gathered_signatures.contains_key(&slot_id)` — true, since only the Rejected branch ran before — so w is added to `total_weight_approved` too. `total_weight_rejected` is never decremented.
4. If enough additional signers reject (even transiently, on other now-resolved reasons) such that `total_weight_rejected.saturating_add(weight_threshold) > total_weight` before their own re-evaluation completes, `SignCoordinator::get_block_status` returns `SignersRejected` even though the currently live votes (including S's Accept) would otherwise reach the 70% acceptance threshold.

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
