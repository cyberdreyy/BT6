### Title
v1 `validate_tenure_change_payload` allows double-signing a tenure via `get_last_globally_accepted_block` instead of `get_last_signed_block` - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionsView::validate_tenure_change_payload` in the v1 chainstate module only rejects a second tenure-change proposal for a tenure if the signer's *prior* signed block in that tenure reached **global** acceptance. The v2 implementation was changed specifically to reject on **any** locally-signed block, with an explicit code comment stating the rationale. This means a v1 signer can be tricked into signing two conflicting blocks for the same tenure whenever its first signature never reached global acceptance.

### Finding Description
The uniqueness invariant the codebase intends to enforce is: *"a signer's own already-signed block in a tenure must block any second tenure-change proposal in that tenure."* v2 implements this correctly: [1](#0-0) 

The comment at v2.rs is explicit about the design intent: only a block the signer merely "pre-committed to" (no signature emitted) is safe to override; anything the signer actually *signed* — locally or globally accepted — must block a competing tenure-change block via `get_last_signed_block`.

v1, however, performs the duplicate check using `get_last_globally_accepted_block`, not `get_last_signed_block`: [2](#0-1) 

Because `get_last_globally_accepted_block` only returns a hit once threshold (globally-accepted) consensus was reached for a block in that tenure, a v1 signer that has already *locally* signed block A for tenure T (i.e., `signed_self` is set for that block, but global acceptance never occurred — e.g., due to network partition, competing proposals, or simply not enough signers responding before a new proposal arrives) will see `get_last_globally_accepted_block` return `None`. The `validate_tenure_change_payload` check then passes, and the v1 signer proceeds to validate/sign a second, conflicting tenure-change block B for the same tenure T.

Attack flow (attacker needs only one miner slot):
1. Attacker wins a sortition and gets a miner slot; crafts BlockProposal A with a tenure-change payload for tenure T, gossips it via StackerDB.
2. A v1-running signer validates and signs A (`signed_self` recorded in its SignerDB) but A fails to reach global (threshold) acceptance — e.g., attacker (or network conditions) prevents enough other signers from responding in time, or mixed-version behavior stalls quorum.
3. Attacker crafts a second, conflicting BlockProposal B, also carrying a tenure-change payload for the same tenure T (same `prev_tenure_consensus_hash`, satisfying `check_tenure_change_confirms_parent`/`check_parent_tenure_choice`), and gossips it.
4. The v1 signer's `validate_tenure_change_payload` calls `get_last_globally_accepted_block(&block.header.consensus_hash)`, which returns `None` (block A was never globally accepted), so the duplicate-block guard is skipped, and the signer proceeds to sign B.
5. The same signer has now signed two conflicting blocks (A and B) at the same tenure-change boundary — violating UNIQUENESS.

The existing guards elsewhere (parent-tenure check, `check_tenure_change_confirms_parent`, `check_parent_tenure_choice`) do not catch this because they only validate the *tenure lineage*, not whether the signer itself has already signed a competing block within the same tenure — that is precisely the job of the line 505 check, which uses the wrong SignerDB accessor in v1.

### Impact Explanation
This breaks the UNIQUENESS chain-safety property: a signer must never sign two conflicting blocks for the same tenure slot. If enough v1-version signers are simultaneously induced into this state during a mixed-version upgrade window, their signatures on both A and B could combine to produce two independently valid, conflicting signature sets for the same tenure, which is a Critical-severity chain-safety violation ("a signer signing an invalid, non-canonical, or conflicting block").

### Likelihood Explanation
Preconditions: at least one signer must be running v1 validation logic (mid-upgrade signer sets are explicitly acknowledged as a real operational scenario), and that signer must have locally signed a block that failed to reach global acceptance in a given tenure before the attacker's second competing proposal arrives. The attacker needs no privilege beyond a single winning miner slot (to legitimately produce a NakamotoBlock via a sortition win) and the ability to gossip a second BlockProposal — both squarely within the allowed attacker capability set. This is repeatable in every tenure where a v1 signer's first signed block stalls before reaching quorum, which is plausible any time the signer set is not perfectly synchronous (e.g., timeouts, races, or an attacker deliberately delaying propagation of the first proposal to a subset of signers to prevent quorum before sending the second proposal).

### Recommendation
Update `SortitionsView::validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs` to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` instead of `get_last_globally_accepted_block`, matching v2's semantics, so that any block the signer has already signed (locally or globally accepted) blocks a competing tenure-change proposal in the same tenure.

### Proof of Concept
Rust test plan (to be placed alongside existing chainstate tests, e.g. in `stacks-signer/src/chainstate/v1.rs`/`v2.rs` test modules):
1. Construct a `SignerDb` (temp sqlite) and insert a `BlockInfo` for block `A` in tenure `T` with `signed_self` set (locally signed) but without global acceptance recorded (i.e., `get_last_globally_accepted_block(T)` returns `None`, `get_last_signed_block(T)` returns `Some(A)`).
2. Build a second `NakamotoBlock` `B` with a `TenureChangePayload` for the same tenure `T` (same `prev_tenure_consensus_hash`, satisfying the parent-tenure checks), and a `ProposedBy` structure that passes `check_tenure_change_confirms_parent`/`check_parent_tenure_choice`.
3. Call `v1::SortitionsView::validate_tenure_change_payload(..., block: &B, signer_db: &mut db, ...)` — assert it returns `Ok(())`.
4. Call the equivalent `v2::validate_tenure_change_payload(..., block: &B, signer_db: &mut db, ...)` on the same DB state — assert it returns `Err(RejectReason::DuplicateBlockFound)`.
5. This differential assertion (`Ok` in v1 vs `Err(DuplicateBlockFound)` in v2 for identical DB/tenure/block state) directly demonstrates the UNIQUENESS-guard discrepancy exploitable by an attacker crafting the second competing tenure-change proposal.

### Citations

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
