### Title
Signer's own duplicate-tenure-block guard uses `get_last_globally_accepted_block` instead of `get_last_signed_block` in `chainstate/v1.rs`, allowing a single signer to sign two conflicting tenure-start blocks - (File: stacks-signer/src/chainstate/v1.rs)

### Summary
`SortitionsView::validate_tenure_change_payload` in v1 checks `signer_db.get_last_globally_accepted_block(...)` to detect whether the signer has already committed to a block in the current tenure before signing a competing tenure-start block, whereas the v2 equivalent (`GlobalStateView::validate_tenure_change_payload`) uses `signer_db.get_last_signed_block(...)`, which additionally covers `LocallyAccepted` blocks. Because a locally-accepted (signed) block that has not yet reached the global-accept threshold is invisible to the v1 check, a v1 signer can be induced to sign a second, conflicting tenure-start block for the same tenure.

### Finding Description
The invariant the guard is supposed to enforce is: "a signer signs at most one tenure-start block per tenure" (uniqueness of the signed block per tenure-start slot). In `validate_tenure_change_payload` (v1.rs lines 505-518), the check is:
```
let last_in_current_tenure = signer_db
    .get_last_globally_accepted_block(&block.header.consensus_hash)?;
if let Some(last_in_current_tenure) = last_in_current_tenure {
    return Err(RejectReason::DuplicateBlockFound);
}
```
This only rejects the new proposal if a prior block for the tenure has reached `GloballyAccepted` state. `get_last_globally_accepted_block` filters by that specific block state [1](#0-0) .

The v2 code performs the analogous check with `get_last_signed_block`, and its comment explicitly states the rationale: "Only blocks we have signed (locally or globally accepted) count here: a block we have merely pre-committed to carries no signature from us..." [2](#0-1) . This confirms that the intended equivocation guard is meant to cover any block the signer has *signed* (`LocallyAccepted` or `GloballyAccepted`), not merely globally-accepted ones — and that v1 diverges from this by checking only global acceptance.

Exploit flow: a miner (attacker, one slot) proposes `block1` as the tenure-start block for tenure T. A v1 signer runs `check_proposal` → `validate_tenure_change_payload`, finds no prior block (globally or otherwise) for T, and signs `block1`, moving it to `LocallyAccepted` in its own `signerdb` (the signer's own database, not requiring global threshold). Before `block1` reaches `GloballyAccepted` (e.g., insufficient other signers have signed yet, or the attacker deliberately withholds/delays gossip of the resulting `BlockResponse`s to prevent aggregation), the same miner proposes `block2`, a second, conflicting tenure-start block for the same tenure T. The v1 signer's `validate_tenure_change_payload` calls `get_last_globally_accepted_block(&T)`, which returns `None` because `block1` is only `LocallyAccepted`, so the duplicate-block guard does not trigger, and the v1 signer proceeds to sign `block2` as well — violating uniqueness by having the *same signer* produce signatures over two conflicting tenure-start blocks in the same tenure. This is strictly stronger than the mixed-version scenario in the prompt (it doesn't even require a v2 signer subset): a single v1 signer alone can be made to equivocate.

This is possible because none of the surrounding guards close the gap: `check_parent_tenure_choice` and `check_tenure_change_confirms_parent` validate the parent-tenure/parent-block relationship, not whether the signer already signed a competing block for the *same* tenure; and `is_timed_out`'s "has_signed_block_in_tenure" fail-safe is only used for the inactivity-timeout path (line 68 of v1.rs), not for the tenure-change duplicate check.

### Impact Explanation
This breaks the uniqueness safety property described in the target equality ("at most one signed block per tenure-start slot"). If enough total signer weight is accumulated this way across the two competing tenure-start blocks (each individual signer only needs to be induced through this single-block asymmetry, and the aggregate signer set can independently reach this state per-signer), two conflicting tenure-start blocks can each collect real, valid threshold-weight signature sets, enabling a chain split rooted at that tenure. This matches the "Critical" impact category: a signer signing a conflicting block, breaking chain safety/uniqueness.

### Likelihood Explanation
Preconditions: v1 protocol signers must be running (v1 `SortitionsView` code path, e.g. during/after an upgrade window, or simply any deployment still running v1 logic), and the attacker must win a single miner slot for tenure T. No majority of signers, no compromised keys, and no auth_token are needed — the attacker only crafts two competing `BlockProposal` messages for the same tenure and gossips them at the right time (before `block1`'s `BlockResponse`s aggregate to global acceptance). This is a repeatable per-tenure attack against any v1-code signer and requires only ordinary miner-slot economics plus network-timing control over gossip of the second proposal, which is within the attacker's granted capabilities (craft `BlockProposal`s and gossip signer messages).

### Recommendation
Change `validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs` to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` (matching v2's logic) instead of `get_last_globally_accepted_block`, so that any block the signer has already signed (`LocallyAccepted` or `GloballyAccepted`) for the tenure blocks a second, conflicting tenure-start proposal from being signed.

### Proof of Concept
Add a test in `stacks-signer/src/chainstate/tests/v1.rs` mirroring `check_tenure_change_rejects_when_locally_accepted_block_exists` (present in `stacks-signer/src/chainstate/tests/v2.rs`), but driving `v1::SortitionsView::check_proposal` / `validate_tenure_change_payload`:
1. Build a `SignerDb` and insert `block1` as a tenure-start block for consensus hash `T`, and mark it `LocallyAccepted` (signed, sub-threshold) via the same `BlockInfo`/`SignerDb` APIs used in the v2 test, without marking it `GloballyAccepted`.
2. Construct `block2`, a second tenure-start `NakamotoBlock` for the same consensus hash `T` (different `signer_signature_hash`, differing parent/coinbase), with a valid `TenureChangePayload` pointing at the same parent tenure.
3. Call `SortitionsView::check_proposal(...)` (v1) with `block2`.
4. Assert that the current v1 code returns `Ok(())` (bug reproduced), then assert that after applying the fix (switching to `get_last_signed_block`), the same call returns `Err(RejectReason::DuplicateBlockFound)` — i.e., add the assertion `assert_eq!(result, Err(RejectReason::DuplicateBlockFound))` as the target post-fix behavior, matching the v2 test `check_tenure_change_rejects_when_locally_accepted_block_exists`'s assertion structure.

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

**File:** stacks-signer/src/chainstate/v2.rs (L340-358)
```rust
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
