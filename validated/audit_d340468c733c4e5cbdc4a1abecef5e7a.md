### Title
Height-only equality in `check_block_builds_on_highest_block_in_tenure` lets a proposal build on a non-canonical sibling parent - (File: `stackslib/src/net/api/postblock_proposal.rs`)

### Summary
`check_block_builds_on_highest_block_in_tenure` accepts any `parent_block_id` whose header height equals the height of the tenure's canonical `highest_header`, without verifying that `parent_block_id` actually *is* `highest_header.block_id()`. When two distinct blocks exist locally at the same height in the same tenure (a same-height sibling pair), a proposal naming the non-canonical sibling as parent passes this check and can be signed by a signer, violating canonicity.

### Finding Description
The guard is: [1](#0-0) 

`highest_header` is obtained via `NakamotoChainState::find_highest_known_block_header_in_tenure`, which explicitly tie-breaks among *candidate* headers collected per burn view: [2](#0-1)  The helper's own doc comment states: "If there are ties at a given burn view, they will both be returned," and the function is explicitly marked "DO NOT USE IN CONSENSUS CODE. Different nodes can have different blocks for the same tenure," confirming that same-height sibling headers are an anticipated, real occurrence in this table, not a hypothetical edge case.

`parent_header` is fetched independently via `NakamotoChainState::get_block_header(chainstate.db(), parent_block_id)` — i.e., by the attacker-supplied `parent_block_id` from the `NakamotoBlock`'s header, with no cross-check against `highest_header.block_id()` [3](#0-2) .

The final comparison at line 435 is a height equality, not a block-id equality: [4](#0-3) 

Exploit flow: if the node locally holds two headers A ("true" highest/canonical, selected by `get_highest_canonical_block_header_from_candidates`) and B (a same-height sibling that lost the tie-break) in the same tenure, an attacker crafts a non-tenure-start `BlockProposal` whose `parent_block_id = B.block_id()`. `check_block_builds_on_highest_block_in_tenure` computes `highest_header = A`, `parent_header = B`; since `A.height() == B.height()`, the check passes even though `parent_block_id != highest_header.block_id()`. Validation then proceeds to build/replay state atop B rather than the canonical tip A, and if the resulting `BlockValidateOk` is returned, the signer's downstream acceptance logic (`check_block_against_signer_db_state`, which relies on this same `validate()` result) may end up signing a block whose true canonical parent is not what the tenure's fork-choice rule designates.

No other check in this path re-derives or enforces block-id equality; `check_block_has_valid_tenure` only checks the tenure's consensus hash is on the canonical Bitcoin fork, not intra-tenure block identity [5](#0-4) .

### Impact Explanation
This breaks canonicity: a signer can be induced to sign a block that legitimately validates (correct state transition) but whose claimed parent is a non-canonical sibling rather than the tenure's actual highest/canonical block. This matches the Critical category — "a signer signing an invalid, non-canonical, or conflicting block." A single malicious/opportunistic miner-slot holder able to produce or reference an existing same-height sibling pair and then propose a child on the non-canonical one can trigger this repeatably across tenures where such ties occur.

### Likelihood Explanation
Requires a precondition: two distinct headers at the same height within the same tenure/burn-view already known locally to the validating node (a tie, which the codebase's own comments acknowledge as a real, non-error condition arising e.g. from near-simultaneous proposals or burn-view changes within a tenure). Given that precondition, the attack requires only crafting one `BlockProposal` naming the "losing" sibling as parent — well within the described unprivileged, single-miner-slot, gossip-only capability. The precondition itself (getting two same-height headers known) is the harder part and depends on tenure/fork timing, so likelihood is conditioned on that fork existing, but once it does, exploitation is trivial and deterministic.

### Recommendation
Change the comparison at line 435 to require `parent_block_id == &highest_header.index_block_hash()` (or equivalent `block_id()` equality) in addition to (or instead of) the height check, so that only the actual canonical highest header for the tenure/burn-view can serve as parent.

### Proof of Concept
Rust test plan (in `stackslib/src/net/api/postblock_proposal.rs` test module or `chainstate/nakamoto` test harness):
1. Construct a tenure and mine two Nakamoto blocks A and B at the same `block_height` (same `burn_view`), both persisted into `nakamoto_block_headers` (simulating a same-height sibling pair), such that `get_highest_canonical_block_header_from_candidates` selects A as canonical.
2. Craft a non-tenure-start `NakamotoBlock` C with `header.parent_block_id = B.block_id()`.
3. Call `NakamotoBlockProposal::check_block_builds_on_highest_block_in_tenure(&chainstate, &sortdb, &tenure_id, &B.block_id())` directly.
4. Assert (pre-fix) that it returns `Ok(())` even though `B.block_id() != A.block_id()` — demonstrating the vulnerable equality.
5. Assert (post-fix) that the same call returns `Err(BlockValidateRejectReason { reason_code: ValidateRejectCode::InvalidParentBlock, .. })` once the check is changed to compare `block_id()`/`index_block_hash()` instead of height alone.

### Citations

**File:** stackslib/src/net/api/postblock_proposal.rs (L395-448)
```rust
        let Some(highest_header) = NakamotoChainState::find_highest_known_block_header_in_tenure(
            chainstate, sortdb, tenure_id,
        )
        .map_err(|e| BlockValidateRejectReason {
            reason_code: ValidateRejectCode::ChainstateError,
            reason: format!("Failed to query highest block in tenure ID: {:?}", &e),
            failed_txid: None,
        })?
        else {
            warn!(
                "Rejected block proposal";
                "reason" => "Block is not a tenure-start block, and has an unrecognized tenure consensus hash",
                "consensus_hash" => %tenure_id,
            );
            return Err(BlockValidateRejectReason {
                reason_code: ValidateRejectCode::NoSuchTenure,
                reason: "Block is not a tenure-start block, and has an unrecognized tenure consensus hash".into(),
                failed_txid: None,
            });
        };
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

**File:** stackslib/src/net/api/postblock_proposal.rs (L454-472)
```rust
    pub(crate) fn check_block_has_valid_tenure(
        db_handle: &SortitionHandleConn,
        tenure_id: &ConsensusHash,
    ) -> Result<(), BlockValidateRejectReason> {
        // Verify that the block's tenure is on the canonical sortition history
        if !db_handle.has_consensus_hash(tenure_id)? {
            warn!(
                "Rejected block proposal";
                "reason" => "Block's tenure consensus hash is not on the canonical Bitcoin fork",
                "consensus_hash" => %tenure_id,
            );
            return Err(BlockValidateRejectReason {
                reason_code: ValidateRejectCode::NonCanonicalTenure,
                reason: "Tenure consensus hash is not on the canonical Bitcoin fork".into(),
                failed_txid: None,
            });
        }
        Ok(())
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L3273-3322)
```rust
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

    /// DO NOT USE IN CONSENSUS CODE.  Different nodes can have different blocks for the same
    /// tenure.
    ///
    /// Get the highest blocks in a given tenure (identified by its consensus hash) at each burn view
    ///  active in that tenure. If there are ties at a given burn view, they will both be returned
    fn get_highest_known_block_header_in_tenure_at_each_burnview(
        db: &Connection,
        tenure_id: &ConsensusHash,
    ) -> Result<Vec<StacksHeaderInfo>, ChainstateError> {
        // see if we have a nakamoto block in this tenure
        let qry = "
        SELECT h.*
        FROM nakamoto_block_headers h
        JOIN (
            SELECT burn_view, MAX(block_height) AS max_height
            FROM nakamoto_block_headers
            WHERE consensus_hash = ?1
            GROUP BY burn_view
        ) maxed
        ON h.burn_view = maxed.burn_view
        AND h.block_height = maxed.max_height
        WHERE h.consensus_hash = ?1
        ORDER BY h.block_height DESC, h.timestamp
        ";
        let args = params![tenure_id];
        let out = query_rows(db, qry, args)?;
        if !out.is_empty() {
            return Ok(out);
        }

        // see if this is an epoch2 header. If it exists, then there will only be one.
        let epoch2_x =
            StacksChainState::get_stacks_block_header_info_by_consensus_hash(db, tenure_id)?;
        Ok(Vec::from_iter(epoch2_x))
    }
```
