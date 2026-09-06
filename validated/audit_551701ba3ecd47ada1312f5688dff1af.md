### Title
Signer's tenure-change parent-identity check runs only once at proposal time and is never re-verified before the block is signed - ([File: stacks-signer/src/v0/signer.rs])

### Summary
The v0 signer's `check_proposal` path performs a one-time "identity" check that a tenure-change block's declared parent tenure (`prev_tenure_consensus_hash`) actually matches the sortition's real parent tenure (`parent_tenure_id`). This check exists specifically to stop a miner from forging a false parent link ("catches block commits with bad parent_block_ptr, e.g., vtxindex=0 exploit"). However, this identity check is embedded only in `validate_tenure_change_payload`, which is called exclusively from `check_proposal` — the code path that runs once, when the block is first proposed. The later re-validation paths that run after the (potentially long) asynchronous node validation completes, and again right before the pre-commit threshold turns into a signature (`check_block_against_signer_db_state`), never re-run this identity check. They only call `check_tenure_change_confirms_parent`, which trusts the block's own `prev_tenure_consensus_hash` value and merely checks freshness/height against the last signed block in *that* tenure — it does not verify that tenure is still the legitimate parent tenure of the current/canonical sortition.

### Finding Description
`validate_tenure_change_payload` (both v1 and v2) contains the security-critical binding check: [1](#0-0) [2](#0-1) 

This is only reached through `check_proposal` (v1.rs:317-326, v2.rs delegated from the block-proposal wrapper), i.e. at the moment the block is *first* received as a proposal.

The re-check function used later — at validate-ok time and again at the pre-commit-threshold-to-signature moment — is `check_block_against_signer_db_state`: [3](#0-2) 

For a tenure-change block, this only calls `SortitionData::check_tenure_change_confirms_parent`, which in turn calls `check_latest_block_in_tenure` using the block's own `tenure_change.prev_tenure_consensus_hash` — it never re-compares that value against the (possibly now-different) `parent_tenure_id` of the currently canonical sortition: [4](#0-3) 

The project's own flow documentation confirms that the parent-identity/duplicate checks performed in `validate_tenure_change_payload` belong to the proposal path only and are explicitly **not** re-run at validate-ok or at signing: [5](#0-4) 

This is the same bug class as the `youki` symlink issue: a security-relevant identity/link ("does this claimed parent actually resolve to the real parent?") is validated once, at "open" time, but the object that gets acted on later (the actual signature over the block) is produced without re-resolving/re-checking that identity — a classic TOCTOU. Here, the "symlink" is `prev_tenure_consensus_hash`; the "real target" is the sortition's actual `parent_tenure_id`, which can legitimately change between proposal time and signing time (a burnchain reorg reassigns which tenure is canonical/parent, `capitulate_miner_view` adopts a different threshold view, or the local `cur_sortition` is invalidated/replaced — see `check_proposal` in v1.rs:144-203, which itself acknowledges the sortition view can flip to `InvalidatedBeforeFirstBlock` or detect a non-canonical parent tenure at proposal time only).

Because block validation against the node (`validate()` in `postblock_proposal.rs`) is asynchronous and can take real time, and pre-commit aggregation waits for further peer messages, there is a genuine window between "parent identity checked" and "signature produced" in which the signer's view of the canonical parent tenure can move out from under the already-accepted-for-validation block, with no code path re-asserting the binding that `validate_tenure_change_payload` was designed to enforce.

### Impact Explanation
If the parent-tenure binding drifts between proposal and signing, and `check_tenure_change_confirms_parent`'s freshness/height check (which only reasons about the tenure named in the payload) still returns true, a single signer can proceed to `mark_pre_committed` / `mark_locally_accepted` and emit a signature over a tenure-change block whose declared parent tenure no longer corresponds to the canonical/expected parent tenure — i.e., a signer signing a non-canonical/invalid block. This directly breaks the "approved-parent vs canonical" equality the signer's whole chainstate-check design exists to protect, and is exactly the class of bug (`vtxindex=0`-style parent forging) that `validate_tenure_change_payload`'s comment says it was built to stop, just reachable through the missing re-check rather than through the original forging vector.

### Likelihood Explanation
Reachable by a single miner (who controls the timing/content of the tenure-change proposal and can attempt to time it around burnchain activity) plus normal gossip/validation latency, without needing any other signer's key, majority collusion, or local access. The likelihood depends on the timing window between proposal and validate-ok/pre-commit being large enough for the sortition view's parent-tenure resolution to change (e.g. via a fast-following burn block, fork, or `capitulate_miner_view` picking up a different threshold view), which the codebase already contemplates as a real, not merely theoretical, occurrence (see `check_proposal`'s own invalidation branches and section 8 of `docs/signer-flows.md` on `capitulate_miner_view`/burn-block races).

### Recommendation
Re-run the `prev_tenure_consensus_hash == parent_tenure_id` (and ideally the rest of `validate_tenure_change_payload`'s identity checks) inside `check_block_against_signer_db_state`, using the currently-refreshed sortition/miner-state view, rather than relying solely on `check_tenure_change_confirms_parent`'s freshness check against the payload's self-declared tenure. At minimum, the pre-commit-to-signature path should reject if the block's claimed parent tenure no longer matches the signer's current canonical view of the parent tenure.

### Proof of Concept
1. Miner proposes tenure-change block `B` with `prev_tenure_consensus_hash = X`, matching the signer's current `cur_sortition.data.parent_tenure_id = X` at proposal time; `check_proposal` → `validate_tenure_change_payload` passes (stacks-signer/src/chainstate/v1.rs:469-481).
2. The signer submits `B` to the node's `/v3/block_proposal` endpoint for validation (`postblock_proposal.rs::validate`), which is asynchronous and can take noticeable wall-clock time for larger blocks.
3. Before `handle_block_validate_ok` fires, a burnchain event (fork/new burn block/`capitulate_viewpoint`) shifts the signer's view of the canonical parent tenure away from `X` (e.g. to `Y`), per the flows in `docs/signer-flows.md` section 8.
4. `handle_block_validate_ok` calls `check_block_against_signer_db_state` (stacks-signer/src/v0/signer.rs:1803-1840), which for the tenure-change block only calls `check_tenure_change_confirms_parent` against `X` (the block's own stale claim) — it never re-derives or re-compares against the now-current parent tenure `Y`.
5. If `X`'s last-signed-block freshness check still passes, the signer proceeds to `mark_pre_committed`/sign `B`, producing a signature over a tenure-change block whose parent-tenure binding is no longer the canonical one — with no re-run of the `validate_tenure_change_payload` identity check anywhere in this path.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L469-481)
```rust
        // Check that the tenure change's prev_tenure matches the sortition's known parent tenure.
        // This catches block commits with bad parent_block_ptr (e.g., vtxindex=0 exploit).
        let parent_tenure_id = &proposed_by.state().data.parent_tenure_id;
        if &tenure_change.prev_tenure_consensus_hash != parent_tenure_id {
            warn!(
                "Block commit parent tenure mismatch: the block commit's parent_block_ptr does not correspond to the actual parent tenure";
                "committed_parent_tenure" => %parent_tenure_id,
                "actual_parent_tenure" => %tenure_change.prev_tenure_consensus_hash,
                "consensus_hash" => %block.header.consensus_hash,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
            );
            return Err(RejectReason::InvalidParentBlock);
        }
```

**File:** stacks-signer/src/chainstate/v2.rs (L314-325)
```rust
        // Check that the tenure change's prev_tenure matches the signer's known parent tenure.
        // This catches block commits with bad parent_block_ptr (e.g., vtxindex=0 exploit).
        if &tenure_change.prev_tenure_consensus_hash != parent_tenure_id {
            warn!(
                "Block commit parent tenure mismatch: the block commit's parent_block_ptr does not correspond to the actual parent tenure";
                "committed_parent_tenure" => %parent_tenure_id,
                "actual_parent_tenure" => %tenure_change.prev_tenure_consensus_hash,
                "consensus_hash" => %block.header.consensus_hash,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
            );
            return Err(RejectReason::InvalidParentBlock);
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1799-1826)
```rust
    /// WARNING: This is an incomplete check. Do NOT call this function PRIOR to check_proposal or block_proposal validation succeeds.
    ///
    /// Re-verify a block's chain length against the last signed block within signerdb.
    /// This is required in case a block has been approved since the initial checks of the block validation endpoint.
    fn check_block_against_signer_db_state(
        &mut self,
        stacks_client: &StacksClient,
        proposed_block: &NakamotoBlock,
    ) -> Option<BlockRejection> {
        let signer_signature_hash = proposed_block.header.signer_signature_hash();
        // If this is a tenure change block, ensure that it confirms the correct number of blocks from the parent tenure.
        if let Some(tenure_change) = proposed_block.get_tenure_change_tx_payload() {
            // Ensure that the tenure change block confirms the expected parent block
            match SortitionData::check_tenure_change_confirms_parent(
                tenure_change,
                proposed_block,
                &mut self.signer_db,
                stacks_client,
                self.proposal_config.tenure_last_block_proposal_timeout,
                self.proposal_config.reorg_attempts_activity_timeout,
            ) {
                Ok(true) => return None,
                Ok(false) => {
                    return Some(self.create_block_rejection(
                        RejectReason::SortitionViewMismatch,
                        proposed_block,
                    ))
                }
```

**File:** stacks-signer/src/chainstate/mod.rs (L488-504)
```rust
    pub fn check_tenure_change_confirms_parent(
        tenure_change: &TenureChangePayload,
        block: &NakamotoBlock,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        tenure_last_block_proposal_timeout: Duration,
        reorg_attempts_activity_timeout: Duration,
    ) -> Result<bool, ClientError> {
        Self::check_latest_block_in_tenure(
            &tenure_change.prev_tenure_consensus_hash,
            block,
            signer_db,
            client,
            tenure_last_block_proposal_timeout,
            reorg_attempts_activity_timeout,
        )
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
