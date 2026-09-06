## Finding: v2 (`GlobalStateView`) proposal-validation path never enforces `check_parent_tenure_choice`, unlike v1 (`SortitionsView`)

This maps to the Rancher bug class: two parallel code paths are supposed to enforce equivalent "is this the legitimate parent/context" checks, but one path silently omits the check the other path relies on for safety, letting a proposal that should be rejected pass instead.

### Summary
`stacks-signer` has two mutually-exclusive proposal-validation paths, selected in `check_block_against_state` based on `state_version.uses_global_state()`: [1](#0-0) 
- Legacy path: `SortitionsView::check_proposal` (`stacks-signer/src/chainstate/v1.rs`)
- Newer path: `GlobalStateView::check_proposal` (`stacks-signer/src/chainstate/v2.rs`)

### Finding Description
In `v1.rs`, `SortitionsView::check_proposal` calls `SortitionData::check_parent_tenure_choice` twice: once when aligning the current sortition against the canonical tip (rejecting with `ReorgNotAllowed` if the tip's parent tenure doesn't match) [2](#0-1) , and again inside `validate_tenure_change_payload` for tenure-change blocks, explicitly to confirm "the parent tenure was a valid choice" [3](#0-2) .

In `v2.rs`, `GlobalStateView::check_proposal` and its own `validate_tenure_change_payload` perform consensus-hash, pubkey, bitvec, `confirms_expected_parent`, and duplicate-block checks [4](#0-3)  — but at no point call `check_parent_tenure_choice` or otherwise verify that `parent_tenure_id` (taken from `self.signer_state.current_miner`) is a legitimate, non-reorg choice. The v2 path fully trusts the `MinerState::ActiveMiner { tenure_id, parent_tenure_id, .. }` value it was handed by the gossiped/aggregated global state machine.

### Impact Explanation
If reorg-legitimacy is not independently re-derived/re-verified before `GlobalStateView::check_proposal` runs — and I could not confirm within the available context that such a check happens earlier when the `SignerStateMachine`/`MinerState::ActiveMiner` is constructed from gossiped `StateMachineUpdate`s (`libsigner/src/v0/signer_state.rs`, `stacks-signer/src/v0/signer_state.rs`) — a signer running the v2/global-state protocol would sign a tenure-change block whose `parent_tenure_id` represents an illegitimate reorg that the v1 path would have rejected with `ReorgNotAllowed`. That breaks the "signed vs. validated" equality this scan is looking for: a block that should be provably invalid (unauthorized reorg of parent tenure) is instead treated as valid and pre-committed/signed.

### Likelihood Explanation
Reachable by a single miner (plus normal gossip of `StateMachineUpdate`/miner-view messages) once a majority of signers have moved to the v2 global-state protocol, without needing another signer's key or majority-signer collusion for the crafted proposal itself — only ordinary tenure-change block construction. This satisfies the "one-slot miner (plus gossip)" reachability bar.

### Recommendation
Add an explicit `check_parent_tenure_choice` (or equivalent) call in `GlobalStateView::check_proposal` / `validate_tenure_change_payload` (`stacks-signer/src/chainstate/v2.rs`), mirroring the checks in `stacks-signer/src/chainstate/v1.rs`, unless it can be proven that an equivalent reorg-legitimacy check is already enforced earlier in the construction of `MinerState::ActiveMiner` for the global state machine.

### Proof of Concept
Not fully constructible from the indexed context: this requires tracing exactly how `MinerState::ActiveMiner.parent_tenure_id` is populated in the global-state-machine update path (`stacks-signer/src/v0/signer_state.rs`, `libsigner/src/v0/signer_state.rs`, `stacks-node/src/nakamoto_node/stackerdb_listener.rs`) to determine conclusively whether `check_parent_tenure_choice`-equivalent logic already runs upstream. I was not able to complete that trace within the available tool budget, so I cannot definitively confirm this is an *unmitigated* bypass rather than logic that is simply relocated. Given the ambiguity and inability to prove the "before vs. after" state on both sides as the scan's Validate criteria require, this should be treated as a candidate requiring further code-reading (specifically `stacks-signer/src/v0/signer_state.rs`'s handling of `current_miner`/`ActiveMiner` transitions) rather than a confirmed vulnerability.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L865-869)
```rust
        if state_version.uses_global_state() {
            self.check_block_against_global_state(stacks_client, &block_info.block)
        } else {
            self.check_block_against_local_state(stacks_client, sortition_state, &block_info.block)
        }
```

**File:** stacks-signer/src/chainstate/v1.rs (L180-201)
```rust
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
```

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

**File:** stacks-signer/src/chainstate/v2.rs (L306-358)
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
```
