### Title
v1 `validate_tenure_change_payload` treats locally-accepted blocks as absent, letting a duplicate tenure-start block be signed - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionState::validate_tenure_change_payload` in `chainstate/v1.rs` queries `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` to decide whether a competing tenure-start proposal is a duplicate, but a block the signer already `LocallyAccepted` (crossed the 70% signature threshold and was signed, but not yet observed as globally accepted by the node) never shows up there. The check therefore returns `Ok(())` for a second, conflicting tenure-start block B at the same height as an already-signed block A, regardless of any freshness timeout.

### Finding Description
The dedupe guard's intended invariant is "the tenure's last-signed block the dedupe check consults == the tenure's actual last-signed block." In v1 this is broken by construction: `get_last_globally_accepted_block` only returns blocks in `BlockState::GloballyAccepted`, while a block can sit in `BlockState::LocallyAccepted` indefinitely if the node hasn't yet processed/pushed the signed block [1](#0-0) .

Contrast this with v2's equivalent function, which was fixed to use `get_last_signed_block` (locally OR globally accepted) precisely to close this gap: "Only blocks we have signed (locally or globally accepted) count here" [2](#0-1) . The v2 regression test explicitly documents the pre-fix bug and asserts `DuplicateBlockFound` for a merely `LocallyAccepted` block: "Before the fix, this would have incorrectly passed because get_last_globally_accepted_block would not find the locally-accepted block" [3](#0-2) . That fix and its regression test exist only for v2 (`chainstate/tests/v2.rs`); no equivalent `DuplicateBlockFound`/`mark_locally_accepted` test exists in `stacks-signer/src/chainstate/tests/v1.rs`, and `chainstate/v1.rs` still calls `get_last_globally_accepted_block` unchanged.

Note this differs from the framing in the question: the gap in v1 is not gated by the `tenure_last_block_proposal_timeout` freshness window at all — v1's duplicate check never calls `SortitionData::get_tenure_last_block_info` (the function that applies `signed_self.max(signed_group)` freshness). It calls `get_last_globally_accepted_block` directly and unconditionally [4](#0-3) . So no waiting/timing is required by the attacker at all; the hole is permanent for any locally-accepted-but-not-yet-globally-accepted block, at any time. (The `get_tenure_last_block_info`/timeout mechanism is used elsewhere for `check_latest_block_in_tenure`/`check_tenure_change_confirms_parent`, a separate check that still runs and is not itself broken.)

Exploit flow: attacker wins a sortition slot honestly (one BTC block). Miner proposes tenure-start block A; signer set (including the honest majority) signs it, crossing 70%, so each signer marks it `LocallyAccepted` via `mark_locally_accepted`. Before the node observes/broadcasts A as globally accepted (e.g., a brief window, or if broadcast is delayed/dropped), the attacker crafts a second tenure-change `BlockProposal` B with identical `prev_tenure_consensus_hash`/`tenure_consensus_hash`, differing transactions, at the same chain height. Any v1 signer running `validate_tenure_change_payload` on B finds no globally-accepted block for that tenure and does not reject with `DuplicateBlockFound`; B proceeds to the tenure-change confirms-parent/reorg checks and can be signed, producing two competing signed blocks at the same height in the same tenure.

### Impact Explanation
This breaks the block-uniqueness/no-fork-at-height safety property for a tenure's first block: a signer can be induced to sign two conflicting blocks (A and B) at the same tenure height, one of which is not canonical. This is a Critical-class chain-safety issue ("a signer signing an invalid, non-canonical, or conflicting block").

### Likelihood Explanation
Preconditions are only: the tenure in question must use the v1 signer-chainstate/protocol path, A must have reached the 70% "locally accepted" threshold but not yet be observed globally accepted by the node (a normal, non-adversarial timing window that occurs on every tenure-start block, however briefly), and the attacker needs just their own single miner slot plus the ability to gossip a `BlockProposal` B — no majority of signers, no privileged role, no auth_token. This is fully repeatable on every tenure-start for as long as v1 chainstate is active.

### Recommendation
Change `stacks-signer/src/chainstate/v1.rs::validate_tenure_change_payload` to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` (as v2 does) instead of `get_last_globally_accepted_block`, so any locally- or globally-accepted (i.e., actually signed) block in the tenure is treated as a duplicate. Add a regression test in `stacks-signer/src/chainstate/tests/v1.rs` mirroring `check_tenure_change_rejects_when_locally_accepted_block_exists` from `tests/v2.rs`.

### Proof of Concept
```rust
// stacks-signer/src/chainstate/tests/v1.rs
#[test]
fn v1_check_tenure_change_rejects_when_locally_accepted_block_exists() {
    // Setup: same pattern as v2's check_tenure_change_rejects_when_locally_accepted_block_exists,
    // but drive stacks-signer/src/chainstate/v1.rs::SortitionState::validate_tenure_change_payload
    // (via check_proposal) instead of v2's.

    // 1. Build a tenure-start BlockProposal `existing_block_proposal` (A) for cur_sortition.
    // 2. Convert to BlockInfo, call `existing_block_info.mark_locally_accepted(false)` (NOT globally accepted).
    // 3. signer_db.insert_block(&existing_block_info).unwrap();

    // 4. Build a second tenure-change BlockProposal `block` (B) for the same tenure
    //    (same consensus_hash / prev_tenure_consensus_hash), signed by the miner.

    // 5. let result = sortitions_view.check_proposal(&stacks_client, &mut signer_db, &block);

    // EQUALITY CHECK (fails today):
    assert!(
        matches!(result, Err(RejectReason::DuplicateBlockFound)),
        "v1 validate_tenure_change_payload should reject B as duplicate of locally-accepted A, got: {result:?}"
    );
    // Today this assertion fails because v1's validate_tenure_change_payload calls
    // signer_db.get_last_globally_accepted_block (not get_last_signed_block), so it returns
    // Ok(()) and B passes, allowing the signer to sign a second conflicting tenure-start block.
}
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

**File:** stacks-signer/src/chainstate/tests/v2.rs (L843-849)
```rust
    // The proposal should be rejected because there's already a locally-accepted
    // block in this tenure. Before the fix, this would have incorrectly passed
    // because get_last_globally_accepted_block would not find the locally-accepted block.
    assert!(
        matches!(result, Err(RejectReason::DuplicateBlockFound)),
        "Expected DuplicateBlockFound rejection when a locally-accepted block exists in the tenure, got: {result:?}"
    );
```
