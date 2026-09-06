### Title
`check_block_builds_on_highest_block_in_tenure` compares parent height only, not block identity, allowing a signer to sign atop a non-canonical sibling parent - ([File: stackslib/src/net/api/postblock_proposal.rs])

### Summary
`NakamotoBlockProposal::check_block_has_valid_parent` delegates to `check_block_builds_on_highest_block_in_tenure`, which validates a proposal's parent solely by comparing `parent_header.anchored_header.height()` to `highest_header.anchored_header.height()`. It never checks that the parent's block id/hash actually equals the highest-known header's id. If the node has stored two blocks at the same chain height in the same tenure (a legitimate/orphaned sibling pair caused by a reorg or an equivocating miner), a proposal whose parent is the non-canonical sibling will pass this check as long as the heights coincide.

### Finding Description
`check_block_has_valid_parent` (stackslib/src/net/api/postblock_proposal.rs:480-524) calls `check_block_builds_on_highest_block_in_tenure` (lines 389-450) for both tenure-start and non-tenure-start blocks. That helper: [1](#0-0) 
only compares `parent_header.anchored_header.height()` against `highest_header.anchored_header.height()`. There is no comparison of `parent_block_id` (or the parent header's block hash) against `highest_header`'s own id. `highest_header` itself is produced by `NakamotoChainState::find_highest_known_block_header_in_tenure`, which groups candidate headers per burn_view and picks the first candidate whose burn_view/consensus_hash is on the canonical Bitcoin sortition fork via `get_highest_canonical_block_header_from_candidates`: [2](#0-1) 
This canonicity test only checks whether the *sortition/burn_view* is on the canonical Bitcoin fork - it cannot distinguish between two different Stacks blocks that share the same tenure consensus hash and burn_view (e.g., two blocks the miner produced at the same chain height, one accepted by some signers/nodes and one orphaned). The SQL query that gathers candidates explicitly notes ties are possible: "If there are ties at a given burn view, they will both be returned." [3](#0-2) 

Because the final accept/reject decision in `check_block_builds_on_highest_block_in_tenure` reduces to a height comparison, a proposal whose `parent_block_id` is the orphaned/non-canonical sibling at that same height is accepted equally well as one built on the canonical sibling - the code has no way to reject "some known parent at the right height" versus "the canonical parent at the right height."

Attack flow: the attacker (a single miner slot holder) equivocates within its own tenure, producing two mutually exclusive blocks C (accepted/canonical on most of the network) and C′ (orphaned, but independently stored on some node/signer because it was individually valid and gossiped before the fork resolved). The attacker (or the same miner in the next tenure) then submits a `BlockProposal` whose `parent_block_id` is C′ to a node/signer that has C′ stored locally. `validate()` finds the parent header for C′ via `NakamotoChainState::get_block_header` (stackslib/src/net/api/postblock_proposal.rs:577-585), then calls `check_block_has_valid_parent`, which only verifies that C′'s height matches the "highest" height in that tenure - true, since C and C′ tie in height - and never verifies that C′ is the same block as the tip the rest of the network converged on.

### Impact Explanation
This breaks the canonicity/uniqueness safety property: a signer can be induced to sign a block built on a non-canonical parent. If several signers are independently in this stale/forked local-state condition (each storing the orphaned sibling instead of the canonical one), their signatures on the offshoot chain can accumulate, enabling a genuine chain split - a Critical-severity chain-safety violation as scoped by the audit rules.

### Likelihood Explanation
Preconditions: the node/signer must have both C and C′ (or just C′) already stored in its chainstate at the same height/tenure, which can arise naturally from network partition, reorg timing, or a miner equivocating within a single tenure and gossiping different blocks to different peers before consensus resolves. The attacker needs only their own miner slot (to produce the equivocating pair) and normal gossip - no majority of signers, no privileged access, and no auth_token. This is a plausible and repeatable scenario during any tenure boundary/reorg window, though it depends on timing (the signer must not yet have discovered/reconciled the canonical block before validating the proposal).

### Recommendation
In `check_block_builds_on_highest_block_in_tenure`, replace the height-only comparison with an identity check: compare `parent_header`'s block id (StacksBlockId, derived from consensus_hash + block hash) directly against `highest_header`'s block id, rejecting the proposal unless they match exactly. Additionally, `get_highest_canonical_block_header_from_candidates` should be hardened to break ties among same-height/same-burn_view candidates using the chainstate's own canonical-tip determination (e.g., verifying inclusion of the candidate in the MARF-indexed canonical chain), not merely burn_view/consensus_hash sortition membership.

### Proof of Concept
```rust
// stackslib/src/net/api/postblock_proposal.rs (test module)
#[test]
fn valid_parent_check_rejects_non_canonical_sibling_at_same_height() {
    // 1. Seed chainstate with tenure T containing block C (canonical) and
    //    block C' (a sibling at the same chain_length, orphaned by reorg
    //    or produced via miner equivocation), both persisted in
    //    nakamoto_block_headers with the same consensus_hash/burn_view.
    // 2. Ensure sortdb's canonical sortition tip reflects C as canonical.
    // 3. Build a child block `block_next` whose header.parent_block_id == C'.block_id()
    //    (NOT C's block id), same chain_length + 1.
    // 4. Call NakamotoBlockProposal::check_block_has_valid_parent(&chainstate, &sortdb, &block_next).
    //
    // EXPECTED (post-fix): Err(BlockValidateRejectReason { reason_code: InvalidParentBlock, .. })
    //   because C' != highest_header (identity check fails even though heights tie).
    //
    // CURRENT BUGGY BEHAVIOR: Ok(()) is returned, because only
    //   parent_header.height() == highest_header.height() is checked.
    //
    // 5. Drive full validate() and assert the resulting BlockValidateOk is never
    //    recorded as Valid in SignerDb (signer_db.block_lookup(signer_signature_hash)
    //    must not show BlockState::Accepted/GloballyAccepted for block_next).
}
```

### Citations

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

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L3229-3286)
```rust
    /// Get the first canonical block header in a vector of height-ordered candidates
    fn get_highest_canonical_block_header_from_candidates(
        sort_db: &SortitionDB,
        candidates: Vec<StacksHeaderInfo>,
    ) -> Result<Option<StacksHeaderInfo>, ChainstateError> {
        let canonical_sortition_handle = sort_db.index_handle_at_tip();
        for candidate in candidates.into_iter() {
            // if burn_view is None, then this is an epoch 2.x header, and since epoch 2.x tenure's correspond
            // to a single stacks block, we can use the miner's tenure sortition as a proxy for canonicity.
            let candidate_ch = candidate
                .burn_view
                .as_ref()
                .unwrap_or(&candidate.consensus_hash);
            let in_canonical_fork = canonical_sortition_handle.processed_block(candidate_ch)?;
            if in_canonical_fork {
                return Ok(Some(candidate));
            }
        }

        // did not find any blocks in candidates
        Ok(None)
    }

    /// Get the highest block in the given tenure on a given fork.
    /// Only works on Nakamoto blocks.
    /// TODO: unit test
    pub fn get_highest_block_header_in_tenure<SDBI: StacksDBIndexed>(
        chainstate_conn: &mut SDBI,
        tip_block_id: &StacksBlockId,
        consensus_hash: &ConsensusHash,
    ) -> Result<Option<StacksHeaderInfo>, ChainstateError> {
        let Some(block_id) =
            chainstate_conn.get_highest_block_id_in_tenure(tip_block_id, consensus_hash)?
        else {
            return Ok(None);
        };
        Self::get_block_header_nakamoto(chainstate_conn.sqlite(), &block_id)
    }

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

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L3288-3322)
```rust
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
