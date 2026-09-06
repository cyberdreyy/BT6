### Title
v1 tenure-change duplicate-block check uses `get_last_globally_accepted_block` instead of `get_last_signed_block`, allowing a signer to validate/pre-commit toward a conflicting tenure-start block - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
The v1 `SortitionsView::validate_tenure_change_payload` duplicate-tenure-start check queries only globally-accepted blocks, not locally-accepted/pre-committed-but-unconfirmed ones. The equivalent v2 check was fixed for exactly this reason (see the regression test in `stacks-signer/src/chainstate/tests/v2.rs`), but v1 was left using the old, narrower predicate.

### Finding Description
`validate_tenure_change_payload` in v1 rejects a proposed tenure-change block with `DuplicateBlockFound` only if the signer already has a **globally accepted** block in that tenure: [1](#0-0) 

The v2 counterpart was patched to use `get_last_signed_block`, which also covers `LocallyAccepted`/pre-committed-but-not-yet-globally-confirmed blocks: [2](#0-1) 

The v2 regression test explicitly documents the class of bug this fixes: "previously, the check used `get_last_globally_accepted_block`, which would miss blocks in `LocallyAccepted` or `PreCommitted` state and incorrectly allow a duplicate tenure change": [3](#0-2) 

`docs/signer-flows.md` corroborates that this asymmetry between v1 and v2 is intentional/known but frames it purely as a semantic difference, not flagging that v1 is still exposed: [4](#0-3) 

A one-slot miner that has proposed a first tenure-start block which a v1-protocol signer locally accepted (crossed the pre-commit/signature threshold from that signer's perspective) but which has not yet reached the node as a `NewBlock` event (i.e., not globally accepted) can propose a **second, competing** tenure-start block for the same tenure. Under v1, `validate_tenure_change_payload`'s duplicate check will not see the first block (since `get_last_globally_accepted_block` returns `None`), so `check_proposal` at proposal time incorrectly treats the second, conflicting tenure-start block as valid and the signer proceeds to validate it and issue a pre-commit for it — a v1 signer producing a pre-commit / partial endorsement over a second, conflicting tenure-start block in the same tenure, which is exactly the one-per-tenure invariant this check exists to protect (`DuplicateBlockFound`).

### Impact Explanation
This breaks the "one signed/committed block per tenure-start" equality on the v1 code path at the point where the signer decides whether to even validate/pre-commit a proposal. Whether this ultimately produces an actual double *signature* depends on the later, independent pre-commit-time re-check (`get_signed_conflicts`, which is derived from `signed_self`/`signed_group` and does catch locally-signed conflicts) acting as a backstop before the final signature is emitted — per `docs/signer-flows.md`'s own description of the "own-tenure conflict guard." I was not able to fully verify within the available tool budget whether every v1 code path that reaches `mark_pre_committed`/signing re-invokes this backstop identically to v2, or whether there is a window (e.g., the pre-commit response itself, or v1-specific miner-invalidation side effects triggered by `ReorgNotAllowed` rejections gated on `uses_global_state()`) where the weakened check at proposal time has consensus-visible effect before the backstop fires. Given the uncertainty about whether a full double-sign is reachable, this is best treated as a High-severity finding (a signer improperly validating/pre-committing to a non-canonical/conflicting tenure-start block, and a documented parity gap between v1 and v2 for a bug class already fixed in v2) rather than a confirmed Critical double-sign, pending code-level confirmation of the pre-commit-time backstop's coverage on the v1 path.

### Likelihood Explanation
This only requires a single miner with one winning sortition slot to propose two competing tenure-start blocks for the same tenure while v1-protocol signers are active, and does not require a majority of signers, another signer's key, or any auth token/local access — consistent with the allowed threat model. It is directly reachable via the normal `check_proposal` entry point.

### Recommendation
Change `validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs` to use `SignerDb::get_last_signed_block` (matching the v2 fix) instead of `get_last_globally_accepted_block`, so that locally-accepted and pre-committed-but-unconfirmed blocks in the current tenure are also treated as duplicates that reject a second tenure-start proposal. Add a v1 regression test mirroring `check_tenure_change_rejects_when_locally_accepted_block_exists` in `stacks-signer/src/chainstate/tests/v2.rs` to lock in the fix and confirm parity between the two chainstate versions.

### Proof of Concept
1. Boot a signer running the v1 chainstate/protocol path (`stacks-signer/src/chainstate/v1.rs`).
2. Miner M wins a sortition and proposes tenure-start block A (with `TenureChangeCause::BlockFound`) for tenure T.
3. Signers validate and locally accept/pre-commit A, such that `BlockInfo::state` is `LocallyAccepted` or `PreCommitted` in `SignerDb`, but the node has not yet processed A as its canonical tip (no `NewBlock` event), so `get_last_globally_accepted_block(T)` still returns `None`.
4. Miner M (or a colluding relay) proposes a second, conflicting tenure-start block B for the same tenure T (e.g., different transactions/coinbase), still referencing the same parent tenure.
5. Call `SortitionsView::check_proposal` (v1) on B: `validate_tenure_change_payload` calls `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)`, which returns `None` (A is only locally accepted), so the `DuplicateBlockFound` branch is skipped and B passes the tenure-change duplicate check — reproducing the exact scenario the v2 regression test (`check_tenure_change_rejects_when_locally_accepted_block_exists`, `stacks-signer/src/chainstate/tests/v2.rs:756-850`) was written to prevent, but on the unpatched v1 code path.

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

**File:** stacks-signer/src/chainstate/tests/v2.rs (L748-756)
```rust
/// Test that a tenure change proposal is rejected when a locally-accepted
/// (but not globally-accepted) block already exists in the same tenure.
///
/// This is a regression test: previously, the check used
/// `get_last_globally_accepted_block`, which would miss blocks in
/// `LocallyAccepted` or `PreCommitted` state and incorrectly allow
/// a duplicate tenure change.
#[test]
fn check_tenure_change_rejects_when_locally_accepted_block_exists() {
```

**File:** docs/signer-flows.md (L428-431)
```markdown
- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
```
