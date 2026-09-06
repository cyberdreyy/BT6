### Title
Version-dependent duplicate-tenure-block check is not re-verified when the active signer protocol version flips between proposal and pre-commit-threshold time - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`check_block_against_signer_db_state`, called at validate-ok and at pre-commit-threshold time, is a fixed-semantics recheck of tenure/chain-length consistency, but it deliberately omits the `DuplicateBlockFound` tenure-change check that only runs once, at proposal time, inside `check_block_against_state` → `check_proposal` (v1/v2). Which variant of `check_proposal` ran at proposal time is itself decided dynamically by `determine_active_signer_protocol_version()`, based on the currently tallied `GlobalStateEvaluator` snapshot. Because that snapshot can change between the moment a block is proposed and the (later) moment it crosses the pre-commit threshold, a block can be evaluated once under v1 semantics and never re-checked under v2 semantics (or vice versa), even though v1 and v2 disagree about what counts as a duplicate/conflicting block in the same tenure.

### Finding Description
`determine_active_signer_protocol_version` recomputes the negotiated protocol version from `self.global_state_evaluator.determine_latest_supported_signer_protocol_version()` fresh on every call: [1](#0-0) 

`check_block_against_state`, which runs only once, at proposal arrival, uses this version to choose between two semantically different validity checks: [2](#0-1) 

The v1 path (`SortitionsView::check_proposal`, via `check_block_against_local_state`) and v2 path (`GlobalStateView::check_proposal`, via `check_block_against_global_state`) differ in how they compute the `DuplicateBlockFound` tenure-change check: v2 treats a block as a duplicate if it is locally **or** globally accepted (`get_last_signed_block`), while v1 only treats it as a duplicate if it is **globally** accepted (`get_last_globally_accepted_block`), per the documented anchors: [3](#0-2) 

Crucially, this duplicate check runs *only* at proposal time and is never repeated. The subsequent rechecks at validate-ok and at pre-commit threshold both go through `check_block_against_signer_db_state`, which performs only `check_tenure_change_confirms_parent`/`check_latest_block_in_tenure` — a fixed check independent of protocol version, and explicitly does not redo the duplicate-tenure check: [4](#0-3) [3](#0-2) 

Since `determine_active_signer_protocol_version` is recomputed independently at proposal time from whatever `GlobalStateEvaluator` tally exists at that instant, and the tally is populated by ordinary `StateMachineUpdate` gossip that keeps arriving throughout the block's lifecycle (protocol-version rollout/rollback windows are explicitly tested in `downgrade_signer_protocol_version` / `rollover_signer_protocol_version`), the version used to gate the one-shot duplicate check at proposal time is not fixed for the life of that specific block. A block proposed while the negotiated version is v1 (looser: only a *globally* accepted block blocks a duplicate) will pass the one-shot duplicate check even though, under v2 semantics active moments earlier/later, a merely *locally* accepted (signed-but-not-yet-globally-accepted) conflicting block in the same tenure would have caused rejection. Because the duplicate check is never re-run at pre-commit-threshold time, this signer can still cross the pre-commit threshold and sign the second, duplicate/conflicting tenure-change block.

### Impact Explanation
This allows a signer to sign a conflicting/non-canonical block: a tenure-change block that starts a new tenure despite the tenure already having a signed (locally accepted) block, which is exactly the class of "signing a conflicting/duplicate block" that `DuplicateBlockFound` exists to prevent under the stricter v2 semantics. This maps to the Critical impact category ("a signer signing an invalid, non-canonical, or conflicting block").

### Likelihood Explanation
The negotiated protocol version is recomputed from ordinary gossip each time `check_block_against_state`/`determine_active_signer_protocol_version` runs, and version rollout/rollback across the ≥70%/<70% threshold is an expected, tested operational condition (see `downgrade_signer_protocol_version`, `rollover_signer_protocol_version`, `multiversioned_signer_protocol_version_calculation` tests), not an attacker-only scenario. A one-slot miner racing a tenure-change proposal against the natural timing of version-negotiation flips (which occurs during any live version migration in the fleet) can trigger the mismatch without needing majority collusion of the signer set — only ordinary timing between when this signer's negotiated version briefly reads v1 versus v2 for two different proposals/re-proposals in the same tenure.

### Recommendation
Re-evaluate (or pin) the negotiated protocol version for the entire lifecycle of a given block/tenure decision, and re-run the full `DuplicateBlockFound`/duplicate-tenure check — not just the fixed `check_latest_block_in_tenure` — inside `check_block_against_signer_db_state` at both validate-ok and pre-commit-threshold time, using the same version-consistent semantics that were used (or would currently be used) for that block's tenure.

### Proof of Concept
Not independently reproduced with a live cluster in this analysis (index-only investigation); the mechanism is demonstrated structurally: (1) `determine_active_signer_protocol_version` is recomputed independently on each call from the live `GlobalStateEvaluator` tally [1](#0-0) ; (2) the duplicate-tenure check differs by version and runs only once, at proposal time [3](#0-2) ; (3) the only later recheck (`check_block_against_signer_db_state`) omits that check entirely [4](#0-3) . A background Devin agent with cluster access would be needed to construct a concrete end-to-end timing PoC (e.g. adapting `rollover_signer_protocol_version`/`downgrade_signer_protocol_version` test harnesses to straddle a version flip across two competing tenure-change proposals) to confirm exploitability under real timing constraints.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L782-807)
```rust
    /// Get the global signer protocol version
    fn determine_active_signer_protocol_version(&mut self) -> Option<SortitionStateVersion> {
        let local_version = self.get_signer_protocol_version();
        if let Ok(update) = self
            .local_state_machine
            .try_into_update_message_with_version(local_version)
        {
            self.global_state_evaluator
                .insert_update(self.stacks_address.clone(), update);
        };
        let local_state_version = SortitionStateVersion::from_protocol_version(local_version);
        self
            .global_state_evaluator
            .determine_latest_supported_signer_protocol_version().map(|version| {
                SortitionStateVersion::from_protocol_version(version)
            })
            .or_else(|| {
                // Don't default if we are in a global consensus activation state as its pointless
                if local_state_version.uses_global_state() {
                    None
                } else {
                    warn!("{self}: No consensus on signer protocol version. Defaulting to local state version: {local_version}.");
                    Some(local_state_version)
                }
            })
    }
```

**File:** stacks-signer/src/v0/signer.rs (L826-870)
```rust
        let Some(state_version) = self.determine_active_signer_protocol_version() else {
            warn!(
                "{self}: No consensus on signer protocol version. Unable to validate block. Rejecting.";
                "signer_signature_hash" => %block_info.block.header.signer_signature_hash(),
                "block_id" => %block_info.block.block_id(),
            );
            return Some(
                self.create_block_rejection(RejectReason::NoSignerConsensus, &block_info.block),
            );
        };

        // reject if the block itself is malformed
        if !block_info.check_static_valid_block() {
            debug!("{self}: Block is syntatically invalid; will not process");
            return Some(self.create_block_rejection(
                RejectReason::ValidationFailed(ValidateRejectCode::InvalidBlock),
                &block_info.block,
            ));
        }

        // For this first version, reject any block that marks transactions as
        // problematic. The criteria for what may legitimately appear in
        // `problematic_txs` has not been decided yet, so signers reject all
        // such blocks until that policy is established.
        if !block_info.block.header.problematic_txs.is_empty() {
            warn!(
                "{self}: Block proposal marks transactions as problematic, which signers do not yet allow; rejecting";
                "signer_signature_hash" => %block_info.block.header.signer_signature_hash(),
                "block_id" => %block_info.block.block_id(),
                "problematic_tx_count" => block_info.block.header.problematic_txs.len(),
            );
            return Some(
                self.create_block_rejection(
                    RejectReason::ProblematicTransactions,
                    &block_info.block,
                ),
            );
        }

        if state_version.uses_global_state() {
            self.check_block_against_global_state(stacks_client, &block_info.block)
        } else {
            self.check_block_against_local_state(stacks_client, sortition_state, &block_info.block)
        }
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
