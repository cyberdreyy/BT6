## Analysis

I found a concrete analog to the NFT-reuse bug: a check performed at proposal time using a **narrower predicate** than the one enforced at signing time, allowing a proposal to pass a check that later gets "spent" against a different, laxer criterion — mirroring how the Particle Exchange bug checked `ownerOf() == address(this)` without asking whether that specific NFT was *already* consumed by another lien.

### Title
Stale `DuplicateBlockFound` predicate in signer v1 (`get_last_globally_accepted_block`) lets a second tenure-start block reach and cross the pre-commit threshold while only locally accepted — ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionsView::validate_tenure_change_payload` (v1 protocol) rejects a new tenure-change block with `RejectReason::DuplicateBlockFound` only if `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` returns `Some(_)` [1](#0-0) . The v2 path uses the strictly broader `get_last_signed_block`, which also counts **locally accepted** blocks, and the project's own documentation calls this out explicitly: "v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1 counts only globally accepted ones (`get_last_globally_accepted_block`)" and warns "Because the duplicate check never runs again, a block that crosses the pre-commit threshold long after it was proposed relies on section 5's own-tenure conflict guard to cover the same ground." [2](#0-1) 

### Finding Description
This check runs **once**, at proposal-arrival time only, and is never re-run at validate-ok or at signing time [2](#0-1) . A v1-protocol signer that has already **locally** accepted (signed) a first tenure-start block A in tenure T, but has not yet observed the network reach 70% (so A is not yet "globally accepted"), will not have any `last_globally_accepted_block` recorded for T. If the miner (or gossip) now proposes a second, conflicting tenure-start block B for the same tenure T, `get_last_globally_accepted_block(T)` still returns `None`, so `validate_tenure_change_payload` does **not** raise `DuplicateBlockFound` — the equivalent of the NFT contract not checking "is this NFT already consumed by an earlier lien" before accepting a new claim against it.

The only remaining safety net is the pre-commit-time "own-tenure conflict guard" in `handle_block_pre_commit`, which relies on `get_signed_conflicts` — but that function only returns blocks that are **signed** (`signed_self`/`signed_group` set) [3](#0-2) . Since this signer *has* signed A (`signed_self` is set the moment it locally accepted A), A does appear as a "signed conflict" and the freshness/`conflict_still_blocks` logic in `handle_block_pre_commit` (lines 1403–1457 of `stacks-signer/src/v0/signer.rs`) is the actual backstop, not the proposal-time check. This backstop is honored in the single-signer local view. However, the proposal-time gap still matters cross-signer: a signer that has **not yet signed A itself** (e.g. it is still waiting on validation, or it rejected A for an unrelated reason) has no `signed_self`/`signed_group` entry for A in its own DB, so `get_signed_conflicts` finds nothing, and `check_block_against_signer_db_state`'s `confirms_expected_parent`/`DuplicateBlockFound` gate (proposal-time only) was the sole line of defense — and for v1 it was already bypassed by the `None` from `get_last_globally_accepted_block`. This lets a subset of v1 signers pre-commit and eventually sign B for the same tenure that another subset of signers already signed A for, purely because the miner (single actor, no majority of signers needed) re-proposes a duplicate tenure-start block before global acceptance of A propagates.

### Impact Explanation
This falls under the Critical bucket: **a signer signing an invalid/non-canonical/conflicting block**. Two conflicting tenure-start blocks (A and B) for the same tenure T can each independently accumulate signer weight before any single signer's local conflict-detection would veto the second one, because the proposal-time duplicate check (v1) is keyed to a narrower "globally accepted" predicate that a first block frequently will not have satisfied yet. This can produce a split where enough distinct signers sign A and enough (different) signers sign B, undermining the "one canonical block per tenure-start" invariant that `DuplicateBlockFound` exists to enforce.

### Likelihood Explanation
Reachable by a single miner plus ordinary gossip timing, with no majority of signers, no other signer's key, and no `auth_token`/local access required — exactly as the rules require. The race window is the normal delay between a signer locally accepting a block and the network reaching the 70% global-acceptance threshold, which is a routine, non-adversarial timing gap that a miner can trivially widen by re-proposing a competing tenure-start block immediately after the first one is signed by some signers but before the group threshold is reached.

### Recommendation
Align v1's `validate_tenure_change_payload` duplicate check with v2's: use `get_last_signed_block` (which includes locally accepted blocks) instead of `get_last_globally_accepted_block`, so a signer's own local acceptance of a tenure-start block is enough to reject a competing proposal for the same tenure at proposal time, closing the same gap v2 already closed. Additionally, consider making `get_signed_conflicts`/`handle_block_pre_commit`'s own-tenure guard the single source of truth documented as covering all versions, since the proposal-time check is stated to "never run again."

### Proof of Concept
Not independently executable from static review (no test harness run), but the code paths are cited directly:
1. `stacks-signer/src/chainstate/v1.rs` lines 505–518 — v1 duplicate check uses `get_last_globally_accepted_block`.
2. `stacks-signer/src/chainstate/v2.rs` lines 340–357 — v2 duplicate check uses `get_last_signed_block` (broader).
3. `docs/signer-flows.md` lines 425–437 — explicit repo documentation stating the discrepancy and that "the duplicate check never runs again."
4. `stacks-signer/src/signerdb.rs` lines 1564–1585 — definition of `get_last_signed_block` (locally or globally accepted) vs. globally-accepted-only variant, confirming the predicate gap.

**Uncertainty**: I could not run the existing test suite (e.g., `stacks-signer/src/chainstate/tests/v2.rs`) to confirm there is no additional v1-specific regression test covering this exact race, nor could I confirm whether `check_block_against_signer_db_state`'s re-check at signing time (section 7 of the docs, `check_latest_block_in_tenure`) independently closes this v1 gap for tenure-change blocks in all cases — the docs state it checks the **parent** tenure for tenure-change blocks, not the tenure-change block's own tenure, which is precisely why the `DuplicateBlockFound` check (own-tenure duplicate) is proposal-path-only and not substitutable by it. A Devin session with the ability to run `stacks-signer`'s unit/integration tests would be needed to fully confirm exploitability end-to-end.

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

**File:** stacks-signer/src/signerdb.rs (L1587-1600)
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
```
