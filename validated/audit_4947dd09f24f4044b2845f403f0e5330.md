Confirmed: the divergence is real and unfixed in v1, while v2 was patched (per the test comment "Before the fix, this would have incorrectly passed" at `stacks-signer/src/chainstate/tests/v2.rs:844-849`).

### Title
v1 protocol duplicate-tenure-start check uses `get_last_globally_accepted_block` instead of `get_last_signed_block`, allowing a signer to sign two conflicting tenure-start blocks - (File: stacks-signer/src/chainstate/v1.rs)

### Summary
`SortitionsView::validate_tenure_change_payload` in the v1 chainstate module is a near-duplicate reimplementation of the equivalent v2 logic (`GlobalStateView::validate_tenure_change_payload`), mirroring the ERC223BeamToken pattern of unnecessarily re-deriving functionality that already exists (and was already fixed) elsewhere. The v2 duplicate-block guard was patched to use `get_last_signed_block` (catching locally-accepted-but-not-yet-globally-accepted blocks), but the parallel v1 code path still uses the stale `get_last_globally_accepted_block` check, reintroducing the exact bug the v2 fix addressed.

### Finding Description
In v2, `validate_tenure_change_payload` explicitly guards against signing two competing tenure-start blocks in the same tenure by checking `signer_db.get_last_signed_block(&block.header.consensus_hash)` — i.e., any block this signer has already locally OR globally accepted (signed) in that tenure: [1](#0-0) 

The v1 equivalent (`SortitionsView::validate_tenure_change_payload`) performs the identical check but against `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)`: [2](#0-1) 

A block only becomes "globally accepted" once the entire signer set has produced 70% weighted signatures and the node has processed it; a block that this signer has merely *locally accepted* (signed itself, `signed_self` set, but not yet `signed_group`) is invisible to `get_last_globally_accepted_block`. The regression test added for v2 (`check_tenure_change_rejects_when_locally_accepted_block_exists`) documents exactly this: "Before the fix, this would have incorrectly passed because get_last_globally_accepted_block would not find the locally-accepted block": [3](#0-2) 

Because v1 still uses `get_last_globally_accepted_block`, a v1 signer that has already signed (locally accepted) tenure-start block A for tenure T, but whose signature has not yet been aggregated to 70%/broadcast/processed by the node, will pass `check_proposal`'s `DuplicateBlockFound` guard for a *second*, different tenure-start block B proposed for the same tenure T. This breaks the "one signature per height/tenure" invariant the whole pre-commit/conflict-guard architecture (docs/signer-flows.md section 5, `get_signed_conflicts`) is built to protect, at the earliest and cheapest point of the pipeline (`check_proposal`, called at proposal arrival before any node validation round-trip).

The downstream "own-tenure conflict" backstop (`handle_block_pre_commit` → `get_signed_conflicts`) is a height-based, cross-tenure query, and it is the *only* other place this class of duplicate is caught after proposal time — the docs explicitly state: "the `DuplicateBlockFound` check ... lives in `check_proposal` and runs only at proposal arrival, never again... Because the duplicate check never runs again, a block that crosses the pre-commit threshold long after it was proposed relies on section 5's own-tenure conflict guard to cover the same ground": [4](#0-3) 

That backstop, however, only blocks while the earlier signature is still "fresh" (within `tenure_last_block_proposal_timeout`) or while the node can still be shown to have built on the earlier block. If A's local signature has gone stale (no group signature ever materialized, e.g. because <70% of signers picked A), the conflict guard stops vetoing new signatures for other blocks at that height, and nothing else in v1 prevents the signer from then signing B for the same tenure — reproducing precisely the double-tenure-start-block signing hazard v2's fix was created to close.

### Impact Explanation
This is a Critical-class issue per the rubric: a v1-protocol signer can end up placing valid signatures over two different, conflicting tenure-start blocks for the same tenure (a `signed vs validated` / `one-per-height` equality break). If enough other v1 signers independently accumulate signatures on divergent tenure-start proposals under the same relaxed check, this can produce two blocks each capable of reaching the aggregate signature threshold — a live equivocation at the tenure-start level, not merely a redundant validation.

### Likelihood Explanation
This requires only a single miner (one-slot) re-proposing a different tenure-start block for the same tenure after the first attempt failed to reach the 70% threshold and the earlier local signature aged past `tenure_last_block_proposal_timeout` — no majority signer collusion or key compromise is needed. Ordinary miner behavior (re-attempting a tenure start after a stalled first attempt) is exactly the trigger scenario documented for the same class of bug in v2 before its fix.

### Recommendation
Change `stacks-signer/src/chainstate/v1.rs::validate_tenure_change_payload` to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` in place of `get_last_globally_accepted_block`, matching the v2 fix, and add a v1-specific regression test mirroring `check_tenure_change_rejects_when_locally_accepted_block_exists` in `stacks-signer/src/chainstate/tests/v2.rs`. More broadly, the duplicated logic between `chainstate/v1.rs` and `chainstate/v2.rs` (which is the root cause enabling this kind of fix-in-one-place-miss-in-another) should be consolidated into the shared `chainstate/mod.rs` helper layer so a single fix always applies to both protocol versions.

### Proof of Concept
1. Signer runs protocol v1. Miner proposes tenure-start block A for tenure T.
2. Signer validates A, pre-commits, and (with ≥70% pre-commit weight reached and no fresh conflicts) signs A: `mark_locally_accepted` sets `signed_self`, but the group never reaches 70% acceptance (e.g., a competing miner or network partition prevents other signers from pre-committing to A in time).
3. `tenure_last_block_proposal_timeout` elapses; A's local signature is now stale, so `get_signed_conflicts`/`conflict_still_blocks` no longer blocks new signatures at that height.
4. Miner proposes a different tenure-start block B for the same tenure T (same `consensus_hash`).
5. `SortitionsView::check_proposal` → `validate_tenure_change_payload` calls `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)`, which returns `None` because A was only locally accepted, never globally accepted — the `DuplicateBlockFound` rejection at [2](#0-1)  never fires.
6. B proceeds through node validation and pre-commit; the signer signs B, now holding signatures over two conflicting tenure-start blocks (A and B) for tenure T.

### Citations

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

**File:** stacks-signer/src/chainstate/tests/v2.rs (L838-849)
```rust
    let result = sortitions_view.check_proposal(&stacks_client, &mut signer_db, &block);

    exit_flag.store(true, Ordering::SeqCst);
    serve.join().unwrap();

    // The proposal should be rejected because there's already a locally-accepted
    // block in this tenure. Before the fix, this would have incorrectly passed
    // because get_last_globally_accepted_block would not find the locally-accepted block.
    assert!(
        matches!(result, Err(RejectReason::DuplicateBlockFound)),
        "Expected DuplicateBlockFound rejection when a locally-accepted block exists in the tenure, got: {result:?}"
    );
```

**File:** docs/signer-flows.md (L425-437)
```markdown
Two things belong to the proposal path only and are **not** re-run at validate-ok
or at signing:

- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
- the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the
  pox bitvec, and tenure-extend rules before delegating here.

Because the duplicate check never runs again, a block that crosses the pre-commit
threshold long after it was proposed relies on section 5's own-tenure conflict
guard to cover the same ground.
```
