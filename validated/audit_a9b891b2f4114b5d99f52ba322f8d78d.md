### Title
Terminal caching of `RejectReason::ReorgNotAllowed` prevents a signer from ever re-validating a re-proposed block whose parent-tenure choice becomes valid again - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
This is a plausible analog to the Zaros bug class ("a check is always enforced with its original strict verdict, even when the underlying condition that produced it has since changed, blocking a legitimate action"). Here, a signer's `ReorgNotAllowed` rejection for a specific block proposal is treated as a **permanent, non-reevaluable** verdict, even though the condition it is based on (`check_parent_tenure_choice`, which is itself time/view-dependent) can legitimately flip from invalid to valid for the same, byte-identical, re-proposed block.

### Finding Description
`SortitionsView::check_proposal` in `stacks-signer/src/chainstate/v1.rs` returns `RejectReason::ReorgNotAllowed` in two places when `check_parent_tenure_choice` decides the sortition did not correctly build off the canonical tip: [1](#0-0) [2](#0-1) 

`check_parent_tenure_choice` is explicitly time-bounded (`first_proposal_burn_block_timing`), meaning its answer for the very same block proposal can change as burn blocks arrive and the canonical view stabilizes.

Once this rejection is recorded, `should_reevaluate_reject_reason` classifies `ReorgNotAllowed` as **not** worth re-validating: [3](#0-2) 

Consequently, when the same block (same `signer_signature_hash`) is re-proposed later — e.g., after the miner's proposal-retry logic resubmits it — `should_reevaluate_block` takes the "reason not reevaluable" branch and, since the state is not `PreCommitted`, calls `determine_response` and re-sends the **stale** previous rejection without ever re-running `check_block_against_state` / `check_proposal` again: [4](#0-3) 

This is in stark contrast to the deliberate, self-correcting staleness machinery built for signature conflicts (`reorg_permit_stands`, `conflict_still_blocks`, freshness cutoffs) documented for the pre-commit path, which exists precisely because a stale verdict must not be allowed to persist once the fact it depended on is no longer true: [5](#0-4) 

No equivalent re-evaluation exists for a locally-rejected (non-`PreCommitted`) block with a "terminal" reject reason like `ReorgNotAllowed`.

### Impact Explanation
This matches the High-impact class "a signer wedged into never signing valid blocks." A single signer that once judged a specific block's parent-tenure choice as `ReorgNotAllowed` can never revise that verdict for that exact block, even if the same block is later legitimately the canonical continuation (e.g., the tip view catches up or the timing window that made the parent tenure look invalid resolves in the miner's favor). That signer's weight is permanently lost for this block, degrading the signer set's effective participation and potentially delaying or preventing the 70% signature threshold if enough signers are similarly and independently wedged around the same reorg event.

### Likelihood Explanation
This is a one-signer-local, config/timing-driven condition — no majority collusion is required to trigger it, and it can arise from ordinary burn-chain jitter around a tenure boundary (the exact scenario `first_proposal_burn_block_timing` and `reorg_attempts_activity_timeout` were introduced to handle). It requires: (1) a signer rejects a proposal with `ReorgNotAllowed`, and (2) the same proposal (same signature hash) is re-broadcast to that signer after the parent-tenure-choice condition would now evaluate differently. Re-proposal of the *same* block is a normal occurrence (timeout-triggered resend), making the likelihood moderate rather than purely theoretical.

### Recommendation
Either (a) remove `ReorgNotAllowed` from the "no need to re-validate" list in `should_reevaluate_reject_reason` so re-proposals are re-run through `check_proposal`/`check_parent_tenure_choice`, or (b) apply the same freshness/staleness self-correction pattern used for signed conflicts (`reorg_permit_stands`/`conflict_still_blocks`) to locally-rejected blocks with time-dependent reject reasons, so a stale `ReorgNotAllowed` judgment does not permanently block a signer from later signing a legitimately canonical block.

### Proof of Concept
Conceptual trace (integration-test style, analogous to existing tests in `stacks-signer/src/v0/tests.rs` and `stacks-node/src/tests/signer/v0/reorg.rs`):
1. Miner proposes block B during a tenure whose parent-tenure choice does not yet look canonical to signer S within `first_proposal_burn_block_timing` → `check_parent_tenure_choice` returns false → S rejects B with `RejectReason::ReorgNotAllowed`, stored via `mark_locally_rejected`.
2. Burn chain state stabilizes (further burn blocks are relayed) such that, were `check_parent_tenure_choice` re-run now, it would return true for the same parent tenure.
3. Miner's proposal-retry logic re-broadcasts the identical block B (same `signer_signature_hash`).
4. `handle_block_proposal` → `should_reevaluate_block` sees `block_info.reject_reason == Some(ReorgNotAllowed)`, `should_reevaluate_reject_reason` returns `false`, state is not `PreCommitted`, so `determine_response` resends the original stale rejection — `check_proposal`/`check_parent_tenure_choice` is never invoked again for B by S, even though it would now pass.

This can be verified by instrumenting `check_parent_tenure_choice` to flip its answer between step 1 and step 3 in a unit test built on the harness in `stacks-signer/src/v0/tests.rs`, and observing that S's second response is still `Rejected(ReorgNotAllowed)` rather than a fresh acceptance/pre-commit.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L180-201)
```rust
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
```

**File:** stacks-signer/src/v0/signer.rs (L1505-1533)
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
            } else {
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

**File:** docs/signer-flows.md (L274-297)
```markdown
Order matters here: the chainstate re-check runs first and produces an explicit
(sticky) rejection when the block now conflicts with a signed one. The conflict
guard behind it is the silent backstop for what that re-check cannot see, and
silence keeps the door open to sign later once the conflict goes stale. Two
blind spots make the guard necessary:

- the re-check only ever looks at _one_ tenure (a tenure-change block's parent,
  or any other block's own), so a signed sibling at the same height in a third
  tenure is invisible to it;
- the `DuplicateBlockFound` check that would catch a second block in the same
  tenure lives in `check_proposal` and runs only at proposal arrival, never
  again. A block that crosses the pre-commit threshold minutes later has no
  other guard, which is what the own-tenure branch above covers.

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
```
