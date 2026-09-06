### Title
GlobalStateView::check_proposal (v2) omits the parent-tenure-choice validity check that SortitionsView::check_proposal (v1) performs, allowing a signer on the global-state protocol to sign a block from an illegitimate tenure reorg - (File: `stacks-signer/src/chainstate/v2.rs`)

### Summary
Analogous to the picklescan advisory, where a security-relevant check exists for one code path (`pydoc`/`operator` function blacklist) but an equivalent unsafe capability in a sibling path (`pydoc.locate`, `operator.methodcaller`) is missing from the same list, the stacks-signer maintains two parallel implementations of block-proposal validation — the legacy `SortitionsView::check_proposal` (v1) and the newer `GlobalStateView::check_proposal` (v2). The v1 path calls `SortitionData::check_parent_tenure_choice` inside `validate_tenure_change_payload` and rejects with `RejectReason::ReorgNotAllowed` when a tenure-change block chose an illegitimate parent tenure. The v2 path's `validate_tenure_change_payload` has no equivalent call — the "is this parent tenure choice valid" check is simply absent from the list of checks performed.

### Finding Description
`SortitionsView::validate_tenure_change_payload` (v1) explicitly performs the reorg-legitimacy check: [1](#0-0) 

This call to `check_parent_tenure_choice` is what allows the v1 signer to detect and reject a tenure-change block that attempts to build off of a parent tenure that does not represent the legitimate/canonical continuation (an improper reorg), producing `RejectReason::ReorgNotAllowed`.

`GlobalStateView::validate_tenure_change_payload` (v2), which runs the same conceptual step for signers that have activated the global-state protocol, performs only the `prev_tenure` match check, the `confirms_expected_parent` check, and the `DuplicateBlockFound` check — there is no call to `check_parent_tenure_choice` and no `ReorgNotAllowed` path at all: [2](#0-1) 

The dispatch in `check_block_against_state` sends every proposal down exactly one of these two paths based on `state_version.uses_global_state()`, so on a signer set that has activated the v2/global-state protocol, `check_parent_tenure_choice` is never invoked during proposal-time validation: [3](#0-2) 

The only other place chainstate is re-checked before a signature is produced is `check_block_against_signer_db_state`, which is version-agnostic and only re-validates `check_tenure_change_confirms_parent` and `check_latest_block_in_tenure` — again, no `check_parent_tenure_choice`/reorg-legitimacy re-check: [4](#0-3) 

The only remaining reorg-safety mechanism visible in the v0 signer pipeline is the "signed conflicts" / `reorg_permit_stands` logic exercised at the pre-commit-threshold stage, documented in `docs/signer-flows.md` section 5. That mechanism answers a different question — whether *this signer itself* has already signed a conflicting block at or above the proposed height — not whether the *proposed tenure's own parent choice* is a legitimate continuation of the canonical chain. It does not substitute for `check_parent_tenure_choice`, which asks whether the new tenure orphans previously-signed blocks of the tenure it is not building on. I was not able to find any other call site in `chainstate/v2.rs`, `chainstate/mod.rs`, or `v0/signer.rs` that invokes `check_parent_tenure_choice` for the global-state path, so this appears to be a genuine gap analogous to an incomplete "deny list" — one enforcement code path (v1) blocks the unsafe input, the parallel path (v2) simply never checks for it.

### Impact Explanation
If a miner proposes a tenure-change block whose declared `prev_tenure_consensus_hash` correctly matches the signer's locally recorded parent tenure (satisfying the checks v2 *does* perform) but which nonetheless represents an illegitimate reorg of the burn/tenure history that `check_parent_tenure_choice` alone would have caught, a v2/global-state signer will sign it. This is a signer signing a non-canonical/invalid-reorg block — a break of the "approved-parent vs. canonical" equality the state machine is meant to preserve, which falls in the Critical impact category (a signer signing an invalid, non-canonical, or conflicting block).

### Likelihood Explanation
This requires only a single miner (one slot, no majority of signers, no other signer's key) to construct and gossip a single crafted `BlockProposal` whose tenure-change payload passes v2's narrower check set but would have failed `check_parent_tenure_choice`. It is reachable on any signer set that has activated the v2/global-state protocol (`state_version.uses_global_state()` true) via the standard `handle_block_proposal` → `check_block_against_state` → `check_block_against_global_state` dispatch. Whether an attacker can actually craft the specific scenario in which `check_parent_tenure_choice`'s extra logic (comparing against `first_proposal_burn_block_timing` and the canonical tip's tenure) diverges from the checks v2 performs (`prev_tenure` match, `confirms_expected_parent`, `DuplicateBlockFound`) depends on burn-chain fork timing that I could not fully enumerate from static code review alone; this is the main residual uncertainty.

### Recommendation
Add an equivalent parent-tenure-choice legitimacy check (`SortitionData::check_parent_tenure_choice` or a v2-appropriate analog) to `GlobalStateView::validate_tenure_change_payload` in `stacks-signer/src/chainstate/v2.rs`, mapping a negative result to `RejectReason::ReorgNotAllowed` (or an equivalent v2 reject reason), so the same reorg-legitimacy guarantee holds regardless of which protocol version a signer set has activated.

### Proof of Concept
Not independently reproducible from static analysis alone: exploiting the gap requires constructing a burn-chain fork/tenure sequence where `check_tenure_change_confirms_parent` and the `prev_tenure_consensus_hash` match succeed (satisfying v2's checks) while `check_parent_tenure_choice`'s canonical-tip/timing logic would have failed (satisfying v1's stricter check). I could not fully verify a concrete such sequence is producible by a single miner within the tool-based review performed, and flag this as the key open item for a background agent with test-harness access (e.g. `stacks-signer/src/chainstate/tests/v2.rs`, which already exercises `check_parent_tenure_choice` in tests for v1/mod.rs, to construct a fork scenario and diff v1 vs v2 signer behavior on the same crafted tenure-change block).

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

**File:** stacks-signer/src/v0/signer.rs (L1809-1841)
```rust
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
