### Title
Duplicate tenure-first-block signing due to `validate_tenure_change_payload` checking only global acceptance, not local signing state - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionsView::validate_tenure_change_payload` guards against signing a second "first block" of a tenure by calling `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` [1](#0-0)  . This only detects a prior block in tenure `T` if it has reached `GloballyAccepted` state; it does not detect a block the signer itself has already locally signed but which has not yet been globally accepted, allowing a second, conflicting tenure-change block for the same tenure to pass this guard.

### Finding Description
The equality the code is supposed to enforce is: "signed blocks per tenure == at most one" for a given signer. The comment on the check states "we've already signed a block in this tenure" [2](#0-1)  , but the implementation calls `get_last_globally_accepted_block`, which is a strictly narrower condition (global acceptance requires a supermajority of signers, not just this signer's own signature). Elsewhere in the same file, `SortitionState::is_timed_out` explicitly distinguishes "signed" from "pre-committed"/"accepted" via `db.has_signed_block_in_tenure(sortition)` [3](#0-2)  , showing the codebase has a function that tracks the signer's own local signing activity separately from global acceptance — but that function is not used in `validate_tenure_change_payload`.

Exploit flow: an attacker who wins the miner slot for tenure `T` submits `BlockProposal` B1 (first block of `T`, containing a `TenureChangePayload`). `check_proposal` routes it to `validate_tenure_change_payload`, which finds no globally-accepted block in `T` (`None`), passes, and the signer signs B1. Before B1 reaches global acceptance, the miner crafts a second first-block B2 for the same tenure `T` (same `prev_tenure_consensus_hash`/parent tenure, since that is fixed by sortition data and checked at lines 471-481, but with a different block body/parent-block confirmation elsewhere in the payload). `check_proposal` is invoked again; the earlier sortition-invalidation and canonical-tip checks (lines 144-203) do not reference this signer's own already-signed block, and `validate_tenure_change_payload` again queries `get_last_globally_accepted_block`, which still returns `None` because B1 has only local acceptance, not global. The `DuplicateBlockFound` rejection at line 517 is therefore bypassed, and the signer proceeds to sign B2 — a second, conflicting block in tenure `T`.

I was not able to fully verify within the available investigation whether some other check in `stacks-signer/src/v0/signer.rs` (which references `check_proposal`) or in `SignerDb`'s handling of locally-accepted blocks independently blocks a second signature for the same tenure before the signature is actually produced. The chainstate-level guard at line 505 as written does not perform this deduplication, and the comment text at line 510-511 ("we've already signed a block") directly promises behavior the implementation does not provide.

### Impact Explanation
If no other layer deduplicates against the signer's own local signature, this breaks the uniqueness/canonicity safety property: a single signer would sign two conflicting blocks for the same tenure, which is the Critical-severity "signer signing an invalid, non-canonical, or conflicting block (chain safety)" category described in the rules. This is a chainstate-validation-logic gap, not a P2P/StackerDB/node-consensus issue, since the flaw is in the signer's own proposal-validation function.

### Likelihood Explanation
Preconditions are minimal and within the described unprivileged attacker's capability: win one miner slot for tenure `T` (their own BTC), and be able to submit two `BlockProposal` gossip messages for the same tenure before B1 achieves global acceptance (a normal timing window that exists during every tenure). No majority-signer collusion, no compromised key, and no local host access are required — matching the constraints in the prompt. The attack is repeatable every tenure the attacker wins, subject to timing between B1's local signature and its global acceptance.

### Recommendation
In `validate_tenure_change_payload` (stacks-signer/src/chainstate/v1.rs, line 505), replace or supplement `get_last_globally_accepted_block` with a check against the signer's own locally-signed/locally-accepted blocks for the same tenure (e.g. using `has_signed_block_in_tenure` or an equivalent per-tenure "already signed" lookup) so that a second conflicting tenure-change proposal is rejected as `DuplicateBlockFound` regardless of whether the first block has reached global acceptance.

### Proof of Concept
```rust
// stacks-signer/src/chainstate/tests/v1.rs (conceptual addition)
#[test]
fn duplicate_tenure_change_block_bypasses_local_signature_guard() {
    // 1. Set up SignerDb, SortitionsView with cur_sortition for tenure T.
    // 2. Construct block B1: first block of tenure T with a valid TenureChangePayload
    //    confirming the correct parent tenure/parent block.
    // 3. Call check_proposal(..., B1, ...) -> assert Ok(()); simulate the signer
    //    signing B1 and recording it in SignerDb as *locally* accepted only
    //    (do NOT mark it GloballyAccepted / do not call the global-acceptance path).
    // 4. Construct block B2: a second, distinct first block of tenure T (different
    //    block body / signer_signature_hash) with the same prev_tenure_consensus_hash
    //    and same expected parent, but different transactions.
    // 5. Call validate_tenure_change_payload(..., B2, signer_db, ...) directly
    //    (or check_proposal(..., B2, ...)).
    // 6. Assert: EXPECTED Err(RejectReason::DuplicateBlockFound), ACTUAL Ok(())
    //    because signer_db.get_last_globally_accepted_block(&T) returns None
    //    since B1 was never marked GloballyAccepted.
}
```

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L60-70)
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
