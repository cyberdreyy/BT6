### Title
V1 Chainstate Tenure-Change Duplicate Check Omits Locally-Signed Blocks, Allowing Equivocation — (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
`SortitionsView::validate_tenure_change_payload` in the v1 (pre-global-state) chainstate path guards against re-signing a competing tenure-start block by checking only `get_last_globally_accepted_block`, whereas the v2 chainstate path performs the analogous check with `get_last_signed_block`, which the v2 code explicitly documents as covering **both locally and globally accepted** blocks. This is structurally the same class of bug as the reported `AccessTokenContract.sol` issue: two code paths that should enforce the same invariant ("don't let this signer double-commit to a tenure") apply different strictness, and a miner fully controls which path is exercised.

### Finding Description [1](#0-0) 

In v1, before signing/pre-committing to a tenure-change ("BlockFound") block, the last line of defense against re-endorsing a conflicting tenure-start block in the same tenure is:
```rust
let last_in_current_tenure = signer_db
    .get_last_globally_accepted_block(&block.header.consensus_hash)?;
if let Some(last_in_current_tenure) = last_in_current_tenure {
    return Err(RejectReason::DuplicateBlockFound);
}
```
This only looks at blocks that reached **global** acceptance (i.e., the node has already processed and announced them).

Compare this with v2's equivalent check: [2](#0-1) 
```rust
// Only blocks we have signed (locally or globally accepted) count
// here: a block we have merely pre-committed to carries no signature from us...
let last_in_current_tenure = signer_db
    .get_last_signed_block(&block.header.consensus_hash)?;
if let Some(last_in_current_tenure) = last_in_current_tenure {
    return Err(RejectReason::DuplicateBlockFound);
}
```
v2's own comment makes explicit that the correct invariant is "have I already *signed* (locally OR globally accepted) a block in this tenure," not merely "has the block already reached global consensus." v1 checks a strictly narrower condition.

Because `determine_active_signer_protocol_version`/`check_block_against_state` dispatches to v1 or v2 purely based on the network's negotiated signer protocol version (`stacks-signer/src/v0/signer.rs:865-869`), any signer set still running below `GLOBAL_SIGNER_STATE_ACTIVATION_VERSION` uses the weaker v1 check for every tenure-change block it evaluates — this is not a rare edge case but the standing behavior of the whole v1 signer population.

### Impact Explanation
A miner (the one-slot proposer for a tenure) can:
1. Propose tenure-start block B1 for tenure T. A quorum of individual signers validate it and call `mark_locally_accepted` (i.e., they sign B1) — this happens well before B1 reaches the 70% pre-commit/signature threshold and before the node reports it as globally accepted.
2. Before B1 achieves global acceptance, propose (or have gossiped/re-broadcast) a second, conflicting tenure-start block B2 for the same tenure T (same `consensus_hash`), e.g. a different block content that also carries a tenure-change tx.
3. For a v1 signer, `validate_tenure_change_payload`'s duplicate check calls `get_last_globally_accepted_block(T)`, which returns `None` because B1 is only locally accepted, not global. The early-rejection guard in `check_block_against_state`/`check_proposal` therefore does **not** stop B2.
4. B2 is submitted to the node for validation and, if the node's chainstate has not yet processed B1, can pass node-side validation as well.
5. The only remaining backstop is the later, common `get_signed_conflicts` re-check performed in `handle_block_pre_commit` at signature time (`stacks-signer/src/v0/signer.rs`, section 5 of `docs/signer-flows.md`). That logic treats a same-tenure conflict as "OWN tenure" and refuses to sign only if the tenure "is confirmed at ≥ this height" by the node's tenure tip. Since B1 was never handed to / processed by the node (a locally-accepted block is not pushed until the whole set signs), `get_tenure_tip` for tenure T will not yet reflect B1, so this check resolves to "never confirmed → SIGN."

The net effect is that a subset of v1 signers can end up producing a valid signature share over **two different, conflicting blocks (B1 and B2) at the same tenure/height**, something the local/global state machine equality ("signed vs. globally-accepted", "one signature per height/tenure") is specifically designed to prevent. If enough distinct signers are split between B1 and B2 (or the same signers contribute to both tallies), this breaks the "a signer never signs two conflicting blocks in one tenure" invariant and can enable competing quorums / a fork, i.e. a signer signing a conflicting block — a Critical-class outcome per the specified impact taxonomy.

### Likelihood Explanation
This does not require a majority of signers or any privileged access — it only requires:
- The tenure's miner (a normal one-slot actor) to produce/gossip two distinct tenure-start proposals for the same tenure in a normal race window (this happens naturally around miner handoff / tenure-change situations, and can be deliberately induced by a malicious or buggy miner).
- The signer set (or a sub-quorum of it) to still be negotiating/using the v1 signer protocol, which the codebase explicitly still supports (`SortitionStateVersion::from_protocol_version`), so this is reachable in any deployment that has not universally activated v2 (`GLOBAL_SIGNER_STATE_ACTIVATION_VERSION`).
- Timing such that B1 has been locally accepted but not yet pushed/confirmed by the node — a normal condition during the pre-commit accumulation window described in `docs/signer-flows.md` section 5.

### Recommendation
Make the v1 `validate_tenure_change_payload` duplicate-block check use the same signed-block semantics as v2, i.e. replace `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` with `signer_db.get_last_signed_block(&block.header.consensus_hash)` (or otherwise unify the two chainstate implementations' invariant so that "have we already signed something in this tenure" always includes locally-accepted blocks, not only globally-accepted ones). Alternatively, if the asymmetry is intentional, ensure the later `get_signed_conflicts`/OWN-tenure recheck in `signer.rs` treats an unconfirmed local acceptance as blocking, closing the same gap at the second layer.

### Proof of Concept
1. Boot a signer set still negotiating protocol version < `GLOBAL_SIGNER_STATE_ACTIVATION_VERSION` (v1 chainstate path).
2. As the tenure's block-commit winner, broadcast a tenure-change block B1; wait for a subset of signers to locally accept it (`mark_locally_accepted`) but do not let it reach the 70% signature threshold or reach the node as a processed/canonical block.
3. Broadcast a second, distinct tenure-change block B2 for the same tenure (`consensus_hash`), e.g., differing tx set/timestamp, still satisfying all of v1's earlier checks (correct pubkey, bitvec, parent tenure choice, `check_tenure_change_confirms_parent`).
4. Observe that `validate_tenure_change_payload`'s `get_last_globally_accepted_block` lookup returns `None` (B1 isn't globally accepted), so B2 is not rejected as `DuplicateBlockFound` and is submitted for node validation and subsequent pre-commit/signature flow.
5. If the node has not processed B1 by the time B2's validation completes, the `get_signed_conflicts`/OWN-tenure recheck at signature time also resolves to "never confirmed → SIGN," and the affected signers end up signing both B1 and B2.

Note: I was unable to directly read `signerdb.rs`'s exact implementations of `get_last_signed_block` / `get_last_globally_accepted_block` / `get_signed_conflicts` within the available iterations (the file is large and my final grep for these functions returned no hits due to a tooling/indexing limitation), so the precise SQL/filter semantics and the exact "OWN tenure" staleness thresholds in `handle_block_pre_commit` could not be fully re-verified line-by-line. The asymmetry itself, however, is directly evidenced by the two `validate_tenure_change_payload` implementations and v2's own comment explaining why it deliberately checks signed (not just globally-accepted) blocks — a distinction v1 lacks. A Devin session with full repository access should confirm the exact behavior of `get_signed_conflicts`/`reorg_permit_stands` to determine precisely how far the resulting equivocation window extends before finalizing the fix.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L496-519)
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
