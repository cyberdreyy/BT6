Confirmed: this exact scenario is covered by the pre-commit conflict guard I read. In `handle_block_pre_commit` (`stacks-signer/src/v0/signer.rs:1432-1457`), a same-tenure conflict only blocks signing if the node's `get_tenure_tip` for that tenure already reaches the proposed height — i.e. only once the earlier block has been confirmed by the node. A block that is merely **locally accepted** (signed by this signer, but short of the 70% group threshold) has not been confirmed by the node, so `tip_height >= chain_length` is false and the guard does not fire, allowing the second, conflicting tenure-start block to be signed too.

### Title
v1 `validate_tenure_change_payload` misses locally-accepted duplicate tenure-start blocks, letting a signer sign two conflicting tenure-start blocks - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
`SortitionsView::validate_tenure_change_payload` (v1) checks for an existing block in the tenure using `SignerDb::get_last_globally_accepted_block`, whereas the v2 path (`GlobalStateView::check_proposal`) uses `SignerDb::get_last_signed_block`, which also counts locally-accepted blocks. This asymmetry means a v1 signer's proposal-time `DuplicateBlockFound` guard is blind to a tenure-start block it has already locally accepted but the group has not yet globally accepted, letting a second, conflicting tenure-start proposal for the same tenure pass the check.

