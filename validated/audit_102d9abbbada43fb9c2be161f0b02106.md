### Title
Signer can sign two conflicting tenure-start blocks when the first sibling is only locally, not globally, accepted - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`validate_tenure_change_payload` guards against signing a second tenure-start block in the same tenure by calling `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)`, but this only detects a prior sibling that has already crossed the network-wide global-acceptance threshold. A sibling that this signer has locally accepted/signed but that never reached global acceptance is invisible to this check, so `check_proposal` incorrectly returns `Ok` for a second, conflicting tenure-start block in the same tenure.

### Finding Description
In `SortitionsView::validate_tenure_change_payload` (stacks-signer/src/chainstate/v1.rs:461-520), the only defense against re-signing a duplicate/competing tenure-change block is: [1](#0-0) 
This uses `SignerDb::get_last_globally_accepted_block`, which only returns a result once a block has crossed the global-acceptance (network-quorum) threshold recorded in `signerdb.rs`. It does not consult any "have I already signed a block in this tenure" state.

Contrast this with `SortitionState::is_timed_out` in the same file, which explicitly uses the stronger `db.has_signed_block_in_tenure(sortition)` check ("if we've already signed a block in this tenure ... the miner can't have timed out") specifically because a pre-commit/local acceptance carries no network guarantee but does represent an actual signature this signer produced: [2](#0-1) 

`validate_tenure_change_payload` does not reuse this stronger check. This is an inconsistency: the codebase already recognizes (in `is_timed_out`) that "signed" state (including local-only acceptance) must be tracked distinctly from "globally accepted" state, and that a locally-accepted-but-not-globally-accepted block still represents a real commitment by this signer. Yet the duplicate-tenure-start guard in `validate_tenure_change_payload` ignores that weaker-but-real commitment and only rejects a second sibling once the first sibling is *already finalized network-wide* — at which point the damage (signing a second, conflicting block) has already happened.

Attack flow (attacker holds the single miner slot for the tenure, needs no signer collusion):
1. Attacker (as tenure's winning miner) proposes tenure-start block A (`BlockProposal`) for consensus hash `CH`. The victim signer runs `check_proposal` → `validate_tenure_change_payload`, no globally-accepted block yet exists for `CH`, checks pass, signer signs A and its `SignerDb` entry for A becomes `Locally Accepted`/`Signed` (but the network as a whole never reaches quorum on A — e.g., attacker deliberately withholds signature aggregation or other signers are slow/offline).
2. Attacker crafts a second, distinct tenure-start block B for the same consensus hash `CH` (different transaction set / parent choice consistent with the same `prev_tenure_consensus_hash`), and gossips it as a new `BlockProposal`.
3. Victim signer's `check_proposal` runs again: `signer_db.get_last_globally_accepted_block(CH)` still returns `None` because A never reached global acceptance. The `DuplicateBlockFound` branch is skipped, and all other checks (parent tenure choice, pubkey, bitvec, etc.) pass since B is a legitimate proposal from the same still-valid miner.
4. The signer signs B as well, producing two distinct, conflicting signed blocks (A and B) at the same tenure-start height, signed by the same signer key.

### Impact Explanation
This breaks the intended uniqueness invariant "at most one block signed per (tenure, height) by a given signer," which underlies chain safety: a signer that has signed two conflicting blocks for the same tenure-start slot can contribute a valid signature to whichever sibling eventually assembles a quorum, or (worse) contribute to both siblings reaching partial signature sets, undermining the assumption that signers police the miner into producing a single canonical chain. This matches the Critical category: "a signer signing an invalid, non-canonical, or conflicting block (chain safety)."

### Likelihood Explanation
The precondition is narrow but realistic: the attacker must be the tenure's winning miner (achievable with a single BTC-won slot) and must be able to prevent/delay the first sibling from reaching global acceptance across the signer set (e.g., because they intentionally send it to only a subset of signers, or network conditions naturally delay quorum). No majority-signer collusion, no compromised keys, and no local host access are required — only crafting and gossiping two conflicting `BlockProposal`/StackerDB messages, which is within the stated attacker capability. It is repeatable for any tenure where the attacker wins the sortition and can control block propagation timing to delay global acceptance of the first sibling.

### Recommendation
In `validate_tenure_change_payload`, replace or augment the `get_last_globally_accepted_block` check with a check for any block this signer has already signed/locally-accepted in the tenure (e.g., reuse `SignerDb::has_signed_block_in_tenure`, or compare against the locally-accepted block record keyed by `consensus_hash`), rejecting the new tenure-change proposal with `DuplicateBlockFound` unless the new block is identical (same `signer_signature_hash`) to the one already signed.

### Proof of Concept
Rust test plan (stacks-signer/src/v0/tests.rs), mirroring `run_sibling_scenario`:
1. Set up a signer and `SignerDb` with a single-tenure sortition view (`SortitionsView` for consensus hash `CH`).
2. Build tenure-start sibling block `A` with a valid `TenureChangePayload` referencing `CH`; call `check_proposal` and assert `Ok(())`; then simulate signing by writing `A` into `signer_db` with block state `Locally Accepted`/`Signed` (not `Globally Accepted`) — i.e., do **not** call the path that would mark it globally accepted.
3. Assert `signer_db.get_last_globally_accepted_block(&CH)` returns `None` (confirming the precondition).
4. Build a second, distinct tenure-start sibling block `B` for the same `CH` (different tx set, same `prev_tenure_consensus_hash`/parent choice) and call `check_proposal` again.
5. Assert `check_proposal` wrongly returns `Ok(())` for `B` (instead of `Err(RejectReason::DuplicateBlockFound)`).
6. Assert that both `A` and `B` are now present in `signer_db` as signed/locally-accepted blocks for the same tenure/height, demonstrating the signer would produce two conflicting `BlockResponse::Accepted` signatures for the same tenure-start slot.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L60-71)
```rust
        // If we've already signed a block in this tenure, the miner can't have timed out: we have
        // committed a signature to this tenure and must not help abandon it.
        //
        // Importantly, a block we have only pre-committed to does not count! A pre-commit carries
        // no signature, and if it never reaches the pre-commit threshold the tenure can stall
        // indefinitely. Treating it as signed here would suppress the inactivity timeout for
        // exactly the signers that pre-committed, so they could never fall back to the prior
        // miner and the tenure could never recover.
        let has_block = db.has_signed_block_in_tenure(sortition)?;
        if has_block {
            return Ok(false);
        }
```

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
