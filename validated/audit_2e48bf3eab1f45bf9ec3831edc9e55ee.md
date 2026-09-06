### Title
GlobalStateView (v2) tenure-change validation omits the `check_parent_tenure_choice` reorg guard present in v1 — ([File: stacks-signer/src/chainstate/v2.rs])

### Summary
The signer's v1 chainstate path (`SortitionsView::validate_tenure_change_payload`) rejects a tenure-change block with `RejectReason::ReorgNotAllowed` if the parent tenure it builds on was not a legitimate choice, via a call to `check_parent_tenure_choice`. The v2 chainstate path (`GlobalStateView::validate_tenure_change_payload`), which is the sole validation path once the signer set activates the global-state protocol version, omits this call entirely, leaving a hole in the equivalent function that otherwise mirrors v1's checks.

### Finding Description
`stacks-signer/src/chainstate/v1.rs` `SortitionsView::validate_tenure_change_payload` (lines 461-520) performs, in order:
1. verify `tenure_change.prev_tenure_consensus_hash` matches the known parent tenure,
2. `SortitionData::check_tenure_change_confirms_parent` (confirms the expected parent block),
3. `proposed_by.state().data.check_parent_tenure_choice(...)` — reject with `RejectReason::ReorgNotAllowed` if the parent tenure choice is invalid,
4. `get_last_globally_accepted_block` duplicate-block check. [1](#0-0) 

`stacks-signer/src/chainstate/v2.rs` `GlobalStateView::validate_tenure_change_payload` (lines 306-359) performs only steps 1, 2, and a duplicate-block check via `get_last_signed_block`; it never calls `check_parent_tenure_choice` or otherwise rejects an improper-reorg parent-tenure choice. [2](#0-1) 

A repo-wide search confirms `check_parent_tenure_choice` (defined once in `stacks-signer/src/chainstate/mod.rs`) is referenced only from `v1.rs` and its own tests — never from `v2.rs`. Once a signer's active protocol version reaches `GLOBAL_SIGNER_STATE_ACTIVATION_VERSION`, `check_block_against_state` in `stacks-signer/src/v0/signer.rs` routes exclusively through `check_block_against_global_state` → `GlobalStateView::check_proposal` → `validate_tenure_change_payload`, so the v1 guard is never invoked again for that signer: [3](#0-2) 

This is structurally identical to the reported bug class: two parallel code paths implement the same validation, but one (v1/Blog) applies a critical rule the other (v2/Pages) silently drops, and the "dropped" path is the one actually reachable for validated content once the newer path becomes active.

### Impact Explanation
`check_parent_tenure_choice` exists specifically to prevent a miner from starting a new tenure on top of an illegitimate parent (an improper reorg of the previous tenure). Without it, a v2-protocol signer can be induced to treat a tenure-change block that reorgs an already-canonical/committed tenure as valid, sign it, and count it toward the group signature — i.e. sign a non-canonical/conflicting block, breaking the "approved-parent vs canonical" equality the check exists to enforce. This meets the Critical bar: a signer signing a non-canonical or conflicting block.

### Likelihood Explanation
This requires only a single miner producing a tenure-change block whose `prev_tenure_consensus_hash` and confirmed parent block are technically self-consistent (satisfying steps 1–2) but whose parent-tenure choice would fail `check_parent_tenure_choice`'s reorg-timing rules — no majority of signers or additional keys are needed, and it is reachable purely by broadcasting a single crafted block proposal to signers running the v2/global-state protocol.

### Recommendation
Add the same `check_parent_tenure_choice` call (with the equivalent `ReorgNotAllowed` rejection) to `GlobalStateView::validate_tenure_change_payload` in `stacks-signer/src/chainstate/v2.rs`, mirroring `SortitionsView::validate_tenure_change_payload` in `v1.rs`, using the v2-appropriate parent-tenure/sortition data available in `GlobalStateView`.

### Proof of Concept
1. Deploy a signer set operating at `GLOBAL_SIGNER_STATE_ACTIVATION_VERSION` (v2/global-state path active), so `check_block_against_state` routes exclusively through `GlobalStateView::check_proposal`.
2. As the current miner, construct a tenure-change block whose `prev_tenure_consensus_hash` matches the recorded parent tenure and whose parent-block confirmation passes `check_tenure_change_confirms_parent`, but where the chosen parent tenure would fail v1's `check_parent_tenure_choice` reorg-timing rule (e.g., reorging a tenure already signed by other signers, timed to abuse `first_proposal_burn_block_timing`).
3. Broadcast the block proposal via StackerDB to the signer set.
4. Observe that `GlobalStateView::validate_tenure_change_payload` accepts the proposal (no `ReorgNotAllowed` rejection is possible since the check is absent), and the signer proceeds to validate, pre-commit, and ultimately sign it — a block that the v1 path would have rejected as an invalid reorg.

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

**File:** stacks-signer/src/chainstate/v2.rs (L306-359)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L865-869)
```rust
        if state_version.uses_global_state() {
            self.check_block_against_global_state(stacks_client, &block_info.block)
        } else {
            self.check_block_against_local_state(stacks_client, sortition_state, &block_info.block)
        }
```
