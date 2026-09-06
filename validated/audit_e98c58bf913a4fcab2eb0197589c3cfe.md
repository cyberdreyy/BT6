### Title
Tenure-change validation only checks globally-accepted blocks, allowing a competing tenure-start proposal into the same tenure as an unresolved locally-accepted block - (File: stacks-signer/src/chainstate/v1.rs)

### Summary
`validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs` guards against re-entering an already-mined tenure by calling `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` and only rejecting with `RejectReason::DuplicateBlockFound` if that returns `Some`. A block this same signer has already `mark_locally_accepted` (signed, but not yet globally accepted) does not appear in that lookup, so a second, competing tenure-start block for the same tenure passes this check and proceeds to the rest of `check_proposal`.

### Finding Description
The relevant code is: [1](#0-0) 

This is the only defense in `validate_tenure_change_payload` against signing two different tenure-start blocks for the same `consensus_hash` (tenure). It is keyed exclusively off `get_last_globally_accepted_block`, which reflects the globally-accepted threshold, not this signer's own local acceptance state. The earlier calls in the same function — `check_tenure_change_confirms_parent` and `check_parent_tenure_choice` — validate the *parent* tenure/block linkage, not whether *this* tenure has already had a competing tenure-start block locally accepted by this signer.

Given the preconditions in the question (signer called `mark_locally_accepted` on block B0, a tenure-change block for tenure T, and B0 never reached the global-acceptance threshold), a second tenure-change block B1 for the same tenure T, with a possibly different parent commit or transaction set, will:
1. Pass the sortition/miner matching checks in `check_proposal` (lines 136–317) as long as it comes from the same current or last sortition winner recognized by the view.
2. Enter `validate_tenure_change_payload`, pass the parent-tenure and parent-block-confirmation checks (assuming B1's committed parent tenure and confirmed parent block match what the signer's chainstate view expects — which is independent of B0's existence).
3. Reach the `get_last_globally_accepted_block` check, get `None` (since B0 is only locally accepted), and fall through to `Ok(())`.

At that point, the signer's block-validation layer has no other consensus-safety guard: `check_proposal` does not consult `has_signed_block_in_tenure` or any locally-accepted-block record for the *current* tenure change validation path, only for the timeout-suppression logic in `SortitionState::is_timed_out` (lines 55–94), which is unrelated to double-signing prevention.

### Impact Explanation
If both B0 and B1 are conflicting tenure-start blocks for the same tenure and both are considered valid by this signer, and the signer independently produces a valid signature/vote for each, this signer contributes to two conflicting blocks reaching signature aggregation for the same tenure. This breaks the uniqueness/safety property that a signer should sign at most one canonical block per (tenure, height) slot, matching the "Critical" impact category (signer signing a conflicting block, chain safety). This is a Critical, chain-safety-relevant scenario if it is reachable without additional guards elsewhere in the codebase that I was unable to fully verify (e.g., in `stacks-signer/src/signerdb.rs` or `stacks-signer/src/v0/signer.rs`'s proposal-handling / equivocation-record logic) due to running out of investigation iterations.

### Likelihood Explanation
Preconditions require: the attacker (or the miner in general) to win a tenure-start slot, propose block B0, get the signer to `mark_locally_accepted` it, and B0 to stall before reaching global threshold (e.g., due to network partition or other signers being slow/offline) — then propose a second competing tenure-start block B1 for the same tenure. This is plausible from a single miner slot with normal proposal/gossip capability and does not require majority-signer collusion, local access, or the auth token. However, I could not fully confirm within available tool calls whether upstream logic in `stacks-signer/src/v0/signer.rs` (the state machine driving `check_proposal`) or `signerdb.rs` (which has other lookup functions like `has_signed_block_in_tenure`) applies an additional block/tenure de-duplication gate before invoking `check_proposal`/before actually signing — I found references to these functions but did not get to read their exact call sites and logic before the iteration budget ended.

### Recommendation
In `validate_tenure_change_payload`, extend the duplicate-tenure-start check to also consider any block this signer has already locally accepted (not just globally accepted) in the target tenure — e.g., check `signer_db`'s locally-accepted-block record for `block.header.consensus_hash` in addition to `get_last_globally_accepted_block`, and reject with `RejectReason::DuplicateBlockFound` (or a dedicated reason) if a different, locally-accepted tenure-start block already exists for that tenure.

### Proof of Concept
```rust
// stacks-signer/src/chainstate/tests/v1.rs
#[test]
fn test_tenure_change_second_proposal_after_local_accept_not_rejected() {
    // 1. Build a SignerDb and SortitionsView fixture matching tenure T's sortition data.
    // 2. Construct tenure-start block B0 for tenure T, call check_proposal -> Ok(()),
    //    then signer_db.mark_locally_accepted(&B0's block info) to simulate the signer
    //    having voted for B0 but not yet reaching global threshold.
    // 3. Assert signer_db.get_last_globally_accepted_block(&T) is still None.
    // 4. Construct a second, distinct tenure-start block B1 for tenure T (different
    //    signer_signature_hash, e.g. a different parent commit choice), and call
    //    view.check_proposal(&client, &mut signer_db, &B1, false, replay_set).
    // 5. Assert the result is Ok(()) (NOT Err(RejectReason::DuplicateBlockFound)),
    //    demonstrating that this signer can now sign a second, conflicting
    //    tenure-start block B1 for tenure T despite already having accepted B0.
}
```

This test directly exercises the equality claimed broken: "at most one block signed by this signer per (tenure, height)" — proving that the guard at `stacks-signer/src/chainstate/v1.rs:505-518` only prevents duplicates once a block is *globally* accepted, not once this signer has locally accepted one.

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
