### Title
v1's `validate_tenure_change_payload` uses `get_last_globally_accepted_block` instead of `get_last_signed_block`, allowing a signer to sign two competing tenure-start blocks for the same tenure before global acceptance is reached - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionsView::validate_tenure_change_payload` in `v1.rs` gates a second tenure-start proposal for tenure `T` on `SignerDb::get_last_globally_accepted_block`, which only returns `Some` once a block in `T` has reached network-wide global acceptance. `GlobalStateView::validate_tenure_change_payload` in `v2.rs` instead calls `SignerDb::get_last_signed_block`, which also counts a block this signer has merely *locally* accepted (i.e., already signed, but not yet globally confirmed) as blocking a competing proposal. This divergence, explicitly called out in the `v2.rs` comment as an intentional strengthening, means a v1 signer that has already signed a locally-accepted (but not global) block B1 in `T` will still return `Ok(())` for a second, competing tenure-start proposal B2 in `T`, whereas a v2 signer in the identical DB state rejects it with `RejectReason::DuplicateBlockFound`.

### Finding Description
In `stacks-signer/src/chainstate/v1.rs`, `validate_tenure_change_payload` checks: [1](#0-0) 
using `get_last_globally_accepted_block`, which only returns a block once the network has reached the global-acceptance threshold for it.

In `stacks-signer/src/chainstate/v2.rs`, the equivalent check uses `get_last_signed_block`: [2](#0-1) 
The comment explicitly documents the semantic: "Only blocks we have signed (locally or globally accepted) count here: a block we have merely pre-committed to carries no signature from us, so it is safe to accept a competing tenure-start block in its place if it failed to reach consensus." This means v2 deliberately blocks re-proposals once *this signer* has emitted any signature (local or global) for a block in `T`, while v1 only blocks once the *network* has globally accepted a block in `T`.

The attack does not require majority signers or privileged access: a single winning miner slot can equivocate by gossiping two different tenure-start `BlockProposal`s for the same tenure `T` (same `consensus_hash`, different content/parent choice), both signed with the miner's own valid key. Each signer independently runs `check_proposal` → `validate_tenure_change_payload` against its own `SignerDb`. A v1 signer that has already locally accepted (and thus already signed) B1 for `T`, but for which global acceptance has not yet been recorded in its local `SignerDb`, will pass B2 through this specific check and may go on to sign B2 as well — a same-signer equivocation for tenure `T`. Nothing else in `check_proposal` (miner-pubkey match, bitvec check, `ProposedBy` matching, `check_parent_tenure_choice`) inherently prevents this, since those checks validate the sortition/miner legitimacy of B2 independently of whether the signer already signed a sibling block B1 for the same tenure.

### Impact Explanation
This breaks the equivocation-guard/uniqueness property for tenure-start blocks: the same signer can end up with signatures on two different, conflicting blocks at the same (tenure, height). If enough v1 signers hit this window simultaneously (which is amplified, but not strictly required, by mixed v1/v2 rollout, since v2 already fixes the same-signer case), it is possible for both B1 and B2 to accumulate partial signatures with no way to reconcile which is canonical, matching the Critical impact category (conflicting block with live partial signatures, chain-safety violation).

### Likelihood Explanation
Preconditions are attacker-controlled and cheap: win a single miner slot, and race two distinct tenure-start proposals to the signer set before global acceptance is recorded for the first one, exploiting the natural gossip delay of reaching global acceptance. No majority signer collusion, no compromised keys, and no local host access are needed — matching the "unprivileged attacker with one miner slot plus gossip" threat model. The window is bounded by how quickly global acceptance propagates, but is repeatable across tenures and does not require any specific mixed v1/v2 rollout, though such a rollout increases the fraction of signers vulnerable to this specific gap.

### Recommendation
Change `stacks-signer/src/chainstate/v1.rs::validate_tenure_change_payload` to use `SignerDb::get_last_signed_block` (matching v2's stricter, already-signed semantics) instead of `get_last_globally_accepted_block`, so that a signer refuses to consider any competing tenure-start proposal for `T` once it has already signed (locally or globally accepted) a block in `T`.

### Proof of Concept
```rust
// stacks-signer/src/chainstate/tests/ (new test)
// 1. Build a SignerDb with a single block B1 for tenure T inserted and
//    marked BlockState::LocallyAccepted (signed by this signer, not yet
//    globally accepted) via signer_db.insert_block(...) / block_state update helpers
//    used elsewhere in v1.rs/v2.rs tests.
// 2. Construct an otherwise-identical competing tenure-start NakamotoBlock B2
//    for the same tenure consensus_hash but a different signer_signature_hash.
// 3. Call SortitionsView::validate_tenure_change_payload(..., B2, &mut signer_db, ...)
//    from v1.rs and assert Ok(()) is returned (allows signing a second block).
// 4. Call GlobalStateView::validate_tenure_change_payload(..., B2, &mut signer_db, ...)
//    from v2.rs against the identical signer_db state and assert
//    Err(RejectReason::DuplicateBlockFound) is returned.
// 5. This demonstrates the exact divergence: v1 == Ok(()) vs v2 == Err(DuplicateBlockFound)
//    for the identical (tenure, signer_db, competing proposal) triple.
```

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
