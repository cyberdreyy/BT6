### Title
Pre-commit re-validation (`check_block_against_signer_db_state`) never re-checks parent-tenure canonicity, only initial `check_proposal` does — (File: stacks-signer/src/v0/signer.rs, stacks-signer/src/chainstate/mod.rs)

### Summary
`check_tenure_change_confirms_parent` (mod.rs:488-504) is a pure delegation to `check_latest_block_in_tenure`, which only compares the proposed block's chain length against locally-recorded blocks for `prev_tenure_consensus_hash` — it never asks whether that consensus hash is still on the canonical sortition history. That canonicity check exists only in `SortitionData::check_parent_tenure_choice`, invoked from `SortitionState::is_tenure_valid` (mod.rs:581-606) during the *initial* `check_proposal` flow. The re-validation path used before signing, `check_block_against_signer_db_state` (signer.rs:1803-1827), calls only `check_tenure_change_confirms_parent` — it does not call `is_tenure_valid`/`check_parent_tenure_choice` again.

### Finding Description
The equality that must hold is: *the parent tenure confirmed by block-content checks == the parent tenure that is currently canonical on the burn chain*, and this equality must be re-verified at every point acceptance/signing is (re-)decided, not just once at initial proposal time.

- `check_tenure_change_confirms_parent` (mod.rs:488-504) only checks block/chain-length consistency against `signer_db`'s cached last-block for the tenure named in `TenureChangePayload.prev_tenure_consensus_hash`. It performs no sortition/canonicity lookup itself. [1](#0-0) 
- Canonicity of the parent tenure choice is checked exclusively by `SortitionData::check_parent_tenure_choice`, reached via `SortitionState::is_tenure_valid`, which is part of the initial `check_proposal` validation pipeline. [2](#0-1) 
- The pre-commit / re-check function explicitly documents itself as an *incomplete* check that must not be called standalone before `check_proposal`, and it invokes only `check_tenure_change_confirms_parent` for tenure-change blocks — no call to `is_tenure_valid` or `check_parent_tenure_choice` appears anywhere in this function. [3](#0-2) 

Because the signer's local burn-block/tenure bookkeeping (`prune_superseded_tenures`, `bitcoin_block_arrival` settlement per docs/signer-flows.md) can lag a real orphaning event, a tenure that was canonical when the proposal was first validated (`check_proposal` passed, including `check_parent_tenure_choice`) can become non-canonical afterward. If the pre-commit re-check happens in that window, `check_block_against_signer_db_state` will consult only stale, locally-cached tenure/block data via `check_tenure_change_confirms_parent`/`check_latest_block_in_tenure`, find it "consistent," and return `None` (no rejection) — without ever re-asking whether `prev_tenure_consensus_hash` is still canonical.

### Impact Explanation
This breaks the canonicity safety property: a signer could contribute a valid signature toward a block whose tenure-change parent is no longer canonical, because the only re-check performed before finalizing the signature omits the sortition-canonicity check that was only exercised once, at initial proposal time. If exploited across enough signers in the same lagged window, this could contribute to finalizing a non-canonical/conflicting block — matching the Critical category ("a signer signing an invalid, non-canonical block").

### Likelihood Explanation
The precondition is a genuine timing race: a real burn-chain reorg must occur, and the signer's local view of tenure canonicity (via `prune_superseded_tenures`/burn-block bookkeeping) must lag behind the node's canonical view, all within the pre-commit re-validation window. This is a narrow, node/burnchain-timing-dependent race rather than something the attacker fully controls with just "one slot plus gossip" — the attacker can craft and time the `BlockProposal`, but cannot force the underlying reorg or guarantee the signer's local state is stale at the precise moment of re-check. It is a genuine code-path gap, but its exploitability depends on external reorg timing outside attacker control, reducing (but not eliminating) practical likelihood.

### Recommendation
Have `check_block_against_signer_db_state` (or its caller) re-run the sortition-canonicity check (`SortitionState::is_tenure_valid` / `check_parent_tenure_choice`) for the `prev_tenure_consensus_hash` before returning `None`, so that every re-validation point — not just the initial `check_proposal` — re-establishes that the confirmed parent tenure is still canonical.

### Proof of Concept
Rust test plan for `stacks-signer`:
1. Construct a `SignerDb` and `StacksClient` (mocked) such that tenure `T_orphan` is canonical and has a recorded last block; build a `BlockProposal` with `TenureChangePayload.prev_tenure_consensus_hash = T_orphan`.
2. Run `check_proposal` — assert it succeeds, confirming `check_parent_tenure_choice` passed while `T_orphan` was canonical.
3. Simulate a reorg: mark `T_orphan` as orphaned in the mocked sortition DB/client (canonical burn view no longer includes it), without updating `SignerDb`'s cached block info for `T_orphan`.
4. Call `check_block_against_signer_db_state` directly on the same proposal.
5. Assert it returns `None` (no rejection) despite `T_orphan` no longer being canonical, and separately assert that calling `SortitionData::check_parent_tenure_choice` directly against the updated (orphaned) sortition view returns `false` — proving the re-check path and the canonicity check disagree, i.e., the equality is broken at re-validation time.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L488-504)
```rust
    pub fn check_tenure_change_confirms_parent(
        tenure_change: &TenureChangePayload,
        block: &NakamotoBlock,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        tenure_last_block_proposal_timeout: Duration,
        reorg_attempts_activity_timeout: Duration,
    ) -> Result<bool, ClientError> {
        Self::check_latest_block_in_tenure(
            &tenure_change.prev_tenure_consensus_hash,
            block,
            signer_db,
            client,
            tenure_last_block_proposal_timeout,
            reorg_attempts_activity_timeout,
        )
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

**File:** stacks-signer/src/v0/signer.rs (L1799-1840)
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
