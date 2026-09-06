Based on my investigation, I found a strong analog to the Nouns DAO "final state that isn't actually final" bug class in the stacks-signer proposal re-evaluation logic.

### Title
`should_reevaluate_reject_reason` permanently blocks re-evaluation of a proposal rejected for `NotLatestSortitionWinner`/`ReorgNotAllowed`/`InvalidParentBlock`, even though the underlying condition is time-dependent and can flip - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`should_reevaluate_block` treats a stored rejection as terminal unless `should_reevaluate_reject_reason` returns `true`. [1](#0-0)  That helper hard-codes `RejectReason::NotLatestSortitionWinner`, `RejectReason::ReorgNotAllowed`, and `RejectReason::InvalidParentBlock` as non-reevaluable ("No need to re-validate these types of rejections"). [2](#0-1)  But the root cause of each of these rejections is `SortitionMinerStatus`/miner-timeout state in `SortitionsView`, which is explicitly time-based and can change from "invalid" to "valid" as time passes (`is_timed_out`) or as new sortitions/burn views arrive — exactly the same "final-but-not-really-final" pattern as the Nouns `Expired` state depending on the mutable `GRACE_PERIOD`.

### Finding Description
In `chainstate/v1.rs`, `check_proposal` computes `SortitionMinerStatus` dynamically per call: a miner starts `Valid` and can become `InvalidatedBeforeFirstBlock` only if `SortitionState::is_timed_out` reports the elapsed time since last activity exceeds `block_proposal_timeout`, or if `check_parent_tenure_choice` disallows a reorg. [3](#0-2)  A block from the *last* sortition is rejected with `NotLatestSortitionWinner` precisely when `cur_sortition.miner_status` is still `Valid`: [4](#0-3) 

This is a snapshot of state at one evaluation instant. Because `is_timed_out` and `check_parent_tenure_choice` are wall-clock/database-driven and recomputed fresh on every `check_proposal` call, `cur_sortition.miner_status` can transition from `Valid` to `InvalidatedBeforeFirstBlock` purely due to elapsed time or a new burn block — with no signer restart or admin action required, only ordinary protocol activity (or a miner/gossip-triggered delay). Once that happens, a proposal from the *last* sortition winner that was previously rejected as `NotLatestSortitionWinner` would legitimately pass `check_proposal` if re-evaluated.

However, `should_reevaluate_reject_reason` marks `NotLatestSortitionWinner` (and `ReorgNotAllowed`, `InvalidParentBlock`, `DuplicateBlockFound`, `SortitionViewMismatch`) as final, so `should_reevaluate_block` will never re-run `check_proposal` for a stored proposal with that reject reason — it either resends the stale rejection or, if still `PreCommitted`, re-runs only the pre-commit path, not the original sortition check. [5](#0-4)  This is the mirror image of the Nouns bug: a rejection is classified as permanent based on a condition (`miner_status`) that is not actually immutable.

### Impact Explanation
This maps to the allowed "High" impact class: "a signer wedged into never signing valid blocks." If the prior-sortition miner's block is the one that should legitimately continue the chain (e.g., the current sortition's miner timed out shortly after the first rejection was cached), the signer that already rejected the re-proposed block will simply resend the identical stale `NotLatestSortitionWinner` rejection forever for that exact block hash, never re-invoking `check_proposal` to notice that `cur_sortition.miner_status` has since flipped to invalid. This can delay or block tenure recovery for that individual signer, contributing to liveness degradation for the affected block hash (the miner would need to produce a new proposal hash, e.g. via re-signing with a new timestamp, to escape the cached decision).

### Likelihood Explanation
Low-to-moderate. It requires the specific timing window where a `NotLatestSortitionWinner`/`ReorgNotAllowed` rejection is cached for a signer, the current miner subsequently times out or a reorg check outcome would change, and the exact same block (same `signer_signature_hash`) is re-proposed rather than a freshly-signed one. Existing tests (`signer_reevaluates_proposal_with_missing_burn_view`, `stale_sibling_replaced_when_canonical_tip_below`) show the team is aware of and has fixed several similar reevaluation gaps for other reject reasons (`NotFoundError`, `ConsensusHashMismatch`, `NoSignerConsensus`), suggesting `NotLatestSortitionWinner`/`ReorgNotAllowed` may be an oversight rather than a deliberate design choice, but I could not confirm from the available code/tests whether this exact scenario is exercised or intentionally excluded.

### Recommendation
Re-examine whether `NotLatestSortitionWinner`, `ReorgNotAllowed`, and `InvalidParentBlock` should be added to the re-evaluable set in `should_reevaluate_reject_reason`, since their root causes (`SortitionMinerStatus`, timeout-based `is_timed_out`, and `check_parent_tenure_choice`) are not immutable facts about the block but time/state-dependent judgments that can change between evaluations. At minimum, re-run `check_proposal` from scratch (not just the pre-commit path) whenever the underlying sortition view has been refreshed since the cached rejection was recorded.

### Proof of Concept
I could not construct or verify an executable PoC/test within this session; the analysis above is based on static code-path tracing of `check_proposal` (stacks-signer/src/chainstate/v1.rs) and `should_reevaluate_reject_reason`/`should_reevaluate_block` (stacks-signer/src/v0/signer.rs). Confirming the finding as a concrete safety/liveness break would require constructing an integration test that: (1) proposes a "last sortition" block while the current sortition's miner is still marked `Valid`, causing a cached `NotLatestSortitionWinner` rejection; (2) waits for `block_proposal_timeout` to elapse so `is_timed_out` would flip `cur_sortition.miner_status`; (3) re-proposes the identical block hash and observes whether the signer resends the stale rejection instead of re-validating.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1505-1529)
```rust
        if !should_reevaluate_reject_reason(block_info) {
            if block_info.state == BlockState::PreCommitted {
                // We validated this block but haven't signed it. Signing requires the
                // pre-commit threshold and the conflict checks in `handle_block_pre_commit`.
                // Re-broadcast our pre-commit and re-run that evaluation instead of
                // responding with a signature directly, so a re-proposed block can't
                // bypass those checks.
                info!(
                    "{self}: received a block proposal for a block we have pre-committed to but not signed. Re-evaluating the pre-commit.";
                    "signer_signature_hash" => %signer_signature_hash,
                    "block_id" => %block_info.block.block_id(),
                    "block_height" => block_info.block.header.chain_length,
                    "burn_height" => block_proposal.burn_height,
                    "consensus_hash" => %block_info.block.header.consensus_hash
                );
                self.send_block_pre_commit(signer_signature_hash.clone());
                let address = self.stacks_address.clone();
                self.handle_block_pre_commit(
                    stacks_client,
                    sortition_state,
                    &address,
                    &signer_signature_hash,
                );
                return false;
            }
```

**File:** stacks-signer/src/v0/signer.rs (L1530-1559)
```rust
            if let Some(block_response) = self.determine_response(block_info) {
                self.send_block_response(&block_info.block, block_response);
                return false;
            } else {
                let is_pending = self
                    .signer_db
                    .has_pending_block_validation(&signer_signature_hash)
                    .unwrap_or_else(|e| {
                        warn!("{self}: Failed to load pending block validations: {e:?}");
                        false
                    });
                if is_pending {
                    debug!(
                        "{self}: received a block proposal for a block for which we is already pending validation. Do nothing.";
                        "signer_signature_hash" => %block_info.block.header.signer_signature_hash(),
                        "block_id" => %block_info.block.block_id()
                    );
                    return false;
                } else {
                    info!(
                        "{self}: received a block proposal for this block before, but we do not have a pending validation for it.";
                        "reject_reason" => ?block_info.reject_reason,
                        "signer_signature_hash" => %signer_signature_hash,
                        "block_id" => %block_info.block.block_id(),
                        "block_height" => block_info.block.header.chain_length,
                        "burn_height" => block_proposal.burn_height,
                        "consensus_hash" => %block_info.block.header.consensus_hash
                    );
                }
            }
```

**File:** stacks-signer/src/v0/signer.rs (L2705-2735)
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
```

**File:** stacks-signer/src/chainstate/v1.rs (L144-202)
```rust
        if self.cur_sortition.miner_status == SortitionMinerStatus::Valid
            && SortitionState::is_timed_out(
                &self.cur_sortition.data.consensus_hash,
                signer_db,
                self.config.block_proposal_timeout,
            )?
        {
            info!(
                "Current miner timed out, marking as invalid.";
                "block_height" => block.header.chain_length,
                "block_proposal_timeout" => ?self.config.block_proposal_timeout,
                "current_sortition_consensus_hash" => ?self.cur_sortition.data.consensus_hash,
            );
            self.cur_sortition.miner_status = SortitionMinerStatus::InvalidatedBeforeFirstBlock;

            // If the current proposal is also for this current
            // sortition, then we can return early here.
            if self.cur_sortition.data.consensus_hash == block.header.consensus_hash {
                return Err(RejectReason::InvalidMiner);
            }
        } else if let Some(tip) = signer_db
            .get_canonical_tip()
            .map_err(SignerChainstateError::from)?
        {
            // Check if the current sortition is aligned with the expected tenure:
            // - If the tip is in the current tenure, we are in the process of mining this tenure.
            // - If the tip is not in the current tenure, then we’re starting a new tenure,
            //   and the current sortition's parent tenure must match the tenure of the tip.
            // - If the tip is not building off of the current sortition's parent tenure, then
            //   check to see if the tip's parent is within the first proposal burn block timeout,
            //   which allows for forks when a burn block arrives quickly.
            // - Else the miner of the current sortition has committed to an incorrect parent tenure.
            let consensus_hash_match =
                self.cur_sortition.data.consensus_hash == tip.block.header.consensus_hash;
            let parent_tenure_id_match =
                self.cur_sortition.data.parent_tenure_id == tip.block.header.consensus_hash;
            if !consensus_hash_match && !parent_tenure_id_match {
                // More expensive check, so do it only if we need to.
                let is_valid_parent_tenure = self.cur_sortition.data.check_parent_tenure_choice(
                    signer_db,
                    client,
                    &self.config.first_proposal_burn_block_timing,
                )?;
                if !is_valid_parent_tenure {
                    warn!(
                        "Current sortition does not build off of canonical tip tenure, marking as invalid";
                        "current_sortition_parent" => ?self.cur_sortition.data.parent_tenure_id,
                        "tip_consensus_hash" => ?tip.block.header.consensus_hash,
                    );
                    self.cur_sortition.miner_status =
                        SortitionMinerStatus::InvalidatedBeforeFirstBlock;

                    // If the current proposal is also for this current
                    // sortition, then we can return early here.
                    if self.cur_sortition.data.consensus_hash == block.header.consensus_hash {
                        return Err(RejectReason::ReorgNotAllowed);
                    }
                }
            }
```

**File:** stacks-signer/src/chainstate/v1.rs (L301-316)
```rust
            ProposedBy::LastSortition(last_sortition) => {
                // should only consider blocks from the last sortition if the new sortition was invalidated
                //  before we signed their first block.
                if self.cur_sortition.miner_status
                    != SortitionMinerStatus::InvalidatedBeforeFirstBlock
                {
                    warn!(
                        "Miner block proposal is from last sortition winner, when the new sortition winner is still valid. Considering proposal invalid.";
                        "proposed_block_consensus_hash" => %block.header.consensus_hash,
                        "signer_signature_hash" => %block.header.signer_signature_hash(),
                        "current_sortition_miner_status" => ?self.cur_sortition.miner_status,
                        "last_sortition" => %last_sortition.data.consensus_hash
                    );
                    return Err(RejectReason::NotLatestSortitionWinner);
                }
            }
```
