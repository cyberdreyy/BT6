### Title
V1 signer's tenure-change duplicate check only looks at globally-accepted blocks, letting a signer put a second signature on a conflicting tenure-start block it locally accepted - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionsView::validate_tenure_change_payload` in the v1 (pre-global-state) chainstate path rejects a tenure-start block as `DuplicateBlockFound` only if the signer already has a **globally accepted** block in that tenure. It never checks locally-accepted-but-not-yet-globally-accepted blocks. The v2/global-state path was already patched to close exactly this gap (`get_last_signed_block`, which covers both `LocallyAccepted` and `GloballyAccepted`), with a regression test proving the old `get_last_globally_accepted_block` behavior was a bug. The v1 path still uses the pre-fix, narrower query.

### Finding Description
In `stacks-signer/src/chainstate/v1.rs`, `validate_tenure_change_payload` performs the tenure-start duplicate check as: [1](#0-0) 
using `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)`.

Compare this to the v2 implementation of the identical check: [2](#0-1) 
which explicitly uses `get_last_signed_block` (covering `LocallyAccepted` OR `GloballyAccepted`) and documents why: "Only blocks we have signed (locally or globally accepted) count here."

A dedicated regression test confirms this exact class of bug was fixed for v2 and was previously exploitable: [3](#0-2) 
"Before the fix, this would have incorrectly passed because `get_last_globally_accepted_block` would not find the locally-accepted block." No equivalent fix or regression test exists for v1's `validate_tenure_change_payload`, which still calls `get_last_globally_accepted_block`: [4](#0-3) 

`BlockState::LocallyAccepted` means the individual signer has already put its own signature over a block (`mark_locally_accepted`), before the group threshold (70%) is reached: [5](#0-4) 
So on v1-protocol signers, if a miner proposes a first tenure-start block that the signer signs (`LocallyAccepted`) but which never reaches the 70% group threshold (e.g., stalls, network partition, or the miner deliberately abandons it), the miner can re-propose a *second, conflicting* tenure-start block for the same tenure. `validate_tenure_change_payload` at proposal time will not flag it as a duplicate, because it only looks for `GloballyAccepted` blocks and finds none.

The docs note a partial mitigation exists further down the pipeline — the pre-commit-threshold conflict check (`get_signed_conflicts`, section 5 in `docs/signer-flows.md`) is supposed to catch same-tenure conflicts before the final signature is issued: [6](#0-5) 
However, that later re-check only fires once the *second* proposal has already crossed the pre-commit threshold; the earlier proposal-time gate in v1's `check_proposal`/`validate_tenure_change_payload` is the one specifically designed to catch tenure-start duplicates immediately, and it silently passes them through on v1 signers because it only checks `get_last_globally_accepted_block`. This creates an inconsistency between what `check_proposal` claims to guarantee (no second tenure-start signature in the same tenure) and what the v1 code path actually checks (equality break: "checked" vs. "actually verified").

### Impact Explanation
This falls under the "signer signing an invalid/non-canonical/conflicting block" class. A v1-protocol signer can be led into locally accepting (signing) two conflicting tenure-start blocks for the same tenure — one it never should have signed had the duplicate check used the same signed-block definition as v2. If enough signers on the v1 protocol path are induced into the same situation (e.g., during a slow/failed pre-commit round for the first proposal), the second, conflicting tenure-start block can gather independent pre-commit/signature weight for a different block content at the same tenure position, undermining the "one-per-tenure-start" invariant that the check is meant to enforce, and creating conflicting equally-signed candidates for the same slot.

### Likelihood Explanation
Likelihood is moderate: it requires only a one-slot miner (no majority of signers, no other signer's key) to propose a tenure-start block, let it fail to reach the pre-commit/signature threshold (a naturally occurring event during timeouts/network hiccups, or which a malicious miner can engineer by delaying broadcast to enough signers), and then re-propose a different tenure-start block for the same tenure. It only manifests on the v1 (pre-global-state) protocol path, which is documented as still active/supported (`SortitionStateVersion::V1` and `determine_active_signer_protocol_version`).

### Recommendation
Update `stacks-signer/src/chainstate/v1.rs`'s `validate_tenure_change_payload` to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` instead of `get_last_globally_accepted_block`, matching the v2 fix and its rationale (locally-accepted blocks already carry this signer's signature and must count as a duplicate/conflict for tenure-start purposes). Add a regression test for v1 mirroring `check_tenure_change_rejects_when_locally_accepted_block_exists` in `stacks-signer/src/chainstate/tests/v2.rs`.

### Proof of Concept
1. Signer running v1 (pre-global-state) protocol validates and locally accepts (signs) tenure-start block `B1` for tenure `T` (`mark_locally_accepted`), but `B1` never reaches the 70% pre-commit/signature threshold (stalls/times out).
2. Miner proposes a second, different tenure-start block `B2` for the same tenure `T` (different transactions/coinbase).
3. `SortitionsView::check_proposal` → `validate_tenure_change_payload` (`stacks-signer/src/chainstate/v1.rs:461-519`) calls `get_last_globally_accepted_block(T)`, which returns `None` because `B1` is only `LocallyAccepted`, not `GloballyAccepted`.
4. No `DuplicateBlockFound` rejection is issued; `B2` proceeds to node validation and, if valid, to pre-commit/signing — the signer may end up signing two distinct tenure-start blocks (`B1` and `B2`) for the same tenure, a case the equivalent v2 check (and its regression test at `stacks-signer/src/chainstate/tests/v2.rs:755-850`) was specifically fixed to prevent.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L497-518)
```rust
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

**File:** stacks-signer/src/chainstate/tests/v2.rs (L843-850)
```rust
    // The proposal should be rejected because there's already a locally-accepted
    // block in this tenure. Before the fix, this would have incorrectly passed
    // because get_last_globally_accepted_block would not find the locally-accepted block.
    assert!(
        matches!(result, Err(RejectReason::DuplicateBlockFound)),
        "Expected DuplicateBlockFound rejection when a locally-accepted block exists in the tenure, got: {result:?}"
    );
}
```

**File:** stacks-signer/src/signerdb.rs (L112-127)
```rust
define_u8_enum!(
/// Block state relative to the signer's view of the stacks blockchain
BlockState {
    /// The block has not yet been processed by the signer
    Unprocessed = 0,
    /// The block is accepted by the signer but a threshold of signers has not yet signed it
    LocallyAccepted = 1,
    /// The block is rejected by the signer but a threshold of signers has not accepted/rejected it yet
    LocallyRejected = 2,
    /// A threshold number of signers have signed the block
    GloballyAccepted = 3,
    /// A threshold number of signers have rejected the block
    GloballyRejected = 4,
    /// The block is pre-committed by the signer, but not yet signed
    PreCommitted = 5
});
```

**File:** stacks-signer/src/v0/signer.rs (L1340-1345)
```rust
        // The chain and signer db state may have changed materially since this block passed the
        // proposal-time checks (e.g. between validation and reaching the pre-commit threshold we
        // may have signed a block that this one would reorg). Re-run the chainstate checks
        // before putting a signature over the block, and respond with a rejection if they no
        // longer pass, just as the block validation response handler does.
        if let Some(block_rejection) =
```
