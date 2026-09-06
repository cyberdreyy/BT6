### Title
Miner-invalidation (`SortitionMinerStatus`/`InvalidMiner`) check is proposal-only and never re-run before a signer produces its signature - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`SortitionsView::check_proposal` (v1) and `GlobalStateView::check_proposal` (v2) are the only places that verify a proposed block's miner is still the **valid** current/last sortition winner (`SortitionMinerStatus::Valid` / `MinerState::ActiveMiner`). Once a block passes that gate it is stored and later moved toward a signature purely through `check_block_against_signer_db_state`, which re-checks only tenure-confirmation (`check_latest_block_in_tenure`/`check_tenure_change_confirms_parent`) and never re-consults miner validity. If the miner is invalidated *after* the block was screened but *before* the async node validation returns or the pre-commit threshold is reached, the signer still signs.

### Finding Description
`check_proposal` in `stacks-signer/src/chainstate/v1.rs` explicitly gates signing on miner status: [1](#0-0) 
and can mark a sortition `InvalidatedBeforeFirstBlock`/`InvalidatedAfterFirstBlock` mid-flight (e.g. timeout, bad parent-tenure choice): [2](#0-1) 
The v2 equivalent similarly requires `MinerState::ActiveMiner`, rejecting with `InvalidMiner` otherwise: [3](#0-2) 

Crucially, `capitulate_viewpoint` (run on every event-loop pass, independent of any specific proposal) can flip `sortition_state.cur_sortition.miner_status` to `InvalidatedBeforeFirstBlock` the moment the global-state view disagrees with the local sortition's miner pubkey hash: [4](#0-3) 

But the two paths that actually produce a signature — `handle_block_validate_ok` (validate-ok → pre-commit) and `handle_block_pre_commit` (pre-commit-threshold → sign) — only call `check_block_against_signer_db_state`, which does not take a `SortitionsView`/miner-status parameter at all and only re-runs the tenure-confirmation logic: [5](#0-4) [6](#0-5) [7](#0-6) 

The project's own documentation confirms this design gap for a sibling case (the tenure-change duplicate check), explicitly stating certain proposal-time-only checks are "not re-run at validate-ok or at signing": [8](#0-7) 
Unlike the duplicate-block case (which the docs argue is compensated by the section-5 own-tenure conflict guard), no equivalent compensating check exists for `miner_status`/`MinerState::ActiveMiner` re-validation — `check_block_against_signer_db_state` never inspects it, and `handle_block_pre_commit`'s conflict logic only reasons about *other signed blocks*, not about whether the *current* block's own miner has since been invalidated.

This is the structural analog of the reported Vikunja bug: a security-relevant check (TOTP requirement / here, "is this miner still valid") is enforced on one entry path (local login / initial `check_proposal`) but is silently skipped on another path that reaches the same privileged outcome (OIDC callback / here, `handle_block_validate_ok` → `handle_block_pre_commit` → signature) after the same identity/block record is reused.

### Impact Explanation
If a signer produces `mark_locally_accepted` (a real signature) for a block whose miner was already flagged invalid (timed out, wrong parent-tenure choice, or equivocation) between proposal screening and signature time, that signature is data other signers and the node will count toward the 70% signing threshold for a block from a miner the signer's own state machine has already condemned. This directly breaks the "a signer signing an invalid/non-canonical block" safety property this repo's threat model is built to prevent (Critical impact bucket per the rules): the signature is produced for a tenure/block the signer's local logic itself considers illegitimate, and unlike node-side validation (`postblock_proposal.rs`), the node does not independently re-derive `SortitionMinerStatus`/`MinerState`—that is exclusively signer-local state.

### Likelihood Explanation
This requires only the ordinary asynchronous timing that already exists in the pipeline (node validation round-trip, or the pre-commit-threshold gathering window) plus a state-machine event that a single miner or normal chain conditions can trigger without any signer collusion:
- a burn-block timeout marking the current sortition `InvalidatedBeforeFirstBlock` (`is_timed_out`/`block_proposal_timeout`),
- a parent-tenure-choice mismatch (`check_parent_tenure_choice` failing) after the fact,
- or a `capitulate_viewpoint` mismatch between the local sortition's `miner_pkh` and the newly-adopted global-state `current_miner_pkh`.

These all update `miner_status`/`current_miner` asynchronously relative to the in-flight block's validation/pre-commit lifecycle, and none of them require a majority of signers, another signer's key, or node/API access — a single miner racing its own proposal against the timeout/parent-choice windows can trigger the divergence.

### Recommendation
Re-validate miner legitimacy at the same points `check_block_against_signer_db_state` is called (`handle_block_validate_ok` and at the pre-commit-threshold crossing in `handle_block_pre_commit`), not only at initial `check_proposal`. Concretely: thread the current `SortitionsView`/`GlobalStateView` miner state into `check_block_against_signer_db_state`, and treat a `miner_status != Valid` (v1) or non-`ActiveMiner` / mismatched `current_miner_pkh` (v2) the same way a failed tenure-confirmation check is treated today — reject with `InvalidMiner` and call `mark_locally_rejected` instead of proceeding to `mark_pre_committed`/`mark_locally_accepted`.

### Proof of Concept
Deterministic repro sketch, using the existing test harness patterns in `stacks-signer/src/chainstate/tests/v1.rs` (`check_proposal_invalid_status`) and `stacks-signer/src/v0/tests.rs` (`run_sibling_scenario`):

1. Miner M wins sortition S and proposes tenure-start block B. `handle_block_proposal` runs `check_proposal`, which (at this moment) sees `miner_status == Valid`, passes, and submits B to the node for validation (`submit_block_for_validation`), storing `BlockInfo` for B.
2. Before the node's async validation response arrives, the signer's event loop runs `capitulate_viewpoint` (or the sortition times out per `is_timed_out`), setting `sortition_state.cur_sortition.miner_status = InvalidatedBeforeFirstBlock` (as in `signer_state.rs:964-974` or `chainstate/v1.rs:144-203`)—simulating a Bitcoin-chain condition where M is no longer the legitimate miner for S.
3. The node's validation response for B (`BlockValidateOk`) now arrives and is processed by `handle_block_validate_ok`. This calls `check_block_against_signer_db_state(stacks_client, &block_info.block)`, which only checks tenure-confirmation, not `miner_status`, so it returns `None` (no rejection) and the signer proceeds to `mark_pre_committed` and broadcasts a pre-commit.
4. Enough peers' pre-commits accumulate to cross the 70% threshold; `handle_block_pre_commit` re-runs `check_block_against_signer_db_state` (same gap) and, finding no conflicting *signed* block, calls `mark_locally_accepted` and broadcasts a real signature for B — despite the signer's own state machine having independently concluded (in step 2) that M's tenure is invalid.

This can be verified by instrumenting `check_proposal_invalid_status` (`stacks-signer/src/chainstate/tests/v1.rs:357-419`) to run *after* a block has already been stored/pre-committed via the `handle_block_validate_ok`/`handle_block_pre_commit` code path (as done in `run_sibling_scenario`, `stacks-signer/src/v0/tests.rs:603+`), then asserting the resulting `BlockInfo.state` reaches `LocallyAccepted` even though `cur_sortition.miner_status` was flipped to `InvalidatedBeforeFirstBlock`/`InvalidatedAfterFirstBlock` mid-flight. I was not able to execute this test in this environment (read-only code index) — it should be run in a live checkout to confirm the exact observable state transition and any incidental compensations from unrelated timeout/tenure-tip checks that I could not fully rule out from static reading alone.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L144-203)
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
        }
```

**File:** stacks-signer/src/chainstate/v1.rs (L288-316)
```rust
        // check that this miner is the most recent sortition
        match proposed_by {
            ProposedBy::CurrentSortition(sortition) => {
                if sortition.miner_status != SortitionMinerStatus::Valid {
                    warn!(
                        "Current miner behaved improperly, this signer views the miner as invalid.";
                        "proposed_block_consensus_hash" => %block.header.consensus_hash,
                        "signer_signature_hash" => %block.header.signer_signature_hash(),
                        "current_sortition_miner_status" => ?sortition.miner_status,
                    );
                    return Err(RejectReason::InvalidMiner);
                }
            }
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

**File:** stacks-signer/src/chainstate/v2.rs (L111-132)
```rust
impl GlobalStateView {
    /// Apply checks from the signer state machine on the block proposal.
    pub fn check_proposal(
        &self,
        client: &StacksClient,
        signer_db: &mut SignerDb,
        block: &NakamotoBlock,
    ) -> Result<(), RejectReason> {
        let MinerState::ActiveMiner {
            current_miner_pkh,
            tenure_id,
            parent_tenure_id,
            ..
        } = &self.signer_state.current_miner
        else {
            info!(
                "No valid current miner. Considering invalid.";
                "block_height" => block.header.chain_length,
                "signer_signature_hash" => %block.header.signer_signature_hash()
            );
            return Err(RejectReason::InvalidMiner);
        };
```

**File:** stacks-signer/src/v0/signer_state.rs (L964-974)
```rust
            match new_miner {
                StateMachineUpdateMinerState::ActiveMiner {
                    current_miner_pkh, ..
                } => {
                    if let Some(sortition_state) = sortition_state {
                        // if there is a mismatch between the new_miner ad the current sortition view, mark the current miner as invalid
                        if current_miner_pkh != sortition_state.cur_sortition.data.miner_pkh {
                            sortition_state.cur_sortition.miner_status =
                                SortitionMinerStatus::InvalidatedBeforeFirstBlock
                        }
                    }
```

**File:** stacks-signer/src/v0/signer.rs (L1345-1366)
```rust
        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
        {
            warn!(
                "{self}: Reached the pre-commit threshold for a block, but it no longer passes the chainstate checks. Rejecting.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "reject_code" => %block_rejection.reason_code,
                "reject_reason" => &block_rejection.reason,
            );
            if let Err(e) = block_info.mark_locally_rejected() {
                if !block_info.has_reached_consensus() {
                    warn!("{self}: Failed to mark block as locally rejected: {e:?}");
                }
            };
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.handle_block_rejection(&block_rejection, sortition_state);
            self.send_block_response(&block_info.block, block_rejection.into());
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1803-1841)
```rust
    fn check_block_against_signer_db_state(
        &mut self,
        stacks_client: &StacksClient,
        proposed_block: &NakamotoBlock,
    ) -> Option<BlockRejection> {
        let signer_signature_hash = proposed_block.header.signer_signature_hash();
        // If this is a tenure change block, ensure that it confirms the correct number of blocks from the parent tenure.
        if let Some(tenure_change) = proposed_block.get_tenure_change_tx_payload() {
            // Ensure that the tenure change block confirms the expected parent block
            match SortitionData::check_tenure_change_confirms_parent(
                tenure_change,
                proposed_block,
                &mut self.signer_db,
                stacks_client,
                self.proposal_config.tenure_last_block_proposal_timeout,
                self.proposal_config.reorg_attempts_activity_timeout,
            ) {
                Ok(true) => return None,
                Ok(false) => {
                    return Some(self.create_block_rejection(
                        RejectReason::SortitionViewMismatch,
                        proposed_block,
                    ))
                }
                Err(e) => {
                    warn!("{self}: Error checking block proposal: {e}";
                        "signer_signature_hash" => %signer_signature_hash,
                        "block_id" => %proposed_block.block_id()
                    );
                    return Some(self.create_block_rejection(
                        RejectReason::ConnectivityIssues(
                            "error checking block proposal".to_string(),
                        ),
                        proposed_block,
                    ));
                }
            }
        }

```

**File:** stacks-signer/src/v0/signer.rs (L1946-1984)
```rust
        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
        {
            // The signer db state has changed. We no longer view this block as valid. Override the validation response.
            if let Err(e) = block_info.mark_locally_rejected() {
                if !block_info.has_reached_consensus() {
                    warn!("{self}: Failed to mark block as locally rejected: {e:?}");
                }
            };
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.handle_block_rejection(&block_rejection, sortition_state);
            self.send_block_response(&block_info.block, block_rejection.into());
        } else {
            if let Err(e) = block_info.mark_pre_committed() {
                // The block may have reached enough signatures before we validated the block so should fail to mark pre-committed
                // but still call to make sure the timestamps and validity are updated correctly.
                if !block_info.has_reached_consensus()
                    && block_info.state != BlockState::LocallyAccepted
                {
                    warn!("{self}: Failed to mark block as approved: {e:?}",);
                    return;
                }
            }

            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.send_block_pre_commit(signer_signature_hash.clone());
            // have to save the signature _after_ the block info
            let address = self.stacks_address.clone();
            self.handle_block_pre_commit(
                stacks_client,
                sortition_state,
                &address,
                signer_signature_hash,
            );
        }
```

**File:** docs/signer-flows.md (L425-437)
```markdown
Two things belong to the proposal path only and are **not** re-run at validate-ok
or at signing:

- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
- the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the
  pox bitvec, and tenure-extend rules before delegating here.

Because the duplicate check never runs again, a block that crosses the pre-commit
threshold long after it was proposed relies on section 5's own-tenure conflict
guard to cover the same ground.
```
