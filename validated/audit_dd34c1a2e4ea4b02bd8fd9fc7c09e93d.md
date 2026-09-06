### Title
v1 signer allows signing a second, conflicting tenure-start block because `validate_tenure_change_payload` only checks globally-accepted blocks, not locally-accepted ones - (File: stacks-signer/src/chainstate/v1.rs)

### Summary
In `SortitionsView::validate_tenure_change_payload` (v1), the duplicate-tenure-start-block guard queries `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)`, whereas the equivalent v2 function `GlobalStateView::validate_tenure_change_payload` queries `signer_db.get_last_signed_block(&block.header.consensus_hash)`, which covers both locally- and globally-accepted blocks. This means that on chains still running v1 rules, a signer that has already locally accepted (and signed) one tenure-start block for a given `consensus_hash` can be induced to sign a second, conflicting tenure-start block for the same tenure as long as that first block hasn't yet reached global acceptance.

### Finding Description
The uniqueness invariant being violated is: "a signer must sign at most one distinct block per tenure (`consensus_hash`)." This is enforced at the bottom of `validate_tenure_change_payload` by rejecting a new tenure-change block if there is already a recorded block signed for that tenure.

- v1 implementation, `stacks-signer/src/chainstate/v1.rs:505-518`, uses `get_last_globally_accepted_block`. [1](#0-0) 
- v2 implementation, `stacks-signer/src/chainstate/v2.rs:344-357`, uses `get_last_signed_block`, and its comment explicitly states the intent: "Only blocks we have signed (locally or globally accepted) count here." [2](#0-1) 

Root cause: `get_last_globally_accepted_block` in `signerdb.rs` only returns blocks that have reached the `GloballyAccepted` state, filtering out blocks the signer itself signed but that are only `LocallyAccepted` (i.e., accepted by this signer, not yet confirmed by a threshold of signers). Because v1's guard does not consult the `LocallyAccepted` state, if a first tenure-start `BlockProposal` for a given `consensus_hash` is locally accepted (and thus signed) by this signer but has not yet crossed the global-acceptance threshold, the v1 code path lets a second, different tenure-start `BlockProposal` for the same `consensus_hash` pass this check and be signed.

Exploit flow (single-slot attacker, unprivileged):
1. Attacker wins a single miner slot for consensus hash `CH`.
2. Attacker crafts tenure-start `BlockProposal A` (with valid tenure-change payload, valid parent tenure, valid pubkey hash, etc.) and gossips it. The target signer processes it via `check_proposal` -> `validate_tenure_change_payload`, finds no globally- or locally-accepted block yet, accepts, and signs it — its `BlockInfo` is recorded as `LocallyAccepted` in `signerdb`.
3. Before block A reaches global acceptance (i.e., before enough other signers/weight sign it), the attacker crafts a second tenure-start `BlockProposal B` for the **same** `CH` but a different transaction set/miner content.
4. The target signer processes B. In `validate_tenure_change_payload`, `get_last_globally_accepted_block(CH)` returns `None` because A is only `LocallyAccepted`, not `GloballyAccepted`. All the other checks (parent tenure match, pubkey hash, bitvec, etc.) can be satisfied by the attacker crafting B correctly. The function returns `Ok(())`, and the signer signs B.
5. The signer has now produced valid signatures over two distinct, conflicting tenure-start blocks for the same tenure — breaking the uniqueness/equivocation guard that downstream aggregation and chain-safety logic depend on.

This requires only the attacker's own miner slot (to produce the `BlockProposal`s) plus gossip capability to deliver both proposals to the target signer — no majority of signers, no compromise of the victim signer, and no auth token.

### Impact Explanation
This breaks the "signer signing a conflicting block" / equivocation-guard safety property (chain safety / uniqueness). If enough signers are induced this way (each independently duplicating the same flaw against the same tenure), the two conflicting signature sets could both reach thresholds, producing two valid-looking, conflicting canonical block candidates for the same tenure and undermining Nakamoto consensus uniqueness guarantees. Even for a single signer, it demonstrates the equivocation guard is not fail-closed on v1: the signer's own local signing record no longer prevents it from signing a second candidate for a tenure it already signed. This matches the "Critical" impact category (signer signing a conflicting block, breaking chain safety/uniqueness).

### Likelihood Explanation
Preconditions: the reward cycle/chain must be running v1 chainstate rules (older signer protocol version) and there must exist a timing window where a signer has locally-but-not-globally accepted the first tenure-start block (a normal, frequently occurring condition prior to full quorum). The attacker cost is exactly one won miner slot plus the ability to gossip two proposals — well within an "unprivileged, single-slot" attacker's capabilities, and the attack is repeatable every time this exact timing window occurs on a v1-governed chain.

### Recommendation
Change `validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs` to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` (matching v2's semantics) instead of `get_last_globally_accepted_block`, so that both `LocallyAccepted` and `GloballyAccepted` blocks for the tenure are considered when rejecting a competing tenure-start proposal.

### Proof of Concept
Port the existing v2 test to v1, asserting the current (buggy) behavior differs from the expected rejection:

```rust
// stacks-signer/src/chainstate/tests/v1.rs
#[test]
fn check_tenure_change_rejects_when_locally_accepted_block_exists() {
    // 1. Build a SortitionsView (v1) with cur_sortition for consensus_hash CH,
    //    matching parent_tenure_id, miner_pkh, etc.
    // 2. Construct BlockProposal A: tenure-start block for CH, with valid
    //    TenureChangePayload confirming parent tenure. Call check_proposal(); it
    //    should return Ok(()). Manually insert/update the BlockInfo in signer_db
    //    as `LocallyAccepted` for A's signer_signature_hash (mirroring what
    //    handle_block_proposal + signerdb persistence would do after local acceptance).
    // 3. Construct BlockProposal B: a DIFFERENT tenure-start block for the SAME
    //    consensus_hash CH (different tx set / signer_signature_hash), otherwise
    //    passing all other checks (same parent tenure, same miner pubkey hash).
    // 4. Call view.validate_tenure_change_payload(&proposed_by, &tenure_change_B,
    //    &block_B, &mut signer_db, &client).
    //
    // Current (buggy) result: Ok(()) — because get_last_globally_accepted_block(CH)
    //    returns None (A is only LocallyAccepted).
    // Expected (fixed) result: Err(RejectReason::DuplicateBlockFound), matching v2's
    //    check_tenure_change_rejects_when_locally_accepted_block_exists test outcome.
    assert!(matches!(result, Err(RejectReason::DuplicateBlockFound)));
}
``` [3](#0-2) [4](#0-3)

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L461-519)
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
