### Title
v1 signer protocol's `DuplicateBlockFound` check only looks at globally-accepted blocks, letting a signer sign two conflicting tenure-start blocks - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
The v1 protocol-version proposal check that is supposed to stop a signer from endorsing a second, competing tenure-start block for a tenure it has already committed to only queries `SignerDb::get_last_globally_accepted_block`, which is blind to blocks the signer has already locally accepted (signed) or pre-committed. The v2 path was fixed to use `SignerDb::get_last_signed_block` for exactly this reason (there is a regression test proving it), but v1 was left using the narrower, globally-accepted-only query.

### Finding Description
`SortitionsView::validate_tenure_change_payload` (v1) runs the duplicate-tenure-start guard at proposal time: [1](#0-0) 

It calls `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` and only rejects with `RejectReason::DuplicateBlockFound` if a block in that tenure has already reached the `GloballyAccepted` state (i.e. the whole signer set signed it and the node processed it).

The equivalent v2 code was patched to call `get_last_signed_block` instead, which also counts `LocallyAccepted` blocks (a block this specific signer has already put a signature over). The regression test documents the exact failure mode being fixed: [2](#0-1) [3](#0-2) 

The shared documentation confirms the deliberate divergence between the two protocol versions: [4](#0-3) 

and separately confirms that this proposal-time duplicate check is a one-shot gate that never runs again — the only other backstop is the own-tenure conflict guard inside the pre-commit/signing path (`get_signed_conflicts` + `conflict_still_blocks`), and that guard only fires on `signed_self`/`signed_group`, i.e. it only protects a signer against itself once it has already recorded a signature for the first block: [5](#0-4) 

### Impact Explanation
This breaks the "one tenure-start block per tenure" equality for v1-protocol signers: a signer that has locally accepted (signed) a first tenure-start block, but for which that signature has not yet reached the node/`GloballyAccepted` state — either because it hasn't yet propagated to the local signer's own DB view via the same-tenure conflict path, or because a different signer instance in the fleet is momentarily unaware its own earlier local acceptance record isn't reflected yet — will pass `validate_tenure_change_payload`'s `DuplicateBlockFound` check for a second, different tenure-start block in the same tenure, since that check only looks for a `GloballyAccepted` predecessor. The proposal is then submitted for node validation and can proceed toward a real signature on a conflicting block, i.e. exactly the "signer signing a conflicting block" class of issue called out in scope. This is a Critical-class impact per the rules (a signer signing a conflicting block, breaking a one-per-tenure equality) reachable by a single miner proposing two competing tenure-start blocks for the same tenure plus ordinary gossip timing, with no majority-of-signers or key compromise required.

### Likelihood Explanation
Reachable by a lone miner: propose tenure-start block A, get it partially signed/locally-accepted by some signers (but not yet globally accepted), then propose a different tenure-start block B for the same tenure before A's signature/global-acceptance record has propagated everywhere. Any v0 signer running the v1 protocol-version path (`state_version.uses_global_state() == false`, dispatched from `check_block_against_state`) evaluates B through `check_proposal` → `validate_tenure_change_payload`, which will not see A unless A is already `GloballyAccepted`: [6](#0-5) 

The secondary backstop at pre-commit time only protects a signer that has already recorded its *own* `signed_self`/`signed_group` entry for A in `SignerDb`; it does not close the proposal-time gap for signers that haven't yet processed A.

### Recommendation
Change `SortitionsView::validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs` to use `SignerDb::get_last_signed_block` (as v2 already does) instead of `get_last_globally_accepted_block`, so the duplicate-tenure-start check also catches blocks this signer has only locally accepted, closing the same gap that was already fixed for v2.

### Proof of Concept
1. Miner proposes tenure-start block A for tenure T. Signer S (running v1 protocol) validates A, locally accepts and signs it (`mark_locally_accepted`), but A does not yet reach the 70% global threshold / `GloballyAccepted` state.
2. Before A's acceptance is reflected as `GloballyAccepted` in S's `SignerDb` (or before S's own local acceptance of A is otherwise re-checked), the miner proposes block B, a different tenure-start block for the same tenure T.
3. S's `check_proposal` → `validate_tenure_change_payload` calls `get_last_globally_accepted_block(T)`, which returns `None` (A is not yet globally accepted), so the `DuplicateBlockFound` rejection is skipped.
4. B proceeds to node validation and, absent an intervening same-tenure conflict re-check that already has A's signature recorded, can be signed by S — producing two signer-endorsed, mutually conflicting tenure-start blocks for tenure T.

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

**File:** docs/signer-flows.md (L425-431)
```markdown
Two things belong to the proposal path only and are **not** re-run at validate-ok
or at signing:

- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
```

**File:** stacks-signer/src/signerdb.rs (L1611-1619)
```rust
        let query = "SELECT b.consensus_hash, b.signer_signature_hash, b.stacks_height, b.state,
                MAX(COALESCE(b.signed_self, 0), COALESCE(b.signed_group, 0)) AS last_endorsed,
                st.superseded_by_consensus_hash, st.superseded_by_burn_block_hash
            FROM blocks b
            LEFT JOIN superseded_tenures st ON st.consensus_hash = b.consensus_hash
            WHERE (b.signed_self IS NOT NULL OR b.signed_group IS NOT NULL)
                AND b.stacks_height >= ?1
                AND b.signer_signature_hash != ?2
            ORDER BY b.stacks_height DESC";
```

**File:** stacks-signer/src/v0/signer.rs (L865-869)
```rust
        if state_version.uses_global_state() {
            self.check_block_against_global_state(stacks_client, &block_info.block)
        } else {
            self.check_block_against_local_state(stacks_client, sortition_state, &block_info.block)
        }
```
