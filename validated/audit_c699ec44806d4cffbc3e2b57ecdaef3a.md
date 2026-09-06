## Analog Vulnerability Found

### Title
Reorg-permission (`check_parent_tenure_choice`) never re-verified when v2 global-state signers evaluate a tenure-change proposal, and never checked at all before signing - ([File: stacks-signer/src/chainstate/v2.rs])

### Summary
The bug-class hint from the report is "an authorization decision computed once against a request's identity, while the actual privileged action is dispatched through a different code path that never repeats that decision." The stacks-signer analog is `check_parent_tenure_choice`: this is the *only* gate that decides whether a miner is allowed to reorg a prior tenure, and it is evaluated once, at proposal-arrival time, in the v1 path. In the v2 (`GLOBAL_SIGNER_STATE_ACTIVATION_VERSION`, i.e. `SUPPORTED_SIGNER_PROTOCOL_VERSION = 2`, the currently active version) path it is never called at all, and in neither path is it re-run at pre-commit/signing time by `check_block_against_signer_db_state`.

### Finding Description
`SortitionData::check_parent_tenure_choice` (`stacks-signer/src/chainstate/mod.rs:170-295`) is the rule that decides whether a miner may build off something other than the prior sortition (a reorg). It is permitted only if every reorged tenure has at most one globally accepted block and was proposed close enough to the sortition transition (`first_proposal_burn_block_timing`). This is the security-critical "approved-parent vs canonical" equality check referenced in the task's rules.

