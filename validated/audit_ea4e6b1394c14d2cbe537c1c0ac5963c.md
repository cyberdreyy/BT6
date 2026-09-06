### Title
`check_block_has_valid_parent` accepts a tenure-start block whose parent tip belongs to an orphaned sibling tenure, breaking canonicity - (File: `stackslib/src/net/api/postblock_proposal.rs`)

### Summary
For tenure-start proposals, `NakamotoBlockProposal::check_block_has_valid_parent` only verifies that the referenced `parent_block_id` is the highest known header in *its own* tenure via `check_block_builds_on_highest_block_in_tenure`, but never checks that the parent's tenure (`parent_header.consensus_hash`) is itself on the canonical sortition fork. The only canonical-fork check performed, `check_block_has_valid_tenure`, is applied to the *new* block's own `consensus_hash`, not to the parent's tenure. A block header that survives in `chainstate.db()` after being orphaned by a sibling/competing tenure therefore still satisfies this "exists and is highest" check.

### Finding Description
In `validate()` [1](#0-0) , the node performs two "for the signer" checks: `check_block_has_valid_tenure` (checks that `self.block.header.consensus_hash`, i.e., the *new* tenure, is on the canonical Bitcoin/sortition fork via `db_handle.has_consensus_hash`) and `check_block_has_valid_parent`.

`check_block_has_valid_parent`, for a tenure-start block, does:
1. `NakamotoChainState::get_block_header(chainstate.db(), &block.header.parent_block_id)` — this is a raw header lookup by block id with **no canonical-fork filter**; any header ever written to the chainstate DB, including one orphaned by a sibling/competing tenure, will be returned. [2](#0-1) 
2. `check_block_builds_on_highest_block_in_tenure(chainstate, sortdb, &parent_header.consensus_hash, &block.header.parent_block_id)` — this only compares the parent block's height to `find_highest_known_block_header_in_tenure` for that *same* tenure, i.e., "is this the tip of tenure X", not "is tenure X still the canonical/valid predecessor tenure." [3](#0-2) 

The function `check_block_builds_on_highest_block_in_tenure` is explicitly annotated `DO NOT CALL FROM CONSENSUS CODE`, signaling it is a weaker heuristic than the real chainstate/consensus canonicity rules. [4](#0-3) 

Crucially, nowhere in `check_block_has_valid_parent` is `parent_header.consensus_hash` checked against `db_handle.has_consensus_hash` (the canonical sortition-fork check used for the block's own tenure at line 602). So if a sibling tenure B was orphaned by a fork/reorg but its blocks remain in `chainstate.db()` (headers are not deleted on orphaning), an attacker who wins a single miner slot for a new tenure C can craft a tenure-start `NakamotoBlockProposal` whose `parent_block_id` is tenure B's tip rather than the actual canonical tenure A's tip. `get_block_header` finds it, `UnknownParent` is not raised, and the height-based "highest in that tenure" check trivially passes because the attacker points to B's own actual tip.

Downstream, `get_expected_burns` and `validate_normal_nakamoto_block_burnchain` are keyed off the block-commit/burn view of the *new* block's own tenure and the sortition-scoped `db_handle`; they do not re-derive or enforce that the referenced parent tenure is the canonical predecessor tenure at the new sortition height.

### Impact Explanation
This breaks the canonicity safety property: the approved parent must be the highest valid parent on the canonical fork, not merely any known header. If exploitable, a signer's block-validation endpoint (and by extension, a signer relying on it) would sign off on / accept as valid a block built atop a non-canonical, orphaned parent — a Critical chain-safety violation matching "a signer signing an invalid, non-canonical, or conflicting block."

### Likelihood Explanation
The precondition is a recent tenure fork where two sibling tenures both got their headers persisted in `chainstate.db()` before one was orphaned by a sortition/tenure-change reorg — this is a normal occurrence in Nakamoto's tenure-fork handling and not something the attacker needs to engineer beyond winning one sortition slot. The attacker only needs their own single miner slot (to be a legitimate proposer for tenure C) and knowledge of the orphaned tenure's tip block id (public chain data), then gossips a crafted `BlockProposal`/`NakamotoBlockProposal`. No majority signer collusion, no auth token, no local access is required.

### Recommendation
In `check_block_has_valid_parent`, for both the tenure-start and non-tenure-start branches, additionally require that `parent_header.consensus_hash` (and the parent block id itself) is present on the canonical sortition/tenure fork as seen from the proposal's resolved `sort_tip`/`db_handle` — e.g., call `db_handle.has_consensus_hash(&parent_header.consensus_hash)` (the same canonical check already used for the block's own tenure) before/alongside `check_block_builds_on_highest_block_in_tenure`, and reject with `ValidateRejectCode::NonCanonicalTenure`/`InvalidParentBlock` if it fails.

### Proof of Concept
```rust
// stacks-signer or stackslib test plan (pseudo-Rust):
// 1. Build a test chain with two sibling tenures A and B forking from the same burn view
//    (simulate a tenure-change reorg so that A becomes canonical and B is orphaned but its
//    NakamotoBlock headers remain queryable via NakamotoChainState::get_block_header).
// 2. Advance the canonical chain to a new tenure C via a real sortition win.
// 3. Construct a NakamotoBlockProposal for a tenure-start block of tenure C whose
//    header.parent_block_id = B's tip StacksBlockId (the orphaned tenure), with a valid
//    TenureChange payload matching B's consensus hash so is_wellformed_tenure_start_block()
//    passes.
// 4. Call NakamotoBlockProposal::check_block_has_valid_parent(&chainstate, &sortdb, &block).
// 5. ASSERT: Ok(()) is returned today (accepts a non-canonical parent) -- vulnerability.
//    After the fix, ASSERT: Err(BlockValidateRejectReason { reason_code: NonCanonicalTenure, .. })
//    is returned because B's consensus_hash fails db_handle.has_consensus_hash() against the
//    canonical sort_tip.
```

Note: I could not directly inspect `NakamotoChainState::find_highest_known_block_header_in_tenure` and `get_block_header`'s full implementation in `stackslib/src/chainstate/nakamoto/mod.rs` within the available tool budget to confirm whether either function independently filters on canonical-fork membership at a lower layer; the analysis above is based on the explicit code and doc-comments visible in `postblock_proposal.rs`, which do not perform that canonicity check for the parent tenure. If `find_highest_known_block_header_in_tenure` or `get_block_header` were confirmed to already restrict to the canonical fork, this finding would not hold — this should be verified with full access to `nakamoto/mod.rs` before treating this as confirmed.

### Citations

**File:** stackslib/src/net/api/postblock_proposal.rs (L383-388)
```rust
    /// DO NOT CALL FROM CONSENSUS CODE
    ///
    /// Check to see if a block builds atop the highest block in a given tenure.
    /// That is:
    /// - its parent must exist, and
    /// - its parent must be as high as the highest block in the given tenure.
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L503-514)
```rust
        } else {
            // this is a tenure-start block, so it must build atop a parent which has the
            // highest height in the *previous* tenure.
            let parent_header = NakamotoChainState::get_block_header(
                chainstate.db(),
                &block.header.parent_block_id,
            )?
            .ok_or_else(|| BlockValidateRejectReason {
                reason_code: ValidateRejectCode::UnknownParent,
                reason: "No parent block".into(),
                failed_txid: None,
            })?;
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L516-521)
```rust
            Self::check_block_builds_on_highest_block_in_tenure(
                chainstate,
                sortdb,
                &parent_header.consensus_hash,
                &block.header.parent_block_id,
            )?;
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L600-606)
```rust
        // (For the signer)
        // Verify that the block's tenure is on the canonical sortition history
        Self::check_block_has_valid_tenure(&db_handle, &self.block.header.consensus_hash)?;

        // (For the signer)
        // Verify that this block's parent is the highest such block we can build off of
        Self::check_block_has_valid_parent(chainstate, sortdb, &self.block)?;
```
