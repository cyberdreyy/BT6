### Title
Signer re-validates tenure/chain-length but never re-checks miner validity/pubkey before signing at the pre-commit threshold - (File: stacks-signer/src/v0/signer.rs)

### Summary
`check_proposal` (the only place that validates a miner's pubkey hash and `SortitionMinerStatus`/`ActiveMiner` identity against the current sortition) runs once, at proposal arrival. The re-validation that runs later — at `handle_block_validate_ok` and again right before a signature is produced in `handle_block_pre_commit` — calls only `check_block_against_signer_db_state`, which the code itself documents as an "incomplete check" that does **not** repeat the miner-identity/pubkey authorization performed by `check_proposal`.

### Finding Description
`check_proposal` in v1 (`stacks-signer/src/chainstate/v1.rs:238-317`) and v2 (`stacks-signer/src/chainstate/v2.rs:146-163`) is where the signer authorizes a block: it recovers the miner pubkey, compares it against the sortition winner's `miner_pkh`, and rejects if the miner's sortition status is not `Valid`/not the `ActiveMiner` [1](#0-0) [2](#0-1) .

This authorization is intentionally **not** re-run afterward. The signer's own documentation states plainly: "Two things belong to the proposal path only and are not re-run at validate-ok or at signing: ... the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the pox bitvec, and tenure-extend rules before delegating here" [3](#0-2) .

The function that *is* re-run at both later checkpoints, `check_block_against_signer_db_state`, is explicitly marked incomplete:
"WARNING: This is an incomplete check. Do NOT call this function PRIOR to check_proposal or block_proposal validation succeeds." It only re-verifies tenure/chain-length consistency (`check_tenure_change_confirms_parent`, `check_latest_block_in_tenure`), never miner pubkey or `SortitionMinerStatus` [4](#0-3) .

This function is invoked twice after `check_proposal` has already returned `Ok`:
1. At `handle_block_validate_ok`, once the stacks-node has validated the block.
2. At `handle_block_pre_commit`, at the moment enough pre-commit weight (≥70%) has accumulated and the signer is about to produce its signature [5](#0-4) .

Between the initial `check_proposal` pass and the pre-commit threshold being crossed, the signer's view of miner validity can change: `SortitionState::is_timed_out` (v1) can flip a still-signed-nothing miner's status to `InvalidatedBeforeFirstBlock` due to `block_proposal_timeout` elapsing, and this flip is checked and applied only inside `check_proposal` [6](#0-5) . Because `check_block_against_signer_db_state` never re-examines `miner_status`/`current_miner_pkh`, a block whose miner became invalid (timed out) *after* the original proposal check but *before* the pre-commit threshold is reached will still be signed: the pre-commit tally and re-check only gate on tenure/chain-length, not on continued miner authorization.

### Impact Explanation
This breaks the "signed vs. validated" equality that the state machine is supposed to enforce: a signer produces a cryptographic signature over a block from a miner it currently (at signing time) considers invalid/timed-out, because the authorization check that would have caught this is only performed once, at an earlier point in the pipeline, and is never re-run at the actual moment the irreversible signing action occurs. Per the report's impact categories, this is a signer signing a block from a party it should treat as no longer authorized — a Critical-class safety break (signing an invalid/stale-miner block).

### Likelihood Explanation
No majority collusion or privileged access is required. It relies purely on ordinary network/gossip timing: pre-commits from honest peers accumulate over time while the burn-block clock advances, so the natural race between (a) `block_proposal_timeout` elapsing and marking a miner invalid and (b) enough independently-delayed pre-commit gossip finally crossing the 70% threshold is a plausible, attacker-influenceable condition (a slow-to-gossip or intentionally delayed proposal increases the window). This falls squarely within the scope of what a one-slot miner plus gossip timing can trigger.

### Recommendation
Re-run the same miner-identity/pubkey/status authorization performed by `check_proposal` (or an equivalent up-to-date check against `SortitionMinerStatus`/`MinerState::ActiveMiner`) inside `check_block_against_signer_db_state`, or otherwise gate `handle_block_pre_commit`'s final signing step on a fresh miner-validity check, not merely tenure/chain-length confirmation.

### Proof of Concept
1. Miner M proposes block B for tenure T. `check_proposal` validates `miner_pkh` against `cur_sortition` and passes (miner_status == `Valid`); B is submitted for node validation and pre-commits begin arriving from peers.
2. Node returns `BlockValidateOk`; `handle_block_validate_ok` calls `check_block_against_signer_db_state`, which only re-checks tenure/chain-length — passes; the signer broadcasts its own pre-commit.
3. Before pre-commit weight from other signers reaches the 70% threshold, `block_proposal_timeout` elapses without M producing a block; the next call into `check_proposal`-adjacent logic (e.g. via `SortitionState::is_timed_out`) would mark `cur_sortition.miner_status = InvalidatedBeforeFirstBlock` — but this flip is only evaluated inside `check_proposal`, which is not called again for B.
4. Additional peers' pre-commits (already in flight/gossiped) push the tally over threshold in `handle_block_pre_commit`; it calls `check_block_against_signer_db_state` (not `check_proposal`), which passes because it never inspects `miner_status`.
5. The signer proceeds to sign B, producing a valid signature over a block from a miner the signer's own state machine now considers invalid/timed-out.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L205-219)
```rust
        if let Some(last_sortition) = self.last_sortition.as_mut() {
            if last_sortition.miner_status == SortitionMinerStatus::Valid
                && SortitionState::is_timed_out(
                    &last_sortition.data.consensus_hash,
                    signer_db,
                    self.config.block_proposal_timeout,
                )?
            {
                info!(
                    "Last miner timed out, marking as invalid.";
                    "block_height" => block.header.chain_length,
                    "last_sortition_consensus_hash" => ?last_sortition.data.consensus_hash,
                );
                last_sortition.miner_status = SortitionMinerStatus::InvalidatedBeforeFirstBlock;
            }
```

**File:** stacks-signer/src/chainstate/v1.rs (L276-317)
```rust
        if proposed_by.state().data.miner_pkh != miner_pkh {
            warn!(
                "Miner block proposal pubkey does not match the winning pubkey hash for its sortition. Considering invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
                "proposed_block_pubkey" => &miner_pk.to_hex(),
                "proposed_block_pubkey_hash" => %miner_pkh,
                "sortition_winner_pubkey_hash" => %proposed_by.state().data.miner_pkh,
            );
            return Err(RejectReason::PubkeyHashMismatch);
        }

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
        };
```

**File:** stacks-signer/src/chainstate/v2.rs (L146-163)
```rust
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

**File:** docs/signer-flows.md (L425-433)
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

**File:** stacks-signer/src/v0/signer.rs (L1799-1880)
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

        // Ensure that the block is the last block in the chain of its current tenure.
        match SortitionData::check_latest_block_in_tenure(
            &proposed_block.header.consensus_hash,
            proposed_block,
            &mut self.signer_db,
            stacks_client,
            self.proposal_config.tenure_last_block_proposal_timeout,
            self.proposal_config.reorg_attempts_activity_timeout,
        ) {
            Ok(is_latest) => {
                if !is_latest {
                    warn!(
                        "Miner's block proposal does not confirm as many blocks as we expect";
                        "proposed_block_consensus_hash" => %proposed_block.header.consensus_hash,
                        "proposed_block_signer_signature_hash" => %signer_signature_hash,
                        "proposed_chain_length" => proposed_block.header.chain_length,
                    );
                    Some(self.create_block_rejection(
                        RejectReason::SortitionViewMismatch,
                        proposed_block,
                    ))
                } else {
                    None
                }
            }
            Err(e) => {
                warn!("{self}: Failed to check block against signer db: {e}";
                    "signer_signature_hash" => %signer_signature_hash,
                    "block_id" => %proposed_block.block_id()
                );
                Some(self.create_block_rejection(
                    RejectReason::ConnectivityIssues(
                        "failed to check block against signer db".to_string(),
                    ),
                    proposed_block,
                ))
            }
        }
    }
```