### Finding Description
In `stacks-signer/src/chainstate/v1.rs`, `validate_tenure_change_payload` (used from `check_proposal`, the only place duplicate tenure-start blocks are checked, per `docs/signer-flows.md:428-431`) does: [1](#0-0) 

This queries `get_last_globally_accepted_block`, which only returns blocks whose `state == GloballyAccepted` (confirmed by the network threshold), not `LocallyAccepted` ones. In contrast, the fix noted in the CHANGELOG — "*When checking tenure change blocks, ensure there are no locally accepted blocks in the tenure, not just globally accepted blocks*" — was applied to the v2 path (`get_last_signed_block`, which counts `GloballyAccepted` **or** `LocallyAccepted`), documented explicitly: [2](#0-1) 

Both `get_last_globally_accepted_block`/`get_last_signed_block` are analogous to the two shadowing mappings in the external report: one is the correct "signed tip" concept (`get_last_signed_block`, used by v2 and by `get_tenure_last_block_info`), the other is a narrower, stale concept (`get_last_globally_accepted_block`) that v1 mistakenly still relies on for its duplicate check.

Because this is the only place `DuplicateBlockFound` is evaluated for tenure-start blocks (it is proposal-time only and never re-run, per `docs/signer-flows.md:283-286`), a v1 signer that has locally accepted tenure-start block A will pass a competing tenure-start block B for the same tenure straight through `check_proposal` without rejection, and B goes on to node validation.

The downstream backstop — the pre-commit "own tenure" conflict guard in `handle_block_pre_commit` — does not reliably catch this either: it only refuses to sign if the node's `get_tenure_tip` for that tenure is already at or above the proposed height: [3](#0-2) 

Since block A is only locally accepted (not yet globally observed/confirmed by the node), `get_tenure_tip` will not yet reflect it, so this guard does not fire, and the signer proceeds to sign B: [4](#0-3) 

This produces two different, signer-signed tenure-start blocks (A and B) at the same chain height in the same tenure from a single signer — a broken "one signed block per tenure/height" invariant, which is exactly the class of bug the assessment rules flag as Critical (a signer signing a conflicting block).

### Impact Explanation
A single signer ending up having signed two mutually exclusive tenure-start blocks at the same height is a safety violation: if enough other signers do the same for each of the two blocks (which the miner/gossip can orchestrate simply by broadcasting both proposals in the right order to different signers, or to the same signer before A is globally observed), two conflicting blocks can each accumulate signatures, creating a chain split/equivocation — the Critical-impact category defined by the assessment rules ("a signer signing an invalid, non-canonical, or conflicting block").

### Likelihood Explanation
This requires only a single miner (or a party relaying miner messages) proposing two tenure-start blocks for the same tenure in quick succession — no signer majority, no other signer's key, and no auth_token access is needed. It also requires that v1 (`SortitionsView`/legacy signer protocol) is still an active code path (`check_block_against_local_state` in `stacks-signer/src/v0/signer.rs`), and that the first block reaches only local (not yet global) acceptance before the second is proposed — a normal, easily reachable race condition rather than a contrived one.

### Recommendation
Change `validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs` (line 506) to use `SignerDb::get_last_signed_block` instead of `SignerDb::get_last_globally_accepted_block`, matching the v2 behavior and the fix already documented in the CHANGELOG, so that a locally-accepted tenure-start block also blocks a competing tenure-start proposal in the same tenure.

### Proof of Concept
1. Signer runs the legacy/v1 signer-protocol path (`check_block_against_local_state` → `SortitionsView::check_proposal`).
2. Miner proposes tenure-start block A for tenure `CH`. Signer validates it, it passes threshold-independent checks, node validates OK, signer pre-commits and eventually reaches `LocallyAccepted` (`mark_locally_accepted`) without yet reaching the 70% group threshold (`signed_group` not yet observed) and without the node's tenure tip advancing to it.
3. Miner (or relayed message) then proposes a different tenure-start block B for the same tenure `CH` (e.g., different transactions/timestamp).
4. `check_proposal` → `validate_tenure_change_payload` calls `signer_db.get_last_globally_accepted_block(CH)`, which returns `None` (A is only `LocallyAccepted`), so the `DuplicateBlockFound` rejection at `stacks-signer/src/chainstate/v1.rs:510-518` is skipped.
5. B is submitted for node validation and, assuming it validates fine, proceeds to pre-commit; at pre-commit threshold, `get_signed_conflicts`/the own-tenure guard in `stacks-signer/src/v0/signer.rs:1432-1457` checks `stacks_client.get_tenure_tip(CH)`, which is still below B's height because A was never confirmed by the node — so the guard does not block, and the signer signs B via `mark_locally_accepted` at `stacks-signer/src/v0/signer.rs:1467`.
6. The signer has now signed both A and B, two conflicting tenure-start blocks at the same height in tenure `CH`. [5](#0-4)

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L461-520)
```rust
    fn validate_tenure_change_payload(
        &self,
        proposed_by: &ProposedBy,
        tenure_change: &TenureChangePayload,
        block: &NakamotoBlock,
        signer_db: &mut SignerDb,
        client: &StacksClient,
    ) -> Result<(), RejectReason> {
        // Check that the tenure change's prev_tenure matches the sortition's known parent tenure.
        // This catches block commits with bad parent_block_ptr (e.g., vtxindex=0 exploit).
        let parent_tenure_id = &proposed_by.state().data.parent_tenure_id;
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
            self.config.tenure_last_block_proposal_timeout,
            self.config.reorg_attempts_activity_timeout,
        )
        .map_err(SignerChainstateError::from)?;
        if !confirms_expected_parent {
            return Err(RejectReason::InvalidParentBlock);
        }
        // now, we have to check if the parent tenure was a valid choice.
        let is_valid_parent_tenure = proposed_by.state().data.check_parent_tenure_choice(
            signer_db,
            client,
            &self.config.first_proposal_burn_block_timing,
        )?;
        if !is_valid_parent_tenure {
            return Err(RejectReason::ReorgNotAllowed);
        }
        let last_in_current_tenure = signer_db
            .get_last_globally_accepted_block(&block.header.consensus_hash)
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

**File:** docs/signer-flows.md (L428-431)
```markdown
- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
```

**File:** stacks-signer/src/v0/signer.rs (L1432-1457)
```rust
        if conflicts.iter().any(|conflict| {
            conflict.consensus_hash == block_info.block.header.consensus_hash
                && !self.reorg_permit_stands(stacks_client, conflict)
        }) {
            match stacks_client.get_tenure_tip(&block_info.block.header.consensus_hash) {
                Ok(tip) => {
                    let tip_height = tip.anchored_header.height();
                    if tip_height >= block_info.block.header.chain_length {
                        warn!(
                            "{self}: Reached the pre-commit threshold for a block that conflicts with previously signed or accepted blocks, and the canonical tip of its tenure is already at or above the proposed height. Refusing to sign.";
                            "signer_signature_hash" => %block_hash,
                            "block_height" => block_info.block.header.chain_length,
                            "canonical_tip_height" => tip_height,
                        );
                        return;
                    }
                }
                Err(e) => {
                    warn!(
                        "{self}: Failed to fetch the canonical tip of the proposed block's tenure: {e:?}. Treating the tenure as unconfirmed.";
                        "signer_signature_hash" => %block_hash,
                        "consensus_hash" => %block_info.block.header.consensus_hash,
                    );
                }
            }
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1466-1478)
```rust
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
        if let Err(e) = block_info.mark_locally_accepted(false) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}",);
            }
        }
        self.signer_db
            .insert_block(&block_info)
            .unwrap_or_else(|e| self.handle_insert_block_error(e));
        let accepted = self.create_block_acceptance(&block_info.block);
        // have to save the signature _after_ the block info
        self.handle_block_signature(stacks_client, sortition_state, &accepted);
        self.send_block_response(&block_info.block, accepted.into());
```
