### Title
Signer re-validation gates never re-check miner-invalidation status, allowing a signature over a block from a miner the signer has already deemed invalid - (File: stacks-signer/src/v0/signer.rs)

### Summary
`SortitionsView::check_proposal` (`stacks-signer/src/chainstate/v1.rs:136-317`) is the only place that checks a proposing miner's `SortitionMinerStatus` (`Valid` / `InvalidatedBeforeFirstBlock` / `InvalidatedAfterFirstBlock`) against the block being evaluated, and it is the only place that *sets* `cur_sortition.miner_status` to an invalidated state (e.g. on tenure-timeout via `SortitionState::is_timed_out`, `stacks-signer/src/chainstate/v1.rs:144-163`, or on an invalid parent-tenure choice, `v1.rs:180-201`). This check runs once, at initial proposal arrival (`handle_block_proposal` → `check_block_against_state`, `stacks-signer/src/v0/signer.rs:1671-1680`). [1](#0-0) [2](#0-1) 

### Finding Description
After a proposal is submitted to the node for validation, the block sits in flight for however long node validation takes. When the OK response returns, and again when the pre-commit threshold is crossed, the signer re-verifies the block against `check_block_against_signer_db_state` (`stacks-signer/src/v0/signer.rs:1803-1880`) before advancing it to `PreCommitted`/signing: [3](#0-2) 

That function only re-checks two things: (1) that a tenure-change block confirms the expected parent (`SortitionData::check_tenure_change_confirms_parent`), and (2) that the block is the latest block of its own tenure (`SortitionData::check_latest_block_in_tenure`). It never re-consults `SortitionsView`/`cur_sortition.miner_status`. This same function is invoked from both `handle_block_validate_ok` (`stacks-signer/src/v0/signer.rs:1946-1959`) and `handle_block_pre_commit` at the moment the 70% pre-commit threshold is crossed (`stacks-signer/src/v0/signer.rs:1340-1366`), i.e. at the two gates closest to producing an actual signature. [4](#0-3) [5](#0-4) 

Meanwhile, `cur_sortition.miner_status` can flip from `Valid` to `InvalidatedBeforeFirstBlock` purely as a side effect of `check_proposal` being invoked again for an unrelated/later proposal — e.g. the tenure-timeout branch (`is_timed_out`, based on `block_proposal_timeout` elapsing with no signed block in that tenure) or the parent-tenure-mismatch branch. Because `check_proposal` is only invoked when a *new* proposal comes in (`handle_block_proposal`, `stacks-signer/src/v0/signer.rs:1671`), the already-in-flight proposal from the now-invalidated miner is never re-screened: its fate is decided solely by `check_block_against_signer_db_state`, which is blind to `miner_status`.

Concretely: the miner proposes block N and it is submitted for node validation (a slow operation). While validation is pending, the block-proposal timeout elapses (or the miner's slot proposes/behaves such that `check_proposal` is re-entered for something else and flips `cur_sortition.miner_status` to `InvalidatedBeforeFirstBlock`, e.g. via `is_timed_out` in `stacks-signer/src/chainstate/v1.rs:144-163`), so the signer's own state now treats this miner as invalid and would reject any *new* proposal from it (`v1.rs:289-299`). But when the node's `Ok` response for block N arrives, `handle_block_validate_ok` only calls `check_block_against_signer_db_state`, which does not consult `miner_status` at all, so the block proceeds to `PreCommitted` and — once 70% of signers reach the same in-flight state — to a full `LocallyAccepted` signature (`handle_block_pre_commit`, `stacks-signer/src/v0/signer.rs:1340-1479`).

This is a direct structural analog of the ZITADEL bug: "deactivating" the resource owner (marking the miner invalid) does not propagate to the already-issued grant (the in-flight, already-validated proposal), because the downstream gates check a narrower invariant (tenure/parent consistency) instead of re-verifying the top-level authorization (miner validity) that was supposed to gate signing.

### Impact Explanation
If this path is reachable, it lets a signer produce a valid signature over a block from a miner that the signer's own local state considers invalidated/timed-out, at the same time that other signers may be moving to accept a tenure-extend block from the prior miner instead. This creates the possibility of two disjoint, individually-well-signed candidates at the same tenure/height — a conflicting-signature scenario — which is categorized as Critical under the given rubric ("a signer signing an invalid, non-canonical, or conflicting block").

### Likelihood Explanation
This requires only ordinary timing (node validation latency exceeding `block_proposal_timeout`, or a legitimate parent-tenure mismatch being detected on a subsequent proposal) plus the natural race between "proposal in flight" and "miner declared invalid" — no majority collusion, no other signer's key, and no auth token is needed. The exact reachability of the race (whether `check_proposal` gets re-invoked with a stale `sortition_state` reference before the in-flight validation completes) depends on scheduling details of `process_event`/`handle_block_validate_response` that I was not able to fully trace to a guaranteed reproduction within this session; I could not find a test in `stacks-signer/src/v0/tests.rs` that specifically exercises "validate-ok arrives after `cur_sortition.miner_status` flips to invalidated," so this should be verified by tracing whether the same in-memory `SortitionsView` (with its mutated `miner_status`) is the one consulted, and whether `sortition_state` can go stale (e.g., reset to `None` and refetched fresh) between the invalidation and the validate-ok callback.

### Recommendation
Make `check_block_against_signer_db_state` (or its callers `handle_block_validate_ok` and the pre-commit-threshold path in `handle_block_pre_commit`) re-consult the live `SortitionsView`/`cur_sortition.miner_status` for the block's consensus hash before advancing to `PreCommitted`/signing, mirroring the check already performed in `check_proposal`. Alternatively, invalidating a miner's status should eagerly walk all locally-tracked, not-yet-globally-decided `BlockInfo` rows for that miner's tenure and mark them rejected/re-evaluate them immediately, rather than relying on a check that only fires when a fresh proposal happens to arrive.

### Proof of Concept
1. Miner M proposes block N; the signer's `check_proposal` passes (miner status `Valid`) and the block is submitted to the node for validation (`submit_block_for_validation`, `stacks-signer/src/v0/signer.rs:1696`).
2. Node validation takes long enough that `block_proposal_timeout` elapses with no signed block yet in M's tenure.
3. A subsequent event causes `check_proposal`/`SortitionState::is_timed_out` to run again and set `self.cur_sortition.miner_status = SortitionMinerStatus::InvalidatedBeforeFirstBlock` (`stacks-signer/src/chainstate/v1.rs:144-163`).
4. The node's earlier validation for block N returns `Ok`; `handle_block_validate_ok` calls `check_block_against_signer_db_state`, which only checks tenure/parent consistency (not `miner_status`) and returns `None` (`stacks-signer/src/v0/signer.rs:1803-1880`, `1946-1959`).
5. Block N is marked `PreCommitted`, pre-commits are gossiped, and once 70% weight is reached the signer places a full signature on block N from a miner it had already locally invalidated (`stacks-signer/src/v0/signer.rs:1340-1479`).

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L144-163)
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
```

**File:** stacks-signer/src/chainstate/v1.rs (L289-299)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L1803-1880)
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

**File:** stacks-signer/src/v0/signer.rs (L1946-1959)
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
```
