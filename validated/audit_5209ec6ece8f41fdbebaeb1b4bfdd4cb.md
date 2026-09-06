### Title
V1 tenure-change duplicate-block guard checks only globally-accepted state, allowing a second conflicting tenure-change block to be signed when the first was never globally confirmed - (File: stacks-signer/src/chainstate/v1.rs)

### Summary
`validate_tenure_change_payload` guards against signing two tenure-change blocks for the same tenure by calling `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)`, which only returns a value once a block has reached `GloballyAccepted` state. If the signer previously signed a tenure-change block B1 for `consensus_hash` CH but the network never drove B1 to global acceptance (e.g., it stayed `LocallyAccepted`/`SignatureGathered`), the lookup returns `None` and the duplicate-block guard is silently skipped, letting the signer sign a conflicting B2 for the same CH.

### Finding Description
The relevant code is: [1](#0-0) 

```rust
let last_in_current_tenure = signer_db
    .get_last_globally_accepted_block(&block.header.consensus_hash)
    .map_err(...)?;
if let Some(last_in_current_tenure) = last_in_current_tenure {
    ...
    return Err(RejectReason::DuplicateBlockFound);
}
Ok(())
```

The intended invariant is "at most one tenure-change block signed per tenure" (UNIQUENESS). The code instead implements "at most one *globally accepted* tenure-change block per tenure," which is a strictly weaker condition. `get_last_globally_accepted_block` is defined in `stacks-signer/src/signerdb.rs` and only returns a row once a block's recorded state has advanced to `GloballyAccepted`; a block this signer itself signed but that never accumulated enough network-wide signatures (still `LocallyAccepted` or in `SignatureGathered`) is invisible to this query.

Exploit flow: an attacker who wins a miner slot proposes tenure-change block B1 for consensus_hash CH. The signer runs `check_proposal` → `validate_tenure_change_payload`, the guard finds no prior globally-accepted block for CH, and the signer signs B1. Because the attacker controls block/message crafting and only needs the target signer's own vote (no majority-signer collusion needed) to prevent B1 from reaching global acceptance in the signer's own view — or simply times the second proposal before the global-acceptance record is written/propagated back — B1 remains non-globally-accepted in `signer_db`. The attacker then submits B2, also a tenure-change payload for the same consensus_hash CH but pointing at a different/conflicting hash. `validate_tenure_change_payload` runs the same guard, `get_last_globally_accepted_block(CH)` again returns `None`, and the guard is bypassed, so `check_proposal` returns `Ok(())` for B2 as well, and the signer signs a second, conflicting tenure-change block at the same tenure.

Other checks in `check_proposal` (miner pubkey/status checks, `check_parent_tenure_choice`, tip alignment) do not address block-level duplication within the same tenure — they validate the sortition/miner and parent-tenure choice, not whether this exact signer already signed a competing block for CH. So none of them close this gap.

### Impact Explanation
This breaks the UNIQUENESS safety property: a single signer can be induced to co-sign two conflicting tenure-change blocks (B1 and B2) at the same tenure/height. If enough signers are similarly starved of global-acceptance confirmation for B1 before B2 arrives, both blocks can separately accumulate enough signatures to be considered valid by different observers, producing a chain split. This matches the Critical severity category: "a signer signing an invalid, non-canonical, or conflicting block (chain safety)."

### Likelihood Explanation
Preconditions: the attacker needs only a single won miner slot (their own BTC) plus the ability to gossip two distinct block proposals for the same consensus_hash before the first one's global-acceptance status propagates back into the target signer's `signer_db`. This is a plausible race — global acceptance requires waiting for enough other signers' signatures to come back and be recorded, whereas the second proposal can be sent immediately after the first is locally/gathered-signed. No majority-signer collusion, no compromised keys, and no local host access are required, matching the constrained attacker model. The race window (time between local signing and the local record being marked globally accepted) is a normal occurrence in asynchronous signing rounds, making this practically repeatable per tenure, not merely theoretical.

### Recommendation
Change the duplicate-tenure-change guard to check whether this signer has already *signed* any block (tenure-change or not) for the given consensus_hash, not just whether one reached `GloballyAccepted`. Concretely, use a query equivalent to "has this signer produced a signature for any block in tenure CH" (e.g., an existing/added `has_signed_block_in_tenure`-style check keyed to the signer's own signing record, or track the first tenure-change block signed per consensus_hash independently of downstream global-acceptance status) before allowing a second tenure-change block for the same CH to be signed.

### Proof of Concept
Rust test plan (in `stacks-signer/src/chainstate/tests/v1.rs` or similar):
1. Construct a `SortitionsView` with `cur_sortition` set to consensus_hash CH.
2. Build tenure-change block B1 for CH; call `check_proposal` and confirm it returns `Ok(())`; persist B1's block info to `signer_db` in state `LocallyAccepted` (or `SignatureGathered`) — explicitly not `GloballyAccepted`.
3. Assert `signer_db.get_last_globally_accepted_block(&CH)` returns `None` (confirming the precondition).
4. Build tenure-change block B2 for the same CH with a different `signer_signature_hash`/parent commitment (conflicting with B1).
5. Call `check_proposal` for B2 and assert it incorrectly returns `Ok(())` instead of `Err(RejectReason::DuplicateBlockFound)`.
6. This demonstrates the equality violation: "signer already signed a block for CH" (true) vs. "guard's check" (false, because it only checks global acceptance) — i.e., the guard is bypassable while UNIQUENESS is violated.

### Citations

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
