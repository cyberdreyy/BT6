### Title
Missing re-verification of `check_parent_tenure_choice` in `check_block_against_signer_db_state` allows a stale reorg-legitimacy decision to be signed - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`check_block_against_signer_db_state`, invoked from `handle_block_validate_ok` after the node returns `BlockValidateOk`, only re-runs `SortitionData::check_tenure_change_confirms_parent`/`check_latest_block_in_tenure` and never re-runs `SortitionData::check_parent_tenure_choice`. If the legitimacy of the proposal's tenure reorg changes between the time `check_proposal`/`check_block_against_state` first evaluated it and the time the delayed node validation response arrives, the signer will pre-commit and ultimately sign the block without re-checking whether the parent-tenure choice is still an allowed reorg.

### Finding Description
The comment at `stacks-signer/src/v0/signer.rs:1799-1803` explicitly labels the function as an incomplete re-check, valid only as a supplement to a prior full `check_proposal` pass: [1](#0-0) 

Its body only calls the two tenure-confirmation helpers: [2](#0-1) 

Neither of those helpers touches `check_parent_tenure_choice`; that check lives only in `SortitionData::check_parent_tenure_choice`, which is called from `SortitionState::is_tenure_valid` (used by the original `check_proposal`/`check_block_against_state` path), and is not called anywhere in `check_block_against_signer_db_state`: [3](#0-2) 

`check_parent_tenure_choice` is a signer-only policy layered on top of raw chainstate validity: it forbids a miner from reorging a tenure that the signer(s) have already locally/globally accepted more than one block for, and it explicitly notes that the node itself may not know about locally-signed-but-not-yet-broadcast blocks in the reorged tenure: [4](#0-3) [5](#0-4) 

Because the node's own block-validation endpoint has no visibility into the signer's local acceptance bookkeeping, `BlockValidateOk` can legitimately be returned for a block that reorgs a tenure the signer set had already advanced past — the exact scenario `check_parent_tenure_choice` exists to catch. The call sequence is:

`handle_block_proposal` → `check_block_against_state` (full `check_proposal`, includes `check_parent_tenure_choice`) → `submit_block_for_validation` → (asynchronous gap where sortition/tenure-acceptance state can change) → node `BlockValidateOk` → `handle_block_validate_ok` → `check_block_against_signer_db_state` (only tenure-confirmation checks) → `mark_pre_committed`/`send_block_pre_commit` → eventual signature. [6](#0-5) 

If the reorg-legitimacy verdict flips during the validation-pending window (e.g., another block becomes globally accepted in the reorged tenure, or the burnchain fork state changes), the abbreviated second-stage check has no path to catch it, and the signer proceeds to pre-commit/sign.

### Impact Explanation
This breaks the canonicity safety property: a signer can sign (or pre-commit toward signing) a block whose parent-tenure choice violates the signer's own reorg-legitimacy policy, potentially re-endorsing a chain history that overrides an already multiply-accepted tenure. This matches the Critical category (signing a non-canonical/conflicting block, chain safety).

### Likelihood Explanation
The trigger condition is a change in the reorg-legitimacy verdict for the specific tenure between the initial `check_proposal` pass and the arrival of the delayed `BlockValidateOk`. This requires either an actual burnchain reorg, or a state change in `signer_db`'s globally-accepted-block bookkeeping for the reorged tenure, to occur specifically during the validation-pending window — a race condition rather than a fully attacker-controlled trigger, since the attacker (a single miner slot) cannot directly force a Bitcoin-level reorg. This lowers the practical repeatability compared to a purely message-crafting exploit, though the underlying code gap (omission of `check_parent_tenure_choice`) is deterministic and reproducible once the race window is hit.

### Recommendation
Re-run `SortitionData::check_parent_tenure_choice` (or the full `is_tenure_valid`) inside `check_block_against_signer_db_state`/`handle_block_validate_ok` before pre-committing/signing, using a freshly fetched `SortitionsView`, rather than relying solely on the tenure-confirmation subset.

### Proof of Concept
A Rust test in `stacks-signer/src/v0/tests.rs` would need to: (1) drive a signer through `handle_block_proposal` for a tenure-change block so `check_proposal` passes and the block is submitted for validation; (2) before delivering `BlockValidateOk`, mutate the signer's `SignerDb` state (e.g., mark an additional block globally accepted in the reorged tenure) so that a fresh `check_parent_tenure_choice` call would return `Ok(false)`; (3) deliver `BlockValidateOk` and assert that `check_block_against_signer_db_state` currently returns `None` (bug) instead of a rejection, then flip the fix in and assert a `BlockRejection` with `RejectReason::SortitionViewMismatch` is produced instead.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1799-1803)
```rust
    /// WARNING: This is an incomplete check. Do NOT call this function PRIOR to check_proposal or block_proposal validation succeeds.
    ///
    /// Re-verify a block's chain length against the last signed block within signerdb.
    /// This is required in case a block has been approved since the initial checks of the block validation endpoint.
    fn check_block_against_signer_db_state(
```

**File:** stacks-signer/src/v0/signer.rs (L1810-1850)
```rust
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

        // Ensure that the block is the last block in the chain of its current tenure.
        match SortitionData::check_latest_block_in_tenure(
            &proposed_block.header.consensus_hash,
            proposed_block,
            &mut self.signer_db,
            stacks_client,
            self.proposal_config.tenure_last_block_proposal_timeout,
            self.proposal_config.reorg_attempts_activity_timeout,
        ) {
```

**File:** stacks-signer/src/v0/signer.rs (L1941-1984)
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

**File:** stacks-signer/src/chainstate/mod.rs (L163-169)
```rust
    /// A permitted reorg is recorded once the whole reorg is permitted: each tenure whose
    /// blocks this one is allowed to replace is marked superseded (see
    /// [`SignerDb::mark_tenure_superseded`]), so a signature we already placed on one of those
    /// blocks does not later block the replacement. The record carries this tenure's sortition
    /// as the permitting one, so the permit stops applying if a burnchain fork later orphans
    /// it. Nothing is recorded for a refused reorg, even for the tenures in it that
    /// individually qualified.
```

**File:** stacks-signer/src/chainstate/mod.rs (L210-223)
```rust
            // disallow reorg if more than one block has already been signed
            let globally_accepted_blocks =
                signer_db.get_globally_accepted_block_count_in_tenure(&tenure.consensus_hash)?;
            if globally_accepted_blocks > 1 {
                warn!(
                    "Miner is not building off of most recent tenure, but a tenure they attempted to reorg has already more than one globally accepted block.";
                    "parent_tenure" => %self.parent_tenure_id,
                    "last_sortition" => %self.prior_sortition,
                    "violating_tenure_id" => %tenure.consensus_hash,
                    "violating_tenure_first_block_id" => ?tenure.first_block_mined,
                    "globally_accepted_blocks" => globally_accepted_blocks,
                );
                return Ok(false);
            }
```

**File:** stacks-signer/src/chainstate/mod.rs (L581-606)
```rust
    pub fn is_tenure_valid(
        &self,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        proposal_config: &ProposalEvalConfig,
        eval: &GlobalStateEvaluator,
    ) -> Result<bool, SignerChainstateError> {
        let data = self.data();
        let chose_good_parent = data.check_parent_tenure_choice(
            signer_db,
            client,
            &proposal_config.first_proposal_burn_block_timing,
        )?;
        if !chose_good_parent {
            return Ok(false);
        }
        Self::is_timed_out(
            &self.version(),
            &data.consensus_hash,
            signer_db,
            client.get_signer_address(),
            proposal_config,
            eval,
        )
        .map(|timed_out| !timed_out)
    }
```
