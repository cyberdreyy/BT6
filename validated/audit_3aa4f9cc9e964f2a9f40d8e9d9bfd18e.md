### Title
Rejections with `ValidateRejectCode::NoSuchTenure` are miscategorized as terminal, wedging the signer against re-signing a later-valid block - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`should_reevaluate_reject_reason` decides whether a previously-rejected block proposal is worth re-checking when re-proposed, versus being short-circuited with the cached rejection. Only two `ValidateRejectCode` variants (`UnknownParent`, `NotFoundError`) are whitelisted to trigger re-evaluation; every other `ValidateRejectCode`, including `NoSuchTenure`, falls into the wildcard `ValidateRejectCode::_ => false` arm and is treated as permanently settled, exactly analogous to the GMX bug where a plain, uncategorized revert (negative price) wasn't recognized by `isEmptyPriceError()` and so wasn't treated as retryable. [1](#0-0) 

### Finding Description
The node's `/v3/block_proposal` validation path returns `ValidateRejectCode::NoSuchTenure` ("Failed to find sortition for block tenure") specifically when the sortition tip for the block's burn view cannot be found yet in the node's sortition DB — a state that is transient and depends purely on how far the local node has synced the burnchain, not on the block itself being invalid. [2](#0-1) 

The other whitelisted code, `UnknownParent`, is emitted from the structurally identical situation just above it — the parent Nakamoto header not (yet) being known to chainstate — and that one *is* included in the re-evaluation whitelist: [3](#0-2) 

But `NoSuchTenure` is omitted from the `should_reevaluate_reject_reason` match's `true` arms and instead absorbed into the blanket `RejectReason::ValidationFailed(_) => false` branch alongside genuinely-terminal reasons like `InvalidBlock`/`BadTransaction`: [4](#0-3) 

When `should_reevaluate_block` returns `false`, `handle_block_proposal` returns immediately without ever re-submitting the block for validation or re-checking chainstate — it just resends the cached rejection: [5](#0-4) [6](#0-5) 

So a signer that is momentarily behind on burnchain sync (its own node hasn't yet indexed the relevant sortition) will validate a legitimate proposal, get `NoSuchTenure`, and permanently freeze that verdict for that `signer_signature_hash`. Once the node catches up (seconds later, as burn blocks propagate), the block would validate `Ok`, but this signer will never resubmit it — every reproposal of the identical block short-circuits straight back to the stale `NoSuchTenure` rejection instead of being reprocessed, unlike what happens with `UnknownParent`/`NotFoundError`, which the code explicitly designed to be revalidated (compare with the `missing_burn_block_proposal.rs` test that documents exactly this re-evaluation behavior for `NotFoundError`). [7](#0-6) 

### Impact Explanation
This is a liveness wedge on a single signer: that signer is permanently prevented from ever signing a specific valid block once it experiences a temporary sortition-lag at the moment of first validation, matching the rule's High-impact criterion "a signer wedged into never signing valid blocks." If enough signers hit this race independently (e.g. right after a burn block, before their nodes have indexed the new sortition), the affected block/tenure can fail to reach signing threshold even though it is fully valid, causing a stalled tenure until the miner gives up and starts a new one — the direct functional analog of the GMX report where a legitimate, previously-submitted order was permanently canceled because a specific negative-price error path wasn't included in the "retry-able" classification.

### Likelihood Explanation
`NoSuchTenure` is reached purely by timing: any node whose sortition DB indexing lags slightly behind the miner's broadcast of a block proposal referencing a very recent burn view will hit this path via `SortitionDB::get_block_snapshot_consensus` returning `None`. This does not require a malicious actor, a majority of signers, or any special access — a single miner (one slot) racing tenure-start block proposals against sortition indexing naturally triggers it, and no code path currently corrects the classification.

### Recommendation
Add `RejectReason::ValidationFailed(ValidateRejectCode::NoSuchTenure)` to the `true` (re-evaluate) arm of `should_reevaluate_reject_reason` in `stacks-signer/src/v0/signer.rs`, alongside `UnknownParent` and `NotFoundError`, since it represents the same class of "we don't yet have enough local chainstate/sortition data to judge" rather than a definitive invalidity verdict.

### Proof of Concept
1. A miner proposes a tenure-change block whose burn view consensus hash corresponds to a burn block the miner's node has already indexed but signer S's paired node has not yet indexed.
2. Signer S submits the proposal to its node's `/v3/block_proposal`; `NakamotoBlockProposal::validate` fails to find `sort_tip` and returns `BlockValidateRejectReason { reason_code: ValidateRejectCode::NoSuchTenure, .. }` (`stackslib/src/net/api/postblock_proposal.rs:589-595`).
3. Signer S's `handle_block_validate_reject` stores this as the block's `reject_reason` and broadcasts the rejection.
4. Signer S's node catches up moments later and would now validate the same block `Ok`.
5. The miner (or StackerDB relay) re-broadcasts the identical, now-valid, block proposal.
6. `handle_block_proposal` looks up the existing `block_info`, calls `should_reevaluate_block` → `should_reevaluate_reject_reason`, which matches `RejectReason::ValidationFailed(ValidateRejectCode::NoSuchTenure)` against the wildcard arm and returns `false` (`stacks-signer/src/v0/signer.rs:2719-2734`).
7. `handle_block_proposal` returns without resubmitting the block for validation (`stacks-signer/src/v0/signer.rs:1596-1604`); signer S resends the stale `NoSuchTenure` rejection forever for this block, even though the block is now provably valid on that same node.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1540-1572)
```rust
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
        } else {
            info!(
                "{self}: received a block proposal for this block before, but our rejection reason allows us to reconsider";
                "reject_reason" => ?block_info.reject_reason,
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_proposal.block.block_id(),
                "block_height" => block_proposal.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "consensus_hash" => %block_proposal.block.header.consensus_hash
            );
        }
        true
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1591-1604)
```rust
        let signer_signature_hash = block_proposal.block.header.signer_signature_hash();
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

**File:** stackslib/src/net/api/postblock_proposal.rs (L577-585)
```rust
        let parent_stacks_header = NakamotoChainState::get_block_header(
            chainstate.db(),
            &self.block.header.parent_block_id,
        )?
        .ok_or_else(|| BlockValidateRejectReason {
            reason_code: ValidateRejectCode::UnknownParent,
            reason: "Unknown parent block".into(),
            failed_txid: None,
        })?;
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L587-595)
```rust
        let burn_view_consensus_hash =
            NakamotoChainState::get_block_burn_view(sortdb, &self.block, &parent_stacks_header)?;
        let sort_tip =
            SortitionDB::get_block_snapshot_consensus(sortdb.conn(), &burn_view_consensus_hash)?
                .ok_or_else(|| BlockValidateRejectReason {
                    reason_code: ValidateRejectCode::NoSuchTenure,
                    reason: "Failed to find sortition for block tenure".to_string(),
                    failed_txid: None,
                })?;
```

**File:** stacks-node/src/tests/signer/v0/missing_burn_block_proposal.rs (L39-67)
```rust
/// Test that when a block proposal contains a TenureChange referencing an
/// unknown burn view consensus hash or one with `pox_valid = 0`, all
/// signers reject it with `ValidationFailed(NotFoundError)` and will
/// reprocess (not short-circuit) the proposal if it is reproposed.
///
/// Test Setup:
/// The test spins up 5 Stacks signers, one miner Nakamoto node, and a
/// corresponding bitcoind instance. The node is advanced to the Epoch 3.0
/// boundary to allow block signing.
///
/// Test Execution:
/// 1. The miner mines a burn block to start a new tenure.
/// 2. The resulting block proposal is intercepted before signers process it.
/// 3. The TenureChange transaction is modified to reference a bogus
///    burn_view_consensus_hash (one that does not exist in the sortition DB).
/// 4. The transaction Merkle root and miner signature are recomputed.
/// 5. The modified block is proposed to the signers.
/// 6. All signers reject the block during validation with
///    `Chainstate Error: Not found`.
/// 7. The same modified block is reproposed.
/// 8. Signers revalidate the proposal and reject it again with the same
///    `NotFoundError` (rather than returning `RejectedInPriorRound`).
///
/// Test Assertion:
/// - All signers reject the modified block with
///   `ValidationFailed(NotFoundError)`.
/// - Upon reproposal, the block is fully revalidated and rejected again
///   with the same error.
/// - The rejection is treated as re-evaluable rather than terminal.
```
