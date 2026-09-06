### Title
V1 protocol's `DuplicateBlockFound` gate only checks `get_last_globally_accepted_block`, missing locally-accepted-only tenure-start blocks that v2's `get_last_signed_block` would catch - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
`SortitionsView::validate_tenure_change_payload` in the v1 chainstate path (`stacks-signer/src/chainstate/v1.rs:505-518`) uses `signer_db.get_last_globally_accepted_block(...)` to detect whether the signer has already signed a block in the current tenure before accepting a competing tenure-start block. The v2 path (`stacks-signer/src/chainstate/v2.rs:344-357`) uses the strictly broader `get_last_signed_block`, which also covers locally-accepted (signed but not yet globally-accepted) blocks. This asymmetry means a v1-pinned signer's duplicate-detection set is a strict subset of "blocks this signer has already signed," letting it approve a second, conflicting tenure-start block for a tenure where it already signed a first one that never reached global acceptance.

### Finding Description
The v1 comment at `stacks-signer/src/chainstate/v1.rs:511-512` states the check exists to reject a tenure-start block "if we've already signed a block in this tenure," but the implementation only queries `get_last_globally_accepted_block`, not all locally-accepted (signed) blocks. The v2 implementation explicitly widened this: its comment at `stacks-signer/src/chainstate/v2.rs:340-343` states "Only blocks we have signed (locally or globally accepted) count here" and calls `get_last_signed_block`. This is a directly observable behavioral divergence between the two protocol versions on the same logical check.

Exploit flow for a v1-pinned victim signer:
1. Miner proposes tenure-start block A for tenure T. The victim signer runs it through `check_proposal` -> `validate_tenure_change_payload`, passes all checks, and signs it (recorded as at least `LocallyAccepted`/signed in `signerdb`), but the node never broadcasts/records it as globally accepted (e.g., a node restart between the signer's local acceptance and global acceptance broadcast, or insufficient other signers responding before a view reset).
2. The attacker (or a miner colluding is not required — the attacker only needs to win a subsequent slot and craft a block, or reuses influence over what the victim node relays) submits/gossips a second tenure-start block B for the same tenure T (or the same miner re-proposes after losing track of A).
3. On the victim's v1-pinned check, `get_last_globally_accepted_block(&block.header.consensus_hash)` returns `None` because A was only locally accepted, so the `DuplicateBlockFound` gate at `stacks-signer/src/chainstate/v1.rs:510-518` is skipped and B is treated as an eligible tenure-start proposal.
4. A signer on the v2 path checking the identical scenario would find `get_last_signed_block` returning `Some(A)` and correctly reject B with `RejectReason::DuplicateBlockFound`.
5. This produces a split verdict: the v1-pinned signer proceeds to consider/sign B for tenure T while other (v2) signers reject it, i.e., the signer pre-commits/signs a second, conflicting tenure-start block for a tenure it already signed.

This does not require majority-signer collusion, a compromised auth_token, or local host access — only that (a) the victim signer is pinned to protocol v1, and (b) a first tenure-start block reached local (signed) but not global acceptance before a second competing tenure-start proposal arrives, which is a plausible operational sequence (node restart / crash / gossip delay) rather than an attacker-controlled guarantee, but the attacker's role is limited to crafting/gossiping block proposal B, consistent with the unprivileged threat model.

### Impact Explanation
This breaks the UNIQUENESS/equivocation-guard safety property: a signer must never sign two conflicting blocks (especially two competing tenure-start blocks) for the same tenure. If exploited, the v1-pinned signer contributes its signature toward a second, non-canonical tenure-start block, potentially helping that competing block reach the signing threshold if enough other signers are similarly affected or if this combines with other equivocation gaps — a Critical-severity issue per the given severity taxonomy (signer signing a conflicting block, violating uniqueness). It is repeatable for any tenure where the first block's global-acceptance record is missing at the time of the second proposal.

### Likelihood Explanation
Preconditions: `TEST_PIN_SUPPORTED_SIGNER_PROTOCOL_VERSION` (or otherwise) pins the victim signer to v1; block A must be locally-accepted/signed but never recorded as globally accepted in the victim's `signerdb` (e.g., due to a node/signer restart racing the global-acceptance broadcast, a scenario acknowledged elsewhere in the codebase, such as v1's `is_timed_out` relying on `has_signed_block_in_tenure` rather than global-acceptance alone). Given this precondition, the attacker's cost is exactly one miner slot plus the ability to gossip a competing `BlockProposal` — no elevated privileges are needed. The exploit is deterministic given the precondition and is reproducible via a signer-only unit test.

### Recommendation
Change the v1 `validate_tenure_change_payload` duplicate check in `stacks-signer/src/chainstate/v1.rs` to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` (matching v2's semantics) instead of `get_last_globally_accepted_block`, so any locally-accepted-only prior signature in the tenure also triggers `RejectReason::DuplicateBlockFound`.

### Proof of Concept
Rust test in `stacks-signer/src/chainstate/tests/v1.rs`, mirroring existing tests such as `check_tenure_change_accepts_when_only_pre_committed_block_exists`:

```rust
#[test]
fn check_tenure_change_rejects_when_locally_accepted_block_exists() {
    // Setup: same sortition/tenure fixtures as
    // check_tenure_change_accepts_when_only_pre_committed_block_exists

    // Insert block A into signer_db for the tenure's consensus_hash,
    // but mark it BlockState::LocallyAccepted (i.e., signed) rather than
    // BlockState::PreCommitted, and do NOT mark it globally accepted.
    signer_db
        .insert_block(&block_info_a_locally_accepted)
        .unwrap();

    // Sanity: v1's own check queries the narrower set.
    assert!(signer_db
        .get_last_globally_accepted_block(&consensus_hash)
        .unwrap()
        .is_none());
    // But the block was in fact signed by this signer.
    assert!(signer_db
        .get_last_signed_block(&consensus_hash)
        .unwrap()
        .is_some());

    // Act: propose competing tenure-start block B for the same tenure.
    let result = view.check_proposal(&client, &mut signer_db, &block_b, false, None);

    // Expected (fixed) behavior: reject as duplicate.
    assert_eq!(result, Err(RejectReason::DuplicateBlockFound));
    // Current (vulnerable) v1 behavior: result is Ok(()), demonstrating
    // that v1's DuplicateBlockFound gate misses the already-signed block A.
}
``` [1](#0-0) [2](#0-1)

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