- In v1, this check is called exactly once, inside `SortitionsView::validate_tenure_change_payload` (`stacks-signer/src/chainstate/v1.rs:454-521`), which itself is called only from `check_proposal`, i.e. only at **proposal arrival** (section 3 of the signer flow, `check_block_against_local_state` → `SortitionsView::check_proposal`). [1](#0-0) 

- In v2, `validate_tenure_change_payload` (`stacks-signer/src/chainstate/v2.rs:306-359`) performs the parent-tenure-id match, `check_tenure_change_confirms_parent`, and the `DuplicateBlockFound` check — but it **never calls `check_parent_tenure_choice`** at all. A grep across the whole repository confirms `check_parent_tenure_choice` is referenced only in `chainstate/mod.rs` (definition), `chainstate/v1.rs` (its only caller), its unit tests, and documentation — never from `chainstate/v2.rs`, `v0/signer_state.rs`, or anywhere in the global-state (`GlobalStateView::check_proposal`) code path. [2](#0-1) [3](#0-2) 

- Regardless of v1/v2, the *only* re-check performed at pre-commit/signing time (section 5, `handle_block_pre_commit` → `check_block_against_signer_db_state`, and section 4, `handle_block_validate_ok` → same function) is `check_tenure_change_confirms_parent` / `confirms_latest_block_in_same_tenure`. Neither calls back into `check_parent_tenure_choice`: [4](#0-3) 

This is structurally identical to the reported bug class: the security decision ("is this reorg legitimate?") is made against one representation of the request (the proposal as first seen) and the code path that actually produces the privileged artifact (a signature, analogous to the proxied LLM call) is reached through a different, unguarded route — either because the version-specific handler (v2) omits the middleware-equivalent check entirely, or because the later re-evaluation stage (`check_block_against_signer_db_state`) was never wired to repeat it.

### Impact Explanation
`check_parent_tenure_choice`'s ruling can become stale between proposal time and signing time: a single malicious (or merely slow) one-slot miner can propose a tenure-change block `B` reorging tenure `P` while `P` still has only one globally accepted block (so the check passes and `B` is stored/pre-committed). While `B` sits in `PreCommitted` state waiting for the 70% threshold (which can take an observable amount of time — node validation round trip plus signer gossip), the miner (or a successor honoring the same tenure `P`) continues mining and gets a **second** block in `P` globally accepted. At that point the reorg via `B` should no longer be permitted (`globally_accepted_blocks > 1` → `Ok(false)` → `RejectReason::ReorgNotAllowed`), but because:
1. v2 never calls `check_parent_tenure_choice` at all, and
2. neither version re-runs it at the pre-commit/signing recheck,

the signer set can go on to sign and push `B`, producing a canonical reorg of a tenure that had already accumulated two globally-accepted (i.e., already-final-by-signer-consensus) blocks — a "signer signing a non-canonical/conflicting block" break of the approved-parent-vs-canonical invariant. This matches the Critical impact bucket defined in the rules.

### Likelihood Explanation
No majority of signers or leaked keys are required — this is triggerable by the ordinary miner-plus-gossip flow the task scopes in: a single miner proposes two competing branches (its own tenure `P`, plus a later tenure-change block `B` reorging `P`) and simply times its broadcasts so that `P`'s second block finalizes after `B`'s proposal was already accepted into `PreCommitted`. Since `SUPPORTED_SIGNER_PROTOCOL_VERSION = 2` (the v2/global-state path) is the currently active protocol version, the "never calls it at all" variant is the one live in production configurations, making this reachable on every proposal that carries a tenure-change payload, not just a narrow race window. [5](#0-4) 

### Recommendation
- Call `SortitionData::check_parent_tenure_choice` from v2's `validate_tenure_change_payload` (`stacks-signer/src/chainstate/v2.rs`), mirroring v1's behavior, so a reorg is gated by the same rule under the active protocol version.
- Re-run `check_parent_tenure_choice` (or an equivalent "is the reorg still permitted" check) inside `check_block_against_signer_db_state` at both the validate-ok recheck (section 4) and the pre-commit-threshold recheck (section 5) for tenure-change blocks, not only at initial proposal arrival, so a tenure that accrues a second globally-accepted block after a reorging proposal was accepted cannot still be signed away.
- Add a regression test analogous to `check_tenure_change_rejects_when_locally_accepted_block_exists` (in `stacks-signer/src/chainstate/tests/v2.rs`) that: proposes a v2 tenure-change block reorging tenure `P` while `P` has one globally-accepted block, lets `P` gain a second globally-accepted block before the pre-commit threshold is reached, and asserts the signer refuses to sign the reorging block.

### Proof of Concept
1. Boot a 3.0+ Nakamoto signer set running the default `SUPPORTED_SIGNER_PROTOCOL_VERSION = 2` (global-state) path.
2. Miner starts tenure `P` (consensus hash `CH_P`) and gets its first block globally accepted (1 globally accepted block in `P`).
3. Miner proposes a tenure-change block `B` for a new tenure `T2` whose `TenureChangePayload.prev_tenure_consensus_hash = CH_P`, reorging `P`. At this instant `get_globally_accepted_block_count_in_tenure(CH_P) == 1`, so if `check_parent_tenure_choice` were invoked it would return `Ok(true)`; but under v2 it is never invoked, so `validate_tenure_change_payload` (v2.rs:306-359) simply checks `check_tenure_change_confirms_parent` and `DuplicateBlockFound`, both of which pass — `B` is accepted for node validation and stored.
4. Before `B` reaches the 70% pre-commit threshold, the miner (still holding tenure `P`) proposes and gets signed a **second** block in `P`; it becomes globally accepted, so `get_globally_accepted_block_count_in_tenure(CH_P) == 2`.
5. `B`'s pre-commit weight crosses 70%. `handle_block_pre_commit` calls `check_block_against_signer_db_state`, which only calls `check_tenure_change_confirms_parent` (parent-tip check) — it does not consult `check_parent_tenure_choice`/`get_globally_accepted_block_count_in_tenure` again — so the recheck passes and the signer set signs `B`.
6. `B` is pushed and adopted, reorging away a tenure `P` that had two already-finalized (globally accepted) blocks, which `check_parent_tenure_choice`'s own rule ("disallow reorg if more than one block has already been signed", `chainstate/mod.rs:210-223`) explicitly says must never be permitted.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L496-504)
```rust
        // now, we have to check if the parent tenure was a valid choice.
        let is_valid_parent_tenure = proposed_by.state().data.check_parent_tenure_choice(
            signer_db,
            client,
            &self.config.first_proposal_burn_block_timing,
        )?;
        if !is_valid_parent_tenure {
            return Err(RejectReason::ReorgNotAllowed);
        }
```

**File:** stacks-signer/src/chainstate/v2.rs (L300-359)
```rust
        Ok(())
    }

    /// in tenure changes, we need to check:
    /// if the tenure change confirms the expected parent block (i.e.,
    /// the last globally accepted block in the parent tenure)
    fn validate_tenure_change_payload(
        tenure_change: &TenureChangePayload,
        block: &NakamotoBlock,
        parent_tenure_id: &ConsensusHash,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        config: &ProposalEvalConfig,
    ) -> Result<(), RejectReason> {
        // Check that the tenure change's prev_tenure matches the signer's known parent tenure.
        // This catches block commits with bad parent_block_ptr (e.g., vtxindex=0 exploit).
        if &tenure_change.prev_tenure_consensus_hash != parent_tenure_id {
            warn!(
                "Block commit parent tenure mismatch: the block commit's parent_block_ptr does not correspond to the actual parent tenure";
                "committed_parent_tenure" => %parent_tenure_id,
                "actual_parent_tenure" => %tenure_change.prev_tenure_consensus_hash,
                "consensus_hash" => %block.header.consensus_hash,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
            );
            return Err(RejectReason::InvalidParentBlock);
        }

        // Ensure that the tenure change block confirms the expected parent block
        let confirms_expected_parent = SortitionData::check_tenure_change_confirms_parent(
            tenure_change,
            block,
            signer_db,
            client,
            config.tenure_last_block_proposal_timeout,
            config.reorg_attempts_activity_timeout,
        )
        .map_err(SignerChainstateError::from)?;
        if !confirms_expected_parent {
            return Err(RejectReason::InvalidParentBlock);
        }
        // We already confirmed in check miner activity that the current tenure is valid. So check we are not
        // reorging the tenure blocks. Only blocks we have signed (locally or globally accepted) count
        // here: a block we have merely pre-committed to carries no signature from us, so it is safe to
        // accept a competing tenure-start block in its place if it failed to reach consensus.
        let last_in_current_tenure = signer_db
            .get_last_signed_block(&block.header.consensus_hash)
            .map_err(|e| {
                SignerChainstateError::from(ClientError::InvalidResponse(e.to_string()))
            })?;
        if let Some(last_in_current_tenure) = last_in_current_tenure {
            warn!(
                "Miner block proposal contains a tenure change, but we've already signed a block in this tenure. Considering proposal invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "proposed_block_signer_signature_hash" => %block.header.signer_signature_hash(),
                "last_in_tenure_signer_signature_hash" => %last_in_current_tenure.block.header.signer_signature_hash(),
            );
            return Err(RejectReason::DuplicateBlockFound);
        }
        Ok(())
    }
```

**File:** stacks-signer/src/v0/signer.rs (L941-999)
```rust
    /// Check if block should be rejected based on global signer state
    /// Will return a BlockRejection if the block is invalid, none otherwise.
    /// This is the Post-global signer state activation path
    fn check_block_against_global_state(
        &mut self,
        stacks_client: &StacksClient,
        block: &NakamotoBlock,
    ) -> Option<BlockRejection> {
        let signer_signature_hash = block.header.signer_signature_hash();
        let block_id = block.block_id();
        let Some(global_state) = self.global_state_evaluator.determine_global_state() else {
            warn!(
                "{self}: Cannot validate block, no global signer state";
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_id,
                "local_signer_state" => ?self.local_state_machine
            );
            return Some(self.create_block_rejection(RejectReason::NoSignerConsensus, block));
        };

        let global_state_view = GlobalStateView {
            signer_state: global_state,
            config: self.proposal_config.clone(),
        };

        info!(
            "{self}: Evaluating proposal against global state";
            "signer_state" => ?global_state_view.signer_state,
            "signer_signature_hash" => %signer_signature_hash,
            "block_id" => %block_id,
            "local_signer_state" => ?self.local_state_machine,
        );

        // Check if proposal can be rejected now if not valid against the global state
        match global_state_view.check_proposal(stacks_client, &mut self.signer_db, block) {
            // Error validating block
            Err(RejectReason::ConnectivityIssues(e)) => {
                warn!(
                    "{self}: Error checking block proposal: {e}";
                    "signer_signature_hash" => %signer_signature_hash,
                    "block_id" => %block_id,
                );
                Some(self.create_block_rejection(RejectReason::ConnectivityIssues(e), block))
            }
            // Block proposal is bad
            Err(reject_code) => {
                warn!(
                    "{self}: Block proposal invalid";
                    "signer_signature_hash" => %signer_signature_hash,
                    "block_id" => %block_id,
                    "reject_reason" => %reject_code,
                    "reject_code" => ?reject_code,
                );
                Some(self.create_block_rejection(reject_code, block))
            }
            // Block proposal passed check, still don't know if valid
            Ok(_) => None,
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

**File:** stacks-signer/src/v0/signer_state.rs (L50-53)
```rust
/// This is the latest supported protocol version for this signer binary
pub static SUPPORTED_SIGNER_PROTOCOL_VERSION: u64 = 2;
/// The version at which global signer state activates
pub static GLOBAL_SIGNER_STATE_ACTIVATION_VERSION: u64 = 2;
```
