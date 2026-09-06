### No vulnerability found for this question.

**Rationale:**

The claimed path requires two things that put this outside actionable scope: (1) a "hypothetical future field" that does not exist anywhere in the current `BlockInfo` struct or `RejectReason` enum in the codebase, and (2) an operator-controlled binary downgrade, which is not an action available to the unprivileged, single-slot attacker defined by the rules (no local access, not a node operator). The rules explicitly exclude theoretical findings and any path requiring a privileged/operational role.

Tracing the actual decision-basis equality the question asks about:

- `BlockInfo.reject_reason: Option<RejectReason>` is a `SignerDb`-local, `serde_json`-persisted field [1](#0-0) .
- The only accept/reject decision function, `determine_response`, branches solely on `block_info.valid` (an `Option<bool>`) — it never reads `reject_reason` to decide accept vs. reject, and hardcodes `RejectReason::RejectedInPriorRound` for any stale rejection it re-sends: [2](#0-1) .
- The only other consumer of `reject_reason`, `should_reevaluate_reject_reason`, only decides whether to re-run validation vs. resend a cached response for an already-tracked, already-rejected block; it does not itself flip an accept/reject vote, since `determine_response` (called in the "no re-evaluation" branch) still uses `valid`, which is independently and correctly set to `false` for any rejected block regardless of which `RejectReason` variant was recorded: [3](#0-2) [4](#0-3) .
- The codebase already anticipates cross-version `RejectReason` widening on the wire-protocol path (not the local DB) via an explicit `Unknown(u8)` catch-all variant and a documented coarsening fallback (`RejectCode::SortitionViewMismatch` for unrecognized reasons), showing this exact class of concern was already designed for at the network layer: [5](#0-4) .
- There is an existing, maintained regression test (`deserialize_old_block_info`) exercising forward-compatible deserialization of `BlockInfo` across schema versions, confirming the team already treats this class of issue as a tested compatibility concern rather than an unguarded gap: [6](#0-5) .

Since (a) the specific field/variant does not exist today, (b) the attacker cannot trigger a binary downgrade, and (c) even under the hypothesized data loss, `determine_response`'s accept/reject decision is driven only by `valid`/`state`, not by `reject_reason`, the claimed equality break (decision-basis before == decision-basis after downgrade) does not fail. No safety (signing invalid/conflicting blocks, rejection recounted as acceptance) or liveness (wedge) property is broken by this scenario.

### Citations

**File:** stacks-signer/src/signerdb.rs (L203-230)
```rust
#[derive(Serialize, Deserialize, Debug, PartialEq, Clone)]
pub struct BlockInfo {
    /// The block we are considering
    pub block: NakamotoBlock,
    /// The burn block height at which the block was proposed
    pub burn_block_height: u64,
    /// The reward cycle the block belongs to
    pub reward_cycle: u64,
    /// Our vote on the block if we have one yet
    pub vote: Option<NakamotoBlockVote>,
    /// Whether the block contents are valid according to our local and node validation. None if not yet validated.
    pub valid: Option<bool>,
    /// Time at which the proposal was received by this signer (epoch time in seconds)
    pub proposed_time: u64,
    /// Time at which the proposal was pre-commited to by this signer (epoch time in seconds)
    pub approved_time: Option<u64>,
    /// Time at which the proposal was signed by this signer (epoch time in seconds)
    pub signed_self: Option<u64>,
    /// Time at which the proposal was signed by a threshold in the signer set (epoch time in seconds)
    pub signed_group: Option<u64>,
    /// The block state relative to the signer's view of the stacks blockchain
    pub state: BlockState,
    /// Consumed processing time in milliseconds to validate this block
    pub validation_time_ms: Option<u64>,
    /// Extra data specific to v0, v1, etc.
    pub ext: ExtraBlockInfo,
    /// If this signer rejected this block, what was the reason
    pub reject_reason: Option<RejectReason>,
```

**File:** stacks-signer/src/signerdb.rs (L4209-4248)
```rust
    /// Verify that we can deserialize the old BlockInfo struct into the new version
    #[test]
    fn deserialize_old_block_info() {
        let block_info_prev = BlockInfoPrev {
            block: NakamotoBlock::new(NakamotoBlockHeader::genesis(), vec![]),
            burn_block_height: 2,
            reward_cycle: 3,
            vote: None,
            valid: None,
            signed_over: true,
            proposed_time: 4,
            signed_self: None,
            signed_group: None,
            state: BlockState::Unprocessed,
            validation_time_ms: Some(5),
            ext: ExtraBlockInfo::default(),
        };

        let block_info: BlockInfo =
            serde_json::from_value(serde_json::to_value(&block_info_prev).unwrap()).unwrap();
        assert_eq!(block_info.block, block_info_prev.block);
        assert_eq!(
            block_info.burn_block_height,
            block_info_prev.burn_block_height
        );
        assert_eq!(block_info.reward_cycle, block_info_prev.reward_cycle);
        assert_eq!(block_info.vote, block_info_prev.vote);
        assert_eq!(block_info.valid, block_info_prev.valid);
        assert_eq!(block_info.proposed_time, block_info_prev.proposed_time);
        assert_eq!(block_info.approved_time, block_info_prev.signed_self);
        assert_eq!(block_info.signed_self, block_info_prev.signed_self);
        assert_eq!(block_info.signed_group, block_info_prev.signed_group);
        assert_eq!(block_info.state, block_info_prev.state);
        assert_eq!(
            block_info.validation_time_ms,
            block_info_prev.validation_time_ms
        );
        assert_eq!(block_info.ext, block_info_prev.ext);
        assert!(block_info.reject_reason.is_none());
    }
```

**File:** stacks-signer/src/v0/signer.rs (L458-471)
```rust
    fn determine_response(&mut self, block_info: &BlockInfo) -> Option<BlockResponse> {
        // We will only have the valid field set if we have already validated this block
        // against our stacks-node/local state.
        let valid = block_info.valid?;
        let response = if valid {
            debug!("{self}: Accepting block {}", block_info.block.block_id());
            self.create_block_acceptance(&block_info.block).into()
        } else {
            debug!("{self}: Rejecting block {}", block_info.block.block_id());
            self.create_block_rejection(RejectReason::RejectedInPriorRound, &block_info.block)
                .into()
        };
        Some(response)
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1505-1532)
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
            if let Some(block_response) = self.determine_response(block_info) {
                self.send_block_response(&block_info.block, block_response);
                return false;
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

**File:** libsigner/src/v0/messages.rs (L1099-1113)
```rust
impl From<&RejectReason> for RejectCode {
    fn from(reject_reason: &RejectReason) -> Self {
        match reject_reason {
            RejectReason::ValidationFailed(code) => RejectCode::ValidationFailed(*code),
            RejectReason::NoSortitionView => RejectCode::NoSortitionView,
            RejectReason::ConnectivityIssues(reason) => {
                RejectCode::ConnectivityIssues(reason.clone())
            }
            RejectReason::RejectedInPriorRound => RejectCode::RejectedInPriorRound,
            RejectReason::SortitionViewMismatch => RejectCode::SortitionViewMismatch,
            RejectReason::TestingDirective => RejectCode::TestingDirective,
            // Newer reject reasons were expanded from SortitionViewMismatch
            _ => RejectCode::SortitionViewMismatch,
        }
    }
```
