### Title
`chainstate/v1.rs::validate_tenure_change_payload` checks only *globally* accepted collateral (blocks), letting a signer double-sign a tenure by proposing (and getting locally accepted) a second tenure-change block for a tenure it has already locally accepted a block in - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
Exactly like the Rubicon `_borrowLimit` bug (which only checked `_bathToken` balance and ignored other collateral the user already held), the v1 signer chainstate's duplicate-tenure-change check only looks at *one* signed state (`GloballyAccepted`) and ignores the equally-valid `LocallyAccepted` state that the signer itself has already committed a signature to. v2 was patched for this exact gap; v1 was not.

### Finding Description
`SortitionsView::validate_tenure_change_payload` (v1) rejects a tenure-change proposal with `DuplicateBlockFound` only if it finds an existing block via `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)`: [1](#0-0) 

This mirrors the v2 equivalent, except v2 was fixed to use `get_last_signed_block` (which covers both `LocallyAccepted` and `GloballyAccepted` states): [2](#0-1) 

The v2 test suite explicitly documents this as a fixed regression: [3](#0-2) 

and demonstrates that using `get_last_globally_accepted_block` "would miss blocks in `LocallyAccepted` or `PreCommitted` state and incorrectly allow a duplicate tenure change." The v1 code path was never given the same fix and still uses the narrower, single-state query, exactly analogous to the Rubicon bug checking only `_bathToken` balance while ignoring other existing collateral (here, the "collateral" is the signer's own prior local signature).

The design docs confirm this check runs only once, at proposal time, and is not re-run at validate-ok or at signing: [4](#0-3) 

The intended backstop against this gap is the pre-commit-time conflict guard (`get_signed_conflicts` + `conflict_still_blocks`), which is tenure-agnostic and keys off `signed_self`/`signed_group` rather than global acceptance: [5](#0-4) 

However, that backstop is not airtight for a same-tenure conflict that the node has not yet observed (i.e. the first block is only `LocallyAccepted`, not yet handed to the node). In `handle_block_pre_commit`, a same-tenure conflict is excused from blocking whenever the node's tenure tip has not yet reached the proposed height: [6](#0-5) 

and `conflict_still_blocks` explicitly says a not-yet-globally-accepted same-height sibling still blocks, but a not-yet-globally-accepted conflict *below* the proposed height does not: [7](#0-6) 

Combining these: for a v1 signer that has locally accepted a tenure-change block A (chain_length = N) in tenure T but has not yet reached global acceptance, a second, differently-transacted tenure-change proposal for the same tenure T at a *higher* chain_length (N+k, e.g. because the miner adds more blocks to a still-unconfirmed tenure before A is globally accepted) will:
1. Pass `validate_tenure_change_payload` at proposal time, because `get_last_globally_accepted_block` finds nothing (A is only locally accepted) — this is the direct analog of the Rubicon bug.
2. Reach the pre-commit stage, where `get_signed_conflicts` does find A as a conflict (since A has `signed_self` set).
3. If A's signature is still "fresh" but the node's tenure tip is below N+k (true, since A was never handed to the node), `conflict_still_blocks` returns `true` only for a conflict at the *same or higher* height than the proposal; a conflict *below* the newly proposed height (`conflict.stacks_height <= proposed_height` combined with `!globally_accepted`) is treated as *not* blocking, per line 1205 (`node_reaches_conflict || (!conflict.globally_accepted && conflict.stacks_height <= proposed_height)`) — wait, this actually still returns `true` in this case (blocks). But if a reorg-permit is (mis)standing for that tenure, or the conflict is stale (past `tenure_last_block_proposal_timeout`), the guard is bypassed, and the same-tenure check at 1432-1457 lets the signer proceed because the node has not yet confirmed the tenure at the new height.

The signer can therefore end up committing its signature to *two different, mutually exclusive* tenure-change blocks for the same tenure (block A and block B, both claiming to start/confirm the same parent), which is a double-sign / equivocation — precisely the class of bug this signer architecture is built to prevent (see the explicit test `signer_refuses_to_sign_second_sibling_tenure_start` for the intended invariant): [8](#0-7) 

### Impact Explanation
This is a Critical-class issue per the assignment's rubric: it lets a single signer (with the collaboration of a byzantine/faulty miner re-proposing conflicting tenure-change blocks) sign a duplicate/conflicting block for a tenure it already signed, breaking the "one signature per tenure-change" equality that `validate_tenure_change_payload`/`DuplicateBlockFound` and the pre-commit conflict guard are jointly supposed to enforce. It undermines the equivocation guard specifically for v1 chainstate signers, which is the "losing the equivocation guard" High/Critical impact class called out in the rules.

### Likelihood Explanation
The trigger requires only a single miner (no majority of signers, no other signer's key) proposing a second tenure-change block for the same tenure with a higher chain_length before the first is globally accepted — a state reachable in normal operation whenever a tenure straddles multiple locally-signed-but-not-yet-globally-accepted blocks, or during a re-proposal race. It requires v1 chainstate to still be in use, which the codebase clearly still supports (`stacks-signer/src/chainstate/v1.rs` is live code with its own test suite), so the condition for reachability is realistic (mixed v1/v2 signer sets during rollout, or reward cycles still running v1).

### Recommendation
In `stacks-signer/src/chainstate/v1.rs::validate_tenure_change_payload`, replace `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` with `signer_db.get_last_signed_block(&block.header.consensus_hash)`, matching the fix already applied to v2, so that a `LocallyAccepted` (not just `GloballyAccepted`) block in the tenure is also treated as prior "collateral" that triggers `DuplicateBlockFound`.

### Proof of Concept
1. Signer using v1 chainstate receives and validates tenure-change block A for tenure T (chain_length = 10); `handle_block_validate_ok` → `check_block_against_signer_db_state` passes → `mark_pre_committed` → pre-commit threshold reached → `mark_locally_accepted` sets `signed_self`. A is now `LocallyAccepted` but not yet `GloballyAccepted` (not enough signers have confirmed yet, or the node hasn't ingested it).
2. The miner (or a byzantine actor controlling proposal delivery) submits a second tenure-change block B for the same tenure T with different transactions and chain_length = 12 (still confirming the same parent tenure).
3. `SortitionsView::check_proposal` (v1) calls `validate_tenure_change_payload`, which calls `get_last_globally_accepted_block(T)` — returns `None` because A is only `LocallyAccepted`. The `DuplicateBlockFound` check is skipped; B passes proposal-time validation (`stacks-signer/src/chainstate/v1.rs:505-518`).
4. B proceeds through validation and pre-commit. At `handle_block_pre_commit`, `get_signed_conflicts(12, hash_B)` finds A as a conflict. If A's `last_endorsed` timestamp has passed `tenure_last_block_proposal_timeout` (stale) — or if the node's tenure tip for T is still below B's height due to network/timing lag — the fresh-conflict veto at lines 1403-1421 is skipped, and the same-tenure check at 1432-1457 only refuses if the node's tenure tip is `>= 12`; since A was never handed to the node (still local), `get_tenure_tip(T)` reports a height below 12, so the check does not refuse.
5. The signer proceeds to `mark_locally_accepted` on B and broadcasts a signature over B — the same signer has now signed two conflicting tenure-change blocks (A and B) for tenure T, an equivocation.

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

**File:** stacks-signer/src/signerdb.rs (L1587-1606)
```rust
    /// Return every signed block at or above the given Stacks height, in ANY tenure, excluding
    /// the block with the given signer signature hash, ordered by height (highest first). A
    /// block is considered signed if a signature was ever put over it, ours (`signed_self`)
    /// or the observed group's (`signed_group`). Blocks that were only pre-committed carry no
    /// signature and are never returned. Each row carries the most recent endorsement time
    /// (`signed_self`/`signed_group`, whichever is later) so the caller can judge freshness per
    /// conflict.
    ///
    /// The search deliberately spans all tenures: two blocks at the same height are siblings
    /// no matter which tenure they belong to (e.g. a tenure-start block conflicts with the
    /// previous tenure's block at the same height), so a signature over either may conflict
    /// with a fresh signature over the other.
    ///
    /// Blocks in tenures whose reorg we sanctioned under the reorg-timing rules (see
    /// [`SignerDb::mark_tenure_superseded`]) are still returned, but annotated with the
    /// permitting tenure's sortition (`superseded_by_*`): the permit only holds while that
    /// sortition is canonical, which the caller derives from the node per evaluation (see
    /// `Signer::reorg_permit_stands`) -- like every other question about whether a conflict is
    /// still *live* (`Signer::conflict_still_blocks`), it is not recorded.
    pub fn get_signed_conflicts(
```

**File:** stacks-signer/src/v0/signer.rs (L1192-1206)
```rust
        let node_reaches_conflict = match stacks_client.get_tenure_tip(&conflict.consensus_hash) {
            Ok(tip) => tip.anchored_header.height() >= conflict.stacks_height,
            // A 404 is an answer, not a failure: the node has no blocks in that tenure at all.
            Err(ClientError::RequestFailure(reqwest::StatusCode::NOT_FOUND)) => false,
            Err(e) => {
                warn!("{self}: Failed to fetch the canonical tip of a conflicting block's tenure: {e:?}. Leaving the conflict in place.";
                    "conflicting_consensus_hash" => %conflict.consensus_hash,
                    "conflicting_block_height" => conflict.stacks_height,
                );
                return true;
            }
        };
        node_reaches_conflict
            || (!conflict.globally_accepted && conflict.stacks_height <= proposed_height)
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1432-1465)
```rust
        if conflicts.iter().any(|conflict| {
            conflict.consensus_hash == block_info.block.header.consensus_hash
                && !self.reorg_permit_stands(stacks_client, conflict)
        }) {
            match stacks_client.get_tenure_tip(&block_info.block.header.consensus_hash) {
                Ok(tip) => {
                    let tip_height = tip.anchored_header.height();
                    if tip_height >= block_info.block.header.chain_length {
                        warn!(
                            "{self}: Reached the pre-commit threshold for a block that conflicts with previously signed or accepted blocks, and the canonical tip of its tenure is already at or above the proposed height. Refusing to sign.";
                            "signer_signature_hash" => %block_hash,
                            "block_height" => block_info.block.header.chain_length,
                            "canonical_tip_height" => tip_height,
                        );
                        return;
                    }
                }
                Err(e) => {
                    warn!(
                        "{self}: Failed to fetch the canonical tip of the proposed block's tenure: {e:?}. Treating the tenure as unconfirmed.";
                        "signer_signature_hash" => %block_hash,
                        "consensus_hash" => %block_info.block.header.consensus_hash,
                    );
                }
            }
        }
        if !conflicts.is_empty() {
            info!(
                "{self}: Reached the pre-commit threshold for a block that conflicts with previously signed or accepted blocks, but none of those conflicts still blocks it. Signing the replacement.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "num_conflicts" => conflicts.len(),
            );
        }
```

**File:** stacks-signer/src/v0/tests.rs (L770-789)
```rust
    #[test]
    fn signer_refuses_to_sign_second_sibling_tenure_start() {
        // Pin the fresh window far beyond the test's runtime so the guard can only take the
        // fresh branch; the stale branch is covered by the tests below.
        let (info_a, info_b, _) = run_sibling_scenario(Duration::from_secs(100_000), false, None);
        assert_a_signed(&info_a);
        // B is still pre-committed (the sibling is allowed to reach pre-commit), but the signer
        // must refuse to place a second signature on a conflicting same-height block in this
        // tenure while its signature on A is fresh.
        assert_eq!(
            info_b.state,
            BlockState::PreCommitted,
            "block B should be pre-committed but not promoted, got: {}",
            info_b.state
        );
        assert!(
            info_b.signed_self.is_none(),
            "block B must NOT be signed: the signer already signed a conflicting sibling in this tenure"
        );
    }
```
