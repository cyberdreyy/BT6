### Title
v1 `validate_tenure_change_payload` duplicate-block guard uses `get_last_globally_accepted_block` instead of `get_last_signed_block`, allowing a v1 signer to sign two conflicting tenure-change blocks for the same tenure - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
`v1::SortitionsView::validate_tenure_change_payload` checks for a prior signed block in the current tenure using `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)`, whereas the equivalent `v2::GlobalStateView::validate_tenure_change_payload` uses `signer_db.get_last_signed_block(&block.header.consensus_hash)`, which the v2 code comment explicitly states is intentional to also catch "blocks we have signed (locally or globally accepted)". Because a locally-signed-but-not-yet-globally-accepted block is invisible to `get_last_globally_accepted_block`, a v1 signer can be induced to sign a second, competing tenure-change block for a tenure it already signed a block in.

### Finding Description
The equality that must hold is: "a block already signed by this signer in tenure T" (i.e., visible via `get_last_signed_block`) must be recognized by `validate_tenure_change_payload`'s duplicate-block guard, in both the v1 and v2 chainstate implementations, so that a second tenure-change block for T can never be signed.

In `v2.rs::validate_tenure_change_payload` [1](#0-0)  the guard explicitly uses `get_last_signed_block`, with a comment stating blocks "we have signed (locally or globally accepted)" must count, precisely to prevent re-signing a competing tenure-start block in the same tenure.

In `v1.rs::validate_tenure_change_payload`, the analogous guard instead calls `get_last_globally_accepted_block`: [2](#0-1) . This only detects blocks that have reached global (majority-signer) acceptance status, not blocks the signer has locally signed but that have not yet crossed the acceptance threshold.

Exploit flow: A miner (attacker, one slot) gets a signer (v1 protocol) to sign a first block B1 in tenure T (a normal, valid block — not necessarily a tenure-change block), such that B1 is recorded in `signer_db` as signed (e.g., `signed_self`) but has not yet become globally accepted (insufficient other signers have responded yet, or the attacker deliberately withholds gossiping other signers' responses so the local signer never learns of global acceptance). The attacker (still holding the single miner slot) then proposes a second block B2, also carrying a `TenureChangePayload` claiming to start T's successor, to the same v1 signer. When `check_proposal` -> `validate_tenure_change_payload` runs for B2, `get_last_globally_accepted_block(&T)` returns `None` because B1 is not globally accepted yet, so the duplicate-block check silently passes, and the signer proceeds to sign B2 — a tenure-change block conflicting with B1 within the same tenure T.

Existing guards do not prevent this: `check_tenure_change_confirms_parent` only validates that the *parent* tenure's last block is properly confirmed; it does not inspect whether *this* tenure (T) already has a signed block. The `is_valid_parent_tenure` / `check_parent_tenure_choice` check similarly evaluates tenure choice for the parent, not duplicate signing within T. Thus nothing else in the v1 path re-derives the `get_last_signed_block` equality that v2 explicitly relies on.

### Impact Explanation
This breaks the **uniqueness** safety property: the same signer produces two valid signatures for conflicting tenure-change blocks that both claim to be the canonical start of T's successor tenure. If enough other signers (some potentially also v1, or independently fooled) sign each of the two competing blocks, this can result in two distinct blocks both claiming valid signer aggregate signatures for successor tenures rooted at the same parent, a chain-safety violation (Critical), matching "a signer signing an invalid, non-canonical, or conflicting block."

### Likelihood Explanation
Preconditions: a mixed-version signer set with at least one signer still running the v1 chainstate protocol, and a tenure T where that signer has locally signed (but not globally accepted) a block. The attacker needs only their own miner slot (to control block proposal timing/content) and the ability to gossip a BlockProposal — no majority signer weight, no key compromise, and no auth_token. The race window (locally-signed but not-yet-globally-accepted) is a normal and not-infrequent network state, especially with network delay or if the attacker times the second proposal to arrive before the signer's peers' StackerDB responses converge to global acceptance. This is repeatable per-tenure against any v1 signer.

### Recommendation
Change `v1::SortitionsView::validate_tenure_change_payload`'s duplicate-block check to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` instead of `get_last_globally_accepted_block`, matching the v2 implementation and its documented rationale (locally-signed-only blocks must also block a competing tenure-change proposal in the same tenure).

### Proof of Concept
Rust test plan (in `stacks-signer/src/chainstate/tests/v1.rs` or similar, mirroring the existing v2 test structure referenced in `stacks-signer/src/chainstate/tests/v2.rs`):
1. Construct a `SignerDb` (temp/in-memory) and insert a `BlockInfo` for tenure `T` (consensus_hash `ch_t`) with state `signed_self` (locally signed) but not `BlockState::GloballyAccepted`.
2. Build a `SortitionsView` (v1) with `cur_sortition` pointing at a successor sortition whose `parent_tenure_id == ch_t`.
3. Construct a second `NakamotoBlock` carrying a `TenureChangePayload` with `prev_tenure_consensus_hash == ch_t`, satisfying `check_tenure_change_confirms_parent` and `check_parent_tenure_choice`.
4. Call `SortitionsView::validate_tenure_change_payload(proposed_by, tenure_change, &block2, &mut signer_db, &client)`.
5. Assert (bug reproduction): result is `Ok(())` — i.e., the duplicate is NOT caught, whereas it should return `Err(RejectReason::DuplicateBlockFound)`.
6. Contrast assertion for the fix: after switching to `get_last_signed_block`, re-run and assert `Err(RejectReason::DuplicateBlockFound)` is returned, matching the v2 behavior verified with `get_last_signed_block` in `stacks-signer/src/chainstate/tests/v2.rs`.

### Citations

**File:** stacks-signer/src/chainstate/v2.rs (L340-357)
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
```

**File:** stacks-signer/src/chainstate/v1.rs (L505-519)
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
        Ok(())
```
