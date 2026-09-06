### Title
Height-only equality check in `check_block_builds_on_highest_block_in_tenure` lets a signature be placed over a block built on a stale/sibling parent instead of the true tenure tip - (File: stackslib/src/net/api/postblock_proposal.rs)

### Summary
`NakamotoBlockProposal::check_block_builds_on_highest_block_in_tenure` (the node-side check invoked from `validate()` during `/v3/block_proposal` processing) is supposed to enforce that a proposed continuation block's parent is *the* highest known block in its tenure. It instead only compares block **heights**, not block **identity** (hash/`StacksBlockId`), between the proposal's declared parent and the tenure's highest known header.

### Finding Description
`check_block_builds_on_highest_block_in_tenure` looks up:
- `highest_header` = `find_highest_known_block_header_in_tenure(...)` for the block's `consensus_hash` [1](#0-0) 
- `parent_header` = `get_block_header(..., parent_block_id)`, i.e. whatever block the proposal *claims* as its parent [2](#0-1) 

It then accepts the proposal as long as:
```
parent_header.anchored_header.height() == highest_header.anchored_header.height()
``` [3](#0-2) 

This is a height *equality* check standing in for an identity check ("is this the highest block"), exactly the class of bug in the Django `is_safe_url()` CVE where a "safe"/"valid" predicate accepted inputs that were superficially similar (same shape) but not the actual value the check was meant to validate. Here, `find_highest_known_block_header_in_tenure_at_each_burnview` explicitly documents that ties can and do occur ("If there are ties at a given burn view, they will both be returned") and the node's own doc-comments warn `DO NOT USE IN CONSENSUS CODE. Different nodes can have different blocks for the same tenure" [4](#0-3) . If the node has ingested (via gossip/relay/stackerdb) a *different* header at the same height as the proposal's claimed parent — e.g. a sibling produced by a re-proposed/duplicate tenure-start attempt, or an old orphaned block that never became canonical but was stored — `get_highest_canonical_block_header_from_candidates` picks one, and the height-only comparison in `check_block_builds_on_highest_block_in_tenure` will treat *any* stored header at that same height as satisfying "parent is highest," even if `parent_header` is not literally `highest_header`.

This matters specifically for the equality this rule is meant to police: "the signer's/node's view of validated-and-approved vs. the proposal's declared parent." The signer-side design (documented extensively in `docs/signer-flows.md` §5/§7 and enforced via `get_signed_conflicts`, `conflict_still_blocks`, `check_latest_block_in_tenure`) is built around the assumption that this low-level node check truly enforces "parent == tip," and multiple signer-side sibling-conflict guards exist precisely because block hash-identity, not merely height, is what must match for a block to be considered a legitimate continuation rather than an equivocating sibling [5](#0-4) .

### Impact Explanation
If exploitable, this allows a miner (with the one active mining slot for the current tenure) to get a block validated `Ok` — and hence pre-committed/signed by the signer set — whose declared parent is not actually the canonical/highest block of the tenure but merely a sibling at the same height. That is a "signer signing a non-canonical/conflicting block" scenario in the rules' Critical bucket, because the block-hash-based parent identity that downstream consensus code relies on (`check_block_has_valid_parent` calls this same height-only helper for both tenure-start and continuation cases) would have passed a check whose docstring promises "its parent must be as high as the highest block in the given tenure" but which is silently satisfied by a merely-same-height, different block.

### Likelihood Explanation
This requires the node to already have two distinct headers at the same height within one tenure in its `nakamoto_block_headers` table (a real possibility once a duplicate/sibling tenure-start block or block-at-same-height has been relayed/observed, as covered extensively by the sibling-race tests in `stacks-signer/src/v0/tests.rs::async_sibling_validation`). I was not able to fully confirm (within the available tool budget) whether `get_block_header`/staging ingestion actually persists an unconfirmed sibling into `nakamoto_block_headers` prior to full validation, which is the precondition for `parent_header` to resolve to a genuine sibling rather than erroring with "Block has no parent." This is the key unresolved uncertainty and would need to be validated against `NakamotoChainState::get_block_header` and the block-header insertion path (append_block / advance_tip) before treating this as a confirmed, not just plausible, exploit path.

### Recommendation
Change the comparison in `check_block_builds_on_highest_block_in_tenure` from a height comparison to an identity comparison, i.e. require `parent_header.index_block_hash() == highest_header.index_block_hash()` (or equivalently compare `StacksBlockId`s), so that a same-height-but-different block can never satisfy "parent is the highest block in the tenure."

### Proof of Concept
Not fully constructible with the available read-only access: a full PoC would require (1) confirming that `nakamoto_block_headers` can hold two rows at the same height/tenure prior to one being finalized as canonical (via relay of a NakamotoBlock that gets header-recorded without immediately being chosen as the tip), and (2) crafting a `NakamotoBlockProposal` whose `parent_block_id` points at the non-canonical sibling header, then submitting it to `/v3/block_proposal` to observe whether `check_block_builds_on_highest_block_in_tenure` (and thus `validate()`) returns `Ok` instead of `InvalidParentBlock`. This step was not completed due to reaching the tool-call budget for this investigation.

### Citations

**File:** stackslib/src/net/api/postblock_proposal.rs (L395-402)
```rust
        let Some(highest_header) = NakamotoChainState::find_highest_known_block_header_in_tenure(
            chainstate, sortdb, tenure_id,
        )
        .map_err(|e| BlockValidateRejectReason {
            reason_code: ValidateRejectCode::ChainstateError,
            reason: format!("Failed to query highest block in tenure ID: {:?}", &e),
            failed_txid: None,
        })?
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L415-434)
```rust
        let Some(parent_header) =
            NakamotoChainState::get_block_header(chainstate.db(), parent_block_id).map_err(
                |e| BlockValidateRejectReason {
                    reason_code: ValidateRejectCode::ChainstateError,
                    reason: format!("Failed to query block header by block ID: {:?}", &e),
                    failed_txid: None,
                },
            )?
        else {
            warn!(
                "Rejected block proposal";
                "reason" => "Block has no parent",
                "parent_block_id" => %parent_block_id
            );
            return Err(BlockValidateRejectReason {
                reason_code: ValidateRejectCode::UnknownParent,
                reason: "Block has no parent".into(),
                failed_txid: None,
            });
        };
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L435-448)
```rust
        if parent_header.anchored_header.height() != highest_header.anchored_header.height() {
            warn!(
                "Rejected block proposal";
                "reason" => "Block's parent is not the highest block in this tenure",
                "consensus_hash" => %tenure_id,
                "parent_header.height" => parent_header.anchored_header.height(),
                "highest_header.height" => highest_header.anchored_header.height(),
            );
            return Err(BlockValidateRejectReason {
                reason_code: ValidateRejectCode::InvalidParentBlock,
                reason: "Block is not higher than the highest block in its tenure".into(),
                failed_txid: None,
            });
        }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L3268-3286)
```rust
    /// DO NOT USE IN CONSENSUS CODE.  Different nodes can have different blocks for the same
    /// tenure.
    ///
    /// Get the highest block in a given tenure (identified by its consensus hash) with a canonical
    ///  burn_view (i.e., burn_view on the canonical sortition fork)
    pub fn find_highest_known_block_header_in_tenure(
        chainstate: &StacksChainState,
        sort_db: &SortitionDB,
        tenure_id: &ConsensusHash,
    ) -> Result<Option<StacksHeaderInfo>, ChainstateError> {
        let chainstate_db_conn = chainstate.db();

        let candidates = Self::get_highest_known_block_header_in_tenure_at_each_burnview(
            chainstate_db_conn,
            tenure_id,
        )?;

        Self::get_highest_canonical_block_header_from_candidates(sort_db, candidates)
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1368-1382)
```rust
        // A pre-commit may be superseded by a competing proposal at the same height (e.g. a
        // re-proposed tenure-start block after the first failed to reach consensus), but a
        // signature must not be superseded while it's still "fresh". A signed block at the
        // same or higher height in ANY tenure is a conflict: two blocks at the same height are
        // siblings no matter which tenure they belong to (e.g. the next tenure's tenure-start
        // block conflicts with the current tenure's block at the same height). Blocks in
        // tenures whose reorg we sanctioned under the reorg-timing rules are excluded, but
        // only while the sortition the permit was granted to is still canonical
        // (`check_parent_tenure_choice` records the permit, `reorg_permit_stands` re-derives
        // its validity from the node); every other question about whether a conflict is
        // still live is derived from the node in `conflict_still_blocks`.
        //
        // Unlike the chainstate check above, a refusal here is "for now" rather than a
        // broadcast rejection: a later pre-commit re-evaluation may still sign the block once
        // the conflicting signature has gone stale.
```
