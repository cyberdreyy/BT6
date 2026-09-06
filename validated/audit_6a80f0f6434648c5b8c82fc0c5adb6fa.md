### Title
GloballyRejected block can be re-signed via `should_reevaluate_reject_reason`-permitted reprocessing, which allocates a fresh `BlockInfo` instead of re-validating against the persisted terminal state - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`BlockInfo::check_state` correctly forbids `GloballyRejected -> GloballyAccepted` on a single in-memory `BlockInfo` instance, but `should_reevaluate_block`/`should_reevaluate_reject_reason` can route a re-proposed block whose `reject_reason` is on the "re-evaluable" allow-list back into the `handle_block_proposal` "fresh evaluation" path, which constructs a brand-new `BlockInfo::from(block_proposal)` at `state: Unprocessed`. That fresh struct's own transition history trivially satisfies `check_state`, and `insert_block` persists it keyed by `signer_signature_hash`, overwriting the durable `GloballyRejected` row.

### Finding Description
The documented invariant (docs/signer-flows.md:152-154) is that "each global state is unreachable from the other," anchored on `BlockInfo::check_state` [1](#0-0) , which explicitly blocks `GloballyRejected -> GloballyAccepted` and vice versa. This guard, however, is enforced only on a single `BlockInfo` struct's own `state` field, not against whatever is already durably stored in `signerdb` for that `signer_signature_hash`.

In `should_reevaluate_block` [2](#0-1) , the only short-circuit for an already-decided block checks `globally_approved_and_responded()`, which only matches `state == GloballyAccepted` [3](#0-2) . There is no equivalent short-circuit for `GloballyRejected`. Instead, control falls through to `should_reevaluate_reject_reason(block_info)` [4](#0-3) , which decides purely from `block_info.reject_reason` — a field set only when *this signer* locally rejected the block for that specific cause (e.g. `NotFoundError`, `UnknownParent`, `ConnectivityIssues`, `NoSignerConsensus`). If this signer had locally rejected a block for one of the "true" (re-evaluable) reasons, and the block subsequently reached `GloballyRejected` in this signer's DB (e.g., through `store_and_process_block_rejection` on the ≥30% rejection weight from peers — a legal `LocallyRejected -> GloballyRejected` or `LocallyAccepted -> GloballyRejected` transition per `check_state`), the stored row is `GloballyRejected` but `reject_reason` is still the earlier re-evaluable code.

On re-proposal of the byte-identical block, `should_reevaluate_block` sees `should_reevaluate_reject_reason == true` and returns `true` (reevaluate) directly, without checking that `block_info.state == GloballyRejected`. Back in `handle_block_proposal` [5](#0-4) , this causes the code to skip the early `return` and fall into the "fresh evaluation" branch, which discards the retrieved `prior_block_info` and builds a brand new `BlockInfo::from(block_proposal.clone())` at `state: Unprocessed`, `reject_reason: None`. This fresh object is then run through `check_block_against_state`, submitted for validation, and — if it passes and gathers enough pre-commits/signatures — transitions `Unprocessed -> PreCommitted -> LocallyAccepted -> GloballyAccepted` via `mark_pre_committed`/`mark_locally_accepted`/`mark_globally_accepted`, each of which is a *legal* transition for that fresh struct under `check_state`. `insert_block` then persists this struct, and because it's keyed by `signer_signature_hash`, it overwrites the previously-stored `GloballyRejected` row for the exact same hash.

The `check_state` guard is therefore bypassed not by breaking the state machine's internal transition rules, but by discarding the in-memory representation of the terminal state and starting a new one, so the guard never sees the pre-existing `GloballyRejected` verdict.

### Impact Explanation
This breaks the safety property that a durable global rejection must remain terminal: a signer can be induced to sign (or count toward global acceptance) a block hash that its own signerdb has already recorded as `GloballyRejected`. Because `GloballyRejected` is meant to be the durable-negative counterpart of `GloballyAccepted` (and the reject weight is publicly gossiped), other signers/observers treating the earlier `GloballyRejected` record as final could be contradicted by a later `GloballyAccepted`/signature response for the identical `signer_signature_hash`, i.e., a rejection recounted as (or superseded by) acceptance for one and the same block. This matches the Critical category ("a rejection recounted as acceptance").

### Likelihood Explanation
The precondition is narrow but plausible under the exact scenario the question poses: the attacker (single miner slot) reproposes the byte-identical block after it reaches `GloballyRejected` in a signer's local db, but only exploits weight if that same signer's `reject_reason` for that hash happened to be one of the re-evaluable categories (`NotFoundError`, `UnknownParent`, `ConnectivityIssues`, `NoSignerConsensus`, `ConsensusHashMismatch`, `NoSortitionView`, `InvalidTenureExtend`, `TestingDirective`, `Unknown`, `NotRejected`). These are exactly the categories the codebase's own comments and the `missing_burn_block_proposal.rs` test document as intentionally "re-evaluable rather than terminal," suggesting the re-evaluation policy was designed around `LocallyRejected` states and the interaction with an already-`GloballyRejected` row was not explicitly considered. Repeating the reproposal costs only gossip of the identical (already broadcast) block. Whether this is provably exploitable end-to-end (i.e., whether the fresh re-validation can actually flip to acceptance rather than reproducing the same rejection) is not fully confirmed from the available context — `check_block_against_state`/`check_proposal` chainstate checks would independently re-validate the block and could still reject it for the same underlying reason. This nuance could not be fully traced in the available index.

### Recommendation
In `should_reevaluate_block`, add an explicit early-return for `block_info.state == BlockState::GloballyRejected` symmetric to the existing `globally_approved_and_responded()` check for `GloballyAccepted`, so that a globally-rejected block is never routed to fresh evaluation regardless of `reject_reason`. Alternatively, before constructing a fresh `BlockInfo` in `handle_block_proposal`, look up the persisted state again and refuse to overwrite any row already in a terminal (`GloballyAccepted`/`GloballyRejected`) state.

### Proof of Concept
```rust
// stacks-signer/src/signerdb.rs (unit test sketch)
#[test]
fn globally_rejected_is_terminal_even_via_fresh_blockinfo_overwrite() {
    let db_path = tmp_db_path();
    let mut db = SignerDb::new(db_path).expect("db");
    let (mut block_info, block_proposal) = create_block();

    // Signer locally rejects with a re-evaluable reason, then block goes globally rejected.
    block_info.reject_reason = Some(RejectReason::ValidationFailed(ValidateRejectCode::NotFoundError));
    block_info.mark_locally_rejected().unwrap();
    block_info.mark_globally_rejected().unwrap();
    db.insert_block(&block_info).unwrap();

    // Direct guard check: this MUST fail per check_state.
    let mut same_info = block_info.clone();
    assert!(same_info.mark_globally_accepted().is_err(),
        "check_state must refuse GloballyRejected -> GloballyAccepted");

    // But should_reevaluate_reject_reason on the persisted record says "re-evaluate":
    assert!(should_reevaluate_reject_reason(&block_info));

    // Simulated handle_block_proposal "fresh evaluation" path: builds a brand-new BlockInfo
    // for the identical signer_signature_hash and walks it to GloballyAccepted legally,
    // then overwrites the DB row for the same hash.
    let mut fresh = BlockInfo::from(block_proposal.clone());
    assert_eq!(fresh.signer_signature_hash(), block_info.signer_signature_hash());
    fresh.mark_pre_committed().unwrap();
    fresh.mark_locally_accepted(false).unwrap();
    fresh.mark_globally_accepted().unwrap(); // succeeds on the fresh struct
    db.insert_block(&fresh).unwrap();        // overwrites the GloballyRejected row

    let reloaded = db.block_lookup(&block_info.signer_signature_hash()).unwrap().unwrap();
    assert_eq!(reloaded.state, BlockState::GloballyAccepted,
        "durable GloballyRejected verdict was overwritten with GloballyAccepted for the same hash");
}
```
This demonstrates the equality violation at the persistence layer: `mark_globally_accepted` on the *same* `BlockInfo` instance is correctly blocked, but the re-evaluation routing lets a *different* (fresh) `BlockInfo` instance for the identical `signer_signature_hash` reach `GloballyAccepted` and overwrite the previously durable `GloballyRejected` record.

### Citations

**File:** stacks-signer/src/signerdb.rs (L314-329)
```rust
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

**File:** stacks-signer/src/signerdb.rs (L359-363)
```rust
    /// Check if the block is globally accepted and this signer has responded to it
    pub fn globally_approved_and_responded(&self) -> bool {
        matches!(self.state, BlockState::GloballyAccepted)
            && (self.signed_self.is_some() || self.valid == Some(false))
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1483-1504)
```rust
    fn should_reevaluate_block(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        block_info: &BlockInfo,
        block_proposal: &BlockProposal,
    ) -> bool {
        let signer_signature_hash = block_info.block.header.signer_signature_hash();
        if block_info.globally_approved_and_responded() {
            info!("{self}: received a block proposal for a globally accepted block to which we have already responded. Ignoring.";
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_info.block.block_id(),
                "block_height" => block_info.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "timestamp" => block_info.block.header.timestamp,
                "signed_group" => block_info.signed_group,
                "signed_self" => block_info.signed_self,
                "valid" => ?block_info.valid
            );
            return false;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1592-1654)
```rust
        let prior_block_info = self.block_lookup_by_reward_cycle(&signer_signature_hash);
        if let Some(block_info) = &prior_block_info {
            // If we have already decided on this block, resend that decision (or ignore
            // the proposal) rather than evaluating it again.
            if !self.should_reevaluate_block(
                stacks_client,
                sortition_state,
                block_info,
                block_proposal,
            ) {
                return;
            }
        }

        if block_proposal
            .block
            .header
            .timestamp
            .saturating_add(self.block_proposal_max_age_secs)
            < get_epoch_time_secs()
        {
            // Block is too old. Reject it (without validating) rather than silently
            // dropping it: the miner's proposal loop re-sends the same block until it
            // accumulates rejection weight, so a silent drop from the whole signer set
            // would livelock the tenure until the next sortition.
            warn!("{self}: Received a block proposal that is more than {} secs old. Rejecting...", self.block_proposal_max_age_secs;
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_proposal.block.block_id(),
                "block_height" => block_proposal.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "timestamp" => block_proposal.block.header.timestamp,
            );
            let rejection =
                self.create_block_rejection(RejectReason::ProposalTooOld, &block_proposal.block);
            self.send_block_response(&block_proposal.block, rejection.into());
            return;
        }

        let pending_responses = if prior_block_info.is_some() {
            PendingBlockResponses::empty()
        } else {
            info!(
                "{self}: received a block proposal for a new block.";
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_proposal.block.block_id(),
                "block_height" => block_proposal.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "consensus_hash" => %block_proposal.block.header.consensus_hash,
            );
            self.signer_db
                .drain_pending_block_responses(&signer_signature_hash)
                .unwrap_or_else(|e| {
                    warn!(
                        "{self}: Failed to drain pending block responses for block proposal: {e:?}";
                        "signer_signature_hash" => %signer_signature_hash,
                        "block_id" => %block_proposal.block.block_id(),
                    );
                    PendingBlockResponses::empty()
                })
        };
        crate::monitoring::actions::increment_block_proposals_received();
        // Creating a new proposal will overwrite any prior proposal info on the block if it exists, e.g. validity, signed_timestamps, etc.
        let mut block_info = BlockInfo::from(block_proposal.clone());
```

**File:** stacks-signer/src/v0/signer.rs (L2706-2739)
```rust
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
