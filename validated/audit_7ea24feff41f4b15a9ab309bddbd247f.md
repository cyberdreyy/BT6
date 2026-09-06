### Title
Signer can double-sign conflicting tenure-change blocks in v1 chainstate because `validate_tenure_change_payload` only checks `get_last_globally_accepted_block` - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionsView::validate_tenure_change_payload` in the v1 chainstate path guards against signing a second, conflicting first-block-of-tenure by calling `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` and rejecting only if that returns `Some`. This ignores blocks the signer has already locally signed for the same tenure but that have not yet reached global (quorum) acceptance, unlike the v2 path which uses `get_last_signed_block` (locally + globally accepted). This asymmetry lets an attacker who controls a single miner/sortition slot present a second tenure-change block for the same `consensus_hash` and get the signer to sign it too.

### Finding Description
The relevant code: [1](#0-0) 

`get_last_globally_accepted_block` only returns blocks whose signer-db state is globally accepted (quorum-reached). A block this specific signer has already signed ("locally accepted") but which has not yet crossed the global-acceptance threshold (e.g., because other signers haven't yet responded, or the tenure stalled) is invisible to this check. The v2 counterpart intentionally widens this to `get_last_signed_block`, which also counts locally-accepted/signed blocks, precisely to close this gap for the newer chainstate.

The attacker (a single, unprivileged miner-slot holder) can:
1. Win a sortition slot and get the honest signer to sign a first tenure-change block `B1` for tenure `CH` (recorded in `SignerDb` as locally signed, not yet globally accepted, e.g. because other signers are slow or don't reach quorum in time).
2. Gossip a second, distinct tenure-change `BlockProposal` `B2` for the same `consensus_hash CH`, same `parent_tenure_id`, with a different `signer_signature_hash`.
3. `check_proposal` routes `B2` through the same `proposed_by` matching (same sortition, same miner pubkey hash — attacker legitimately owns this slot) and into `validate_tenure_change_payload`.
4. `check_tenure_change_confirms_parent` and `check_parent_tenure_choice` pass because `B2` references the same legitimate parent tenure as `B1`.
5. The duplicate-block guard calls `get_last_globally_accepted_block(CH)`, which returns `None` (since `B1` is only locally accepted), so no `DuplicateBlockFound` rejection occurs, and validation falls through to `Ok(())`.
6. The signer then signs `B2`, resulting in this single signer having signed two conflicting blocks for the same tenure/height (`B1` and `B2`).

This breaks the intended "at most one block signed per tenure per signer" invariant, which is exactly the uniqueness property `has_signed_block_in_tenure`/`is_timed_out`'s own comments (lines 60-67 of the same file) explicitly distinguish between "signed" and "globally accepted" for — showing the codebase is aware locally-signed-but-not-globally-accepted blocks are a real, tracked state that should have been consulted here but wasn't.

### Impact Explanation
This is a uniqueness/equivocation-guard failure (fail-closed safety property broken): a single honest-but-tricked signer's signature is attached to two competing chain histories for the same tenure/height. If enough signers are put into this state, or if this contributes even partially to a mixed signature set across `B1`/`B2`, it can help finalize conflicting blocks and contributes materially to a chain split — matching the "Critical: signer signing an invalid/non-canonical/conflicting block" and "signature valid across chain/tenure boundaries"-style safety violation. The attack is repeatable for every tenure the attacker wins, requiring only their own slot and gossip.

### Likelihood Explanation
Preconditions are realistic and attacker-controlled: the attacker needs to win one miner/sortition slot (a permitted capability per the threat model) and simply delay/withhold enough signer responses (or exploit natural network timing) so the first proposal is signed locally by at least one signer but not yet globally accepted, then gossip a second competing proposal. No majority of signers, no privileged role, and no auth token are required — only the described sequence of two `BlockProposal`s and normal StackerDB gossip that a signer already consumes. This is feasible on any v1-chainstate signer (`uses_global_state() == false`) whenever tenure-change confirmation races with quorum formation, which is a routine timing window, not a rare edge case.

### Recommendation
In `validate_tenure_change_payload` (stacks-signer/src/chainstate/v1.rs), replace the call to `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` with a call equivalent to v2's `get_last_signed_block` (or otherwise also check locally-accepted/signed blocks) so that any block this signer has already signed for the given tenure — whether or not it has reached global acceptance — triggers `RejectReason::DuplicateBlockFound` for a conflicting second proposal.

### Proof of Concept
```rust
// stacks-signer/src/chainstate/tests/v1.rs (test plan)
#[test]
fn v1_duplicate_tenure_change_not_caught_by_global_only_check() {
    // Setup: SignerDb with a BlockInfo for consensus_hash CH, block B1,
    // marked as Locally accepted / signed (signer_signature_hash = sig_hash_1),
    // but NOT GloballyAccepted (simulate quorum not yet reached).
    // e.g. signer_db.insert_block(&block_info_b1_locally_signed).unwrap();

    // sanity: get_last_globally_accepted_block returns None despite B1 existing
    assert!(signer_db.get_last_globally_accepted_block(&ch).unwrap().is_none());
    // but a "signed" lookup (as v2 uses) would find it:
    // assert!(signer_db.get_last_signed_block(&ch).unwrap().is_some());

    // Build a second tenure-change block B2 for the same CH/parent_tenure_id,
    // different signer_signature_hash (sig_hash_2), same miner pubkey.
    let result = sortitions_view.check_proposal(
        &client, &mut signer_db, &block_b2, false, ReplayTransactionSet::none(),
    );

    // BUG: current behavior
    assert!(result.is_ok(), "v1 path incorrectly accepts a conflicting second tenure-change block");

    // EXPECTED (post-fix) behavior:
    // assert_eq!(result, Err(RejectReason::DuplicateBlockFound));
}
```
This test demonstrates the equality that should hold — "at most one signed block per (signer, tenure CH)" — is violated because the v1 guard only checks `get_last_globally_accepted_block` instead of also covering locally-signed blocks.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L505-518)
```rust
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
```
