### Title
Sticky `SortitionViewMismatch` rejection wedges a signer against a re-proposed block whose confirming-tip condition has since resolved - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`check_latest_block_in_tenure` in `stacks-signer/src/chainstate/mod.rs` decides whether a proposed block confirms enough of its tenure by comparing it against transient, time-varying signals: the last *signed* block in the tenure (subject to `tenure_last_block_proposal_timeout`) and the node's live tenure tip via `client.get_tenure_tip`. This can legitimately flip from "fails" to "passes" over time — the blocking signed block times out, or the node's tenure tip advances. When the check fails, the signer stores a `RejectReason::SortitionViewMismatch` and moves the block to `LocallyRejected`. `should_reevaluate_reject_reason` in `stacks-signer/src/v0/signer.rs` (lines 2706-2739) hard-codes `SortitionViewMismatch` as **not** re-evaluable, so a re-proposal of the exact same block (same `signer_signature_hash`) is answered by replaying the stale cached verdict (`determine_response`) instead of re-running `check_latest_block_in_tenure`, even though the transient condition that caused the original rejection may since have resolved.

### Finding Description
`get_tenure_last_block_info` (`stacks-signer/src/chainstate/mod.rs` lines 330-364) explicitly times out the "last signed block" it uses as a veto after `tenure_last_block_proposal_timeout` seconds: [1](#0-0) 

`check_latest_block_in_tenure` uses that possibly-stale signal, plus a live call to the node's `get_tenure_tip`, to decide `Ok(false)` (reject) vs `Ok(true)` (pass): [2](#0-1) [3](#0-2) 

Both inputs are explicitly time-dependent: the timeout window in `get_tenure_last_block_info`, and the node's tenure tip, which advances as blocks are processed. A rejection produced from this check is surfaced as `RejectReason::SortitionViewMismatch` (see `check_block_against_signer_db_state`, `stacks-signer/src/v0/signer.rs` lines 1842-1866).

When the miner later re-proposes the identical block (same `signer_signature_hash`) — an explicitly anticipated flow, per `handle_block_proposal` → `should_reevaluate_block` → `should_reevaluate_reject_reason`: [4](#0-3) 

the dispatcher first calls `should_reevaluate_reject_reason`, which hard-codes `SortitionViewMismatch` into the "no need to re-validate" bucket alongside genuinely permanent rejections like `InvalidMiner`/`PubkeyHashMismatch`: [5](#0-4) 

Because this reason is not re-evaluable, `should_reevaluate_block` takes the `else` branch: since state is `LocallyRejected` (not `PreCommitted`), it calls `determine_response(block_info)` and resends the previously cached rejection rather than recomputing `check_latest_block_in_tenure` against current chain state. The stale verdict is repeated indefinitely for that specific block hash, even after the exact time-based conditions that produced it (timeout of the blocking signed block, or the node's tenure tip advancing) have resolved.

This mirrors the reported bug class precisely: a validity condition (`_isSolverActive` / "does this block confirm enough of the tenure") is evaluated once and cached, and the cached negative verdict is never re-checked even though the underlying state that produced it is explicitly designed to change over time.

### Impact Explanation
This is a per-signer liveness wedge, not a network-wide equivocation. The affected signer will refuse to sign that specific re-proposed block forever, degrading its contribution to the group's signing weight for that block, even once the same block would legitimately pass `check_latest_block_in_tenure` again. This maps to the "High" impact bucket defined by the rules: a signer wedged into never signing a valid block for a given proposal, based on a stale/no-longer-accurate local view, until the miner produces a *new* (differently-hashed) block. It does not by itself break block-level safety (a differently-signed majority can still finalize the block), and does not require a majority of signers to trigger — it is purely a self-inflicted, permanent local rejection triggered by ordinary miner retry behavior (re-proposing the same block after a timeout), which the codebase's own tests (`signers_reprocess_late_block_proposals_signatures`, `reproposal_cannot_bypass_fresh_conflict`) show is an expected and exercised code path.

### Likelihood Explanation
Moderate-to-high likelihood in practice: `tenure_last_block_proposal_timeout`-based expiry and tenure-tip advancement are ordinary events that happen on every tenure, and miners are documented to re-propose byte-identical blocks after proposal/signature timeouts. Any signer that once computed `SortitionViewMismatch` for a given block hash under a transient condition will never re-derive the (now-different) answer for that same hash without a code path change — the miner would have to produce a distinct block (new hash) to escape the sticky verdict.

### Recommendation
Remove `RejectReason::SortitionViewMismatch` from the non-reevaluable list in `should_reevaluate_reject_reason` (`stacks-signer/src/v0/signer.rs`), or otherwise route re-proposals with this reject reason back through `check_block_against_signer_db_state`/`check_latest_block_in_tenure` before replaying the cached response, so that a re-proposal of the same block is judged against current chain state rather than a stale cached verdict. This mirrors how `RejectReason::ConnectivityIssues`, `NoSignerConsensus`, etc. are already treated as re-evaluable because their underlying condition is time/state-dependent.

### Proof of Concept
1. Miner proposes block `B` in tenure `T`. At validation time, `check_latest_block_in_tenure` returns `Ok(false)` because the tenure's last signed block has not yet timed out (`get_tenure_last_block_info` returns `Some`) or the node's `get_tenure_tip` has not yet advanced past `B`'s height.
2. The signer stores `block_info` for `B`'s hash with `RejectReason::SortitionViewMismatch`, state `LocallyRejected`.
3. Time passes: the blocking signed block's `signed_self`/`signed_group` timestamp plus `tenure_last_block_proposal_timeout` elapses, or the node's tenure tip catches up to/past `B`'s chain length — either condition would now make `check_latest_block_in_tenure` return `Ok(true)`.
4. The miner re-proposes the identical block `B` (same `signer_signature_hash`), a scenario the codebase explicitly anticipates (miner retries after a signature/proposal timeout).
5. `handle_block_proposal` → `should_reevaluate_block` → `should_reevaluate_reject_reason(block_info)` returns `false` for `SortitionViewMismatch`.
6. Since `block_info.state != PreCommitted`, the signer calls `determine_response(block_info)` and re-sends the original, now-stale `SortitionViewMismatch` rejection, without ever calling `check_latest_block_in_tenure` again — permanently rejecting a block it would otherwise now correctly sign.

Note: the exact fix commit / whether this was previously flagged and remediated could not be fully confirmed from the available index; the analysis above is based on direct reading of `stacks-signer/src/v0/signer.rs` and `stacks-signer/src/chainstate/mod.rs` in this repository snapshot.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L349-363)
```rust
        if signed_over_time.saturating_add(tenure_last_block_proposal_timeout.as_secs())
            > get_epoch_time_secs()
        {
            // The last accepted block is not timed out, return it
            Ok(Some(block_info))
        } else {
            // The last accepted block is timed out
            info!(
                "Last accepted block has timed out";
                "signer_signature_hash" => %block_info.block.header.signer_signature_hash(),
                "signed_over_time" => signed_over_time,
                "state" => %block_info.state,
            );
            Ok(None)
        }
```

**File:** stacks-signer/src/chainstate/mod.rs (L390-419)
```rust
        if let Some(info) = last_block_info {
            // N.B. this block might not be the last globally accepted block across the network;
            // it's just the highest one in this tenure that we know about.  If this given block is
            // no higher than it, then it's definitely no higher than the last globally accepted
            // block across the network, so we can do an early rejection here.
            if block.header.chain_length <= info.block.header.chain_length {
                warn!(
                    "Miner's block proposal does not confirm as many blocks as we expect";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "proposed_chain_length" => block.header.chain_length,
                    "expected_at_least" => info.block.header.chain_length + 1,
                );
                if info.signed_group.is_none_or(|signed_time| {
                    signed_time + reorg_attempts_activity_timeout.as_secs() > get_epoch_time_secs()
                }) {
                    // Note if there is no signed_group time, this is a locally accepted block (i.e. tenure_last_block_proposal_timeout has not been exceeded).
                    // Treat any attempt to reorg a locally accepted block as valid miner activity.
                    // If the call returns a globally accepted block, check its globally accepted time against a quarter of the block_proposal_timeout
                    // to give the miner some extra buffer time to wait for its chain tip to advance
                    // The miner may just be slow, so count this invalid block proposal towards valid miner activity.
                    if let Err(e) = signer_db.update_last_activity_time(
                        &block.header.consensus_hash,
                        get_epoch_time_secs(),
                    ) {
                        warn!("Failed to update last activity time: {e}");
                    }
                }
                return Ok(false);
            }
```

**File:** stacks-signer/src/chainstate/mod.rs (L450-477)
```rust
        let tip = match client.get_tenure_tip(tenure_id) {
            Ok(tip) => tip.anchored_header,
            Err(e) => {
                warn!(
                    "Failed to fetch the tenure tip for the parent tenure: {e:?}. Assuming proposal is higher than the parent tenure for now.";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "parent_tenure" => %tenure_id,
                );
                return Ok(true);
            }
        };
        if let Some(nakamoto_tip) = tip.as_stacks_nakamoto() {
            // If we have seen this block already, make sure its state is updated to globally accepted.
            // Otherwise, don't worry about it.
            if let Ok(Some(mut block_info)) =
                signer_db.block_lookup(&nakamoto_tip.signer_signature_hash())
            {
                if block_info.state != BlockState::GloballyAccepted {
                    if let Err(e) = block_info.mark_globally_accepted() {
                        warn!("Failed to mark block as globally accepted: {e}");
                    } else if let Err(e) = signer_db.insert_block(&block_info) {
                        warn!("Failed to update block info in db: {e}");
                    }
                }
            }
        }
        Ok(tip.height() < block.header.chain_length)
```

**File:** stacks-signer/src/v0/signer.rs (L1481-1529)
```rust
    /// Determine if an already tracked block should be re-evaluated based on a new block proposal for it.
    /// Returns true if the block should be re-evaluated, false if it should be ignored.
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

**File:** stacks-signer/src/v0/signer.rs (L2705-2738)
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
```
