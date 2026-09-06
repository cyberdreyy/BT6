### Title
Signer skips the proposal-time miner/consensus-hash/pubkey/bitvec re-validation when signing at pre-commit threshold, allowing a stale (superseded) miner's block to be signed - (File: `stacks-signer/src/v0/signer.rs`, `stacks-signer/src/chainstate/v2.rs`)

### Summary
`GlobalStateView::check_proposal` (the check that verifies a proposed block's consensus hash, miner pubkey hash, and bitvec still match the *current* global-state view of the active miner) is only invoked once, when a block proposal first arrives. Neither the block-validation-response handler nor the pre-commit-threshold signing path re-runs it; they only call the narrower `check_block_against_signer_db_state`, which re-checks tenure-tip/parent consistency but never re-checks that the block's miner/tenure is still the one the signer set currently recognizes as active.

### Finding Description
Block signing in `stacks-signer/src/v0/signer.rs` proceeds in three stages:
1. Proposal arrival → `check_block_against_global_state` → `GlobalStateView::check_proposal`, which enforces `ConsensusHashMismatch`, `PubkeyHashMismatch`, and `InvalidBitvec` against the *current* `MinerState::ActiveMiner` derived from the global signer state machine [1](#0-0) .
2. Node validation response → `handle_block_validate_ok` re-checks only `check_block_against_signer_db_state`, not the global-state view [2](#0-1) .
3. Pre-commit threshold reached → `handle_block_pre_commit` again re-checks only `check_block_against_signer_db_state` before calling `mark_locally_accepted` and broadcasting the actual signature [3](#0-2) .

`check_block_against_signer_db_state` is explicitly documented as an *incomplete* check limited to tenure-tip continuity (`check_tenure_change_confirms_parent` / `check_latest_block_in_tenure`) [4](#0-3) . It does not re-verify the block's consensus hash/miner pubkey/bitvec against the signer's current global-state view of the active miner - those checks live only inside `GlobalStateView::check_proposal`, which is not called again on this path [5](#0-4) .

This gap is explicitly called out in the repository's own documentation of the signer state machine: "Two things belong to the proposal path only and are **not** re-run at validate-ok or at signing: ... the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the pox bitvec, and tenure-extend rules before delegating here." [6](#0-5) 

### Impact Explanation
If, between the time a block is proposed/submitted for validation and the time the pre-commit threshold (≥70% weight) is reached, the signer set's global state advances to a new active miner/tenure (e.g., a new Bitcoin sortition selects a different miner), a block that is stale relative to that new global state can still be signed: nothing on the validate-ok or pre-commit path re-checks that the block's `consensus_hash`/miner pubkey still matches the currently recognized `ActiveMiner`. The only remaining defense is the tenure-conflict logic in `handle_block_pre_commit` (`get_signed_conflicts`/`conflict_still_blocks`), which only fires once some other block has *already been signed* at that height/tenure - it does not compare against the global-state machine's view of who the active miner is. This can let a signer sign a block for a superseded miner/tenure, which is exactly the "signing a non-canonical/conflicting block" outcome the review rules classify as Critical.

### Likelihood Explanation
This requires a specific timing window: a proposal must clear the proposal-time `check_proposal` (matching the miner/state view at that moment), then a new sortition/global-state update must occur before the pre-commit threshold is reached, and no other signed sibling block yet exists to trigger the `get_signed_conflicts` guard. This is a plausible but not trivial race under normal validation latencies (`block_proposal_validation_timeout`) and is reachable by ordinary miner/sortition activity plus normal signer gossip - it does not require a majority of signers or key compromise, only the natural passage of time between proposal and pre-commit threshold.

### Recommendation
Re-run `GlobalStateView::check_proposal` (or at minimum its consensus-hash/pubkey-hash/bitvec portion) as part of `check_block_against_signer_db_state`, or explicitly re-invoke it immediately before `mark_locally_accepted` in `handle_block_pre_commit`, so that a stale block cannot be signed once the signer's global-state view has moved past its miner/tenure.

### Proof of Concept
1. Miner M wins sortition for tenure T and proposes tenure-change block B (consensus_hash = T, correct pubkey/bitvec). `check_block_against_global_state`/`GlobalStateView::check_proposal` passes against the then-current `ActiveMiner` view, and B is submitted to the node for validation.
2. Before validation completes / before 70% pre-commit weight is reached, a new Bitcoin sortition occurs and a different miner M' wins; a majority of signers update their `StateMachineUpdate` broadcasts so `GlobalStateEvaluator::determine_global_state` now reports `ActiveMiner` = M'/tenure T'.
3. The node still returns `Ok` for B (B is still a well-formed block on canonical sortition history at proposal-submission time), so `handle_block_validate_ok` runs `check_block_against_signer_db_state`, which only checks tenure-tip continuity and passes - `GlobalStateView::check_proposal` (with its `ConsensusHashMismatch`/`PubkeyHashMismatch` checks) is never re-invoked [7](#0-6) .
4. Enough signers who validated B before the sortition-change still hold pre-commits; once ≥70% pre-commit weight accumulates, `handle_block_pre_commit` again only re-runs `check_block_against_signer_db_state`, finds no `get_signed_conflicts` entry (no other block yet signed at this height), and proceeds to `mark_locally_accepted`, producing a signature over B despite the global state having already advanced to M'/T' [8](#0-7) .

### Citations

**File:** stacks-signer/src/chainstate/v2.rs (L111-163)
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
        if &block.header.consensus_hash != tenure_id {
            info!("Miner block proposal consensus hash does not match the current miner's tenure id. Considering invalid.";
                "block_height" => block.header.chain_length,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
                "block_consensus_hash" => %block.header.consensus_hash,
                "active_miner_tenure_id" => %tenure_id,
                "active_miner_parent_tenure_id" => %parent_tenure_id,
            );
            return Err(RejectReason::ConsensusHashMismatch {
                actual: block.header.consensus_hash.clone(),
                expected: tenure_id.clone(),
            });
        }
        let Some(miner_pk) = block.header.recover_miner_pk() else {
            warn!("Failed to recover miner pubkey";
                  "signer_signature_hash" => %block.header.signer_signature_hash(),
                  "consensus_hash" => %block.header.consensus_hash);
            return Err(RejectReason::IrrecoverablePubkeyHash);
        };
        let miner_pkh = Hash160::from_data(&miner_pk.to_bytes_compressed());
        if current_miner_pkh != &miner_pkh {
            warn!(
                "Miner block proposal pubkey does not match the winning pubkey hash for its sortition. Considering invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
                "proposed_block_pubkey" => &miner_pk.to_hex(),
                "proposed_block_pubkey_hash" => %miner_pkh,
                "active_miner_pubkey_hash" => %current_miner_pkh,
            );
            return Err(RejectReason::PubkeyHashMismatch);
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1340-1366)
```rust
        // The chain and signer db state may have changed materially since this block passed the
        // proposal-time checks (e.g. between validation and reaching the pre-commit threshold we
        // may have signed a block that this one would reorg). Re-run the chainstate checks
        // before putting a signature over the block, and respond with a rejection if they no
        // longer pass, just as the block validation response handler does.
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

**File:** stacks-signer/src/v0/signer.rs (L1799-1841)
```rust
    /// WARNING: This is an incomplete check. Do NOT call this function PRIOR to check_proposal or block_proposal validation succeeds.
    ///
    /// Re-verify a block's chain length against the last signed block within signerdb.
    /// This is required in case a block has been approved since the initial checks of the block validation endpoint.
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

**File:** stacks-signer/src/v0/signer.rs (L1941-1975)
```rust
        if !block_info.check_static_valid_block() {
            debug!("{self}: Block is syntatically invalid; will not store");
            return;
        }

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
```

**File:** docs/signer-flows.md (L425-434)
```markdown
Two things belong to the proposal path only and are **not** re-run at validate-ok
or at signing:

- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
- the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the
  pox bitvec, and tenure-extend rules before delegating here.

```
