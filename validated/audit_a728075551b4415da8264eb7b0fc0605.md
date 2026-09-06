### Title
Signer's `postblock_proposal::validate` accepts a `TenureChange::Extend`'s self-declared `burn_view_consensus_hash` without checking it is the actual canonical burnchain tip - ([File: stackslib/src/net/api/postblock_proposal.rs])

### Summary
`NakamotoBlockProposal::validate` derives the block's burn view via `NakamotoChainState::get_block_burn_view`, which for an `Extend`-cause `TenureChange` simply trusts the miner-supplied `burn_view_consensus_hash` field. The subsequent tenure check (`check_block_has_valid_tenure` → `SortitionHandleConn::has_consensus_hash`) validates ancestry only *within the fork rooted at that attacker-chosen snapshot*, never comparing it against `SortitionDB::get_canonical_burn_chain_tip`. This differs from the node's own append-time check (`validate_nakamoto_tenure_snapshot`), which explicitly re-derives the canonical tip and requires ancestor connectivity to it.

### Finding Description
In `stackslib/src/net/api/postblock_proposal.rs::validate`: [1](#0-0) 

`burn_view_consensus_hash` is obtained from `NakamotoChainState::get_block_burn_view`, which for a block carrying a `TenureChange` simply returns `tenure_change.burn_view_consensus_hash` (attacker-controlled) after only checking that it "descends from" the parent's own burn view — a fork-connectivity check, not a canonicity check: [2](#0-1) 

`sort_tip` is then looked up via `SortitionDB::get_block_snapshot_consensus`, which returns *any* snapshot matching that consensus hash regardless of whether it is on the currently-canonical Bitcoin fork (no `pox_valid`/canonical filtering). `check_block_has_valid_tenure` then opens a `SortitionHandleConn` rooted at that very snapshot's `sortition_id` and calls `has_consensus_hash`: [3](#0-2) [4](#0-3) 

`has_consensus_hash` computes `get_ancestor_sort_id(self, sn.block_height, &self.context.chain_tip)` where `self.context.chain_tip` **is** `sort_tip.sortition_id` — i.e., the very snapshot the attacker chose. The check is thus self-referential: it verifies the block's `consensus_hash` is an ancestor of the attacker-chosen fork tip, not that the fork tip itself is canonical right now. Contrast this with the chainstate's own append-path tenure check, `validate_nakamoto_tenure_snapshot`, which explicitly fetches `SortitionDB::get_canonical_burn_chain_tip` and requires the tenure snapshot's ancestor-at-height to match that *real* tip's `sortition_id`: [5](#0-4) 

This canonical-tip cross-check is absent from `postblock_proposal.rs::validate`. As long as an orphaned sortition (e.g., left over from a recently-resolved short Bitcoin fork) still has a `snapshots` row whose ancestry at the parent's burn-view height matches the parent's real burn view — which is trivially true since forks share a common ancestor before diverging — a miner can set an `Extend`-cause `TenureChangePayload.burn_view_consensus_hash` to that orphaned hash. `get_block_burn_view`'s connectivity check passes (the orphaned fork descends from the same real ancestor), `get_block_snapshot_consensus` finds the orphaned but present snapshot, and `has_consensus_hash` passes because it only checks ancestry within that same orphaned fork. `validate()` proceeds to `BlockValidateOk`.

### Impact Explanation
This breaks the canonicity safety property that `BlockValidateOk` is supposed to certify: the signer is told a block's tenure/burn-view context is valid, but the burn view it self-declares may correspond to a non-canonical, orphaned Bitcoin fork rather than the current canonical tip. Per `docs/signer-flows.md`, adoption-as-ground-truth is scoped only to `NewBlock`/`check_latest_block_in_tenure`, not to this burn-view derivation — so nothing else in the signer's flow is documented to independently re-verify burn-view canonicity before signing. A signature obtained this way is a signature over a block whose burn-chain context is attacker-chosen and possibly non-canonical, matching the "Critical: signer signing a non-canonical/conflicting block" category.

### Likelihood Explanation
The attacker needs only one won miner slot (to be the active miner authorized to produce an `Extend` tenure-change) plus a recently-orphaned-but-still-recorded sortition row in the target signer's sortdb — a state that can arise naturally from any short-lived Bitcoin reorg, which is not attacker-privileged and requires no majority signer collusion. Given such a precondition window, the exploit is repeatable for every `Extend` tenure-change the attacker's miner produces during that window.

### Recommendation
In `NakamotoChainState::get_block_burn_view` (or in `postblock_proposal::validate` immediately after computing `burn_view_consensus_hash`/`sort_tip`), add an explicit canonicity check analogous to `validate_nakamoto_tenure_snapshot`: fetch `SortitionDB::get_canonical_burn_chain_tip`, and require that `get_ancestor_sort_id(db_handle, sort_tip.block_height, &canonical_tip.sortition_id) == Some(sort_tip.sortition_id)` before accepting the burn view as valid, rejecting the proposal otherwise.

### Proof of Concept
```rust
// stackslib/src/net/api/tests/postblock_proposal.rs
#[test]
fn reject_extend_tenure_change_with_orphaned_burn_view() {
    // 1. Build a sortdb/chainstate with a canonical tip T.
    // 2. Fork the burnchain: mine an alternate sortition A off T's parent that
    //    later loses (T's competing sibling wins by height/hash tie-break),
    //    leaving A's snapshot row present but non-canonical.
    let (sortdb, chainstate, canonical_tip, orphaned_sn) = /* build fork scenario */;

    // 3. Craft an Extend TenureChangePayload whose burn_view_consensus_hash == orphaned_sn.consensus_hash,
    //    connecting properly to the parent block's real burn view (common ancestor before the fork).
    let tenure_change = make_tenure_change_tx(TenureChangePayload {
        cause: TenureChangeCause::Extended,
        burn_view_consensus_hash: orphaned_sn.consensus_hash.clone(),
        ..parent_payload
    });
    let proposal_block = build_block_with_tenure_change(tenure_change, &parent_header);

    let proposal = NakamotoBlockProposal { block: proposal_block, /* .. */ };
    let result = proposal.validate(&sortdb, &mut chainstate, /* timeouts */);

    // EQUALITY UNDER TEST:
    // orphaned_sn.sortition_id (self-declared burn view) != canonical_tip's ancestor at orphaned_sn.block_height
    assert_ne!(
        orphaned_sn.sortition_id,
        SortitionDB::get_ancestor_snapshot(&sortdb.index_conn(), orphaned_sn.block_height, &canonical_tip.sortition_id)
            .unwrap().unwrap().sortition_id
    );

    // Current (buggy) behavior: validate() still returns Ok(..) because
    // check_block_has_valid_tenure only checks ancestry within the orphaned fork.
    // Fixed behavior: validate() must return Err(BlockValidateRejectReason { reason_code: NonCanonicalTenure, .. }).
    assert!(result.is_err(), "validate() must reject a burn view that is not on the canonical fork");
}
```

### Citations

**File:** stackslib/src/net/api/postblock_proposal.rs (L452-471)
```rust
    /// Verify that the block we received builds upon a valid tenure.
    /// Implemented as a static function to facilitate testing.
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
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L587-602)
```rust
        let burn_view_consensus_hash =
            NakamotoChainState::get_block_burn_view(sortdb, &self.block, &parent_stacks_header)?;
        let sort_tip =
            SortitionDB::get_block_snapshot_consensus(sortdb.conn(), &burn_view_consensus_hash)?
                .ok_or_else(|| BlockValidateRejectReason {
                    reason_code: ValidateRejectCode::NoSuchTenure,
                    reason: "Failed to find sortition for block tenure".to_string(),
                    failed_txid: None,
                })?;

        let burn_dbconn: SortitionHandleConn = sortdb.index_handle(&sort_tip.sortition_id);
        let db_handle = sortdb.index_handle(&sort_tip.sortition_id);

        // (For the signer)
        // Verify that the block's tenure is on the canonical sortition history
        Self::check_block_has_valid_tenure(&db_handle, &self.block.header.consensus_hash)?;
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2206-2248)
```rust
        let burnchain_view = if let Some(tenure_change) = next_ready_block.get_tenure_tx_payload() {
            if let Some(ref parent_burn_view) = parent_header_info.burn_view {
                // check that the tenure_change's burn view descends from the parent
                let parent_burn_view_sn = SortitionDB::get_block_snapshot_consensus(
                    sort_db.conn(),
                    parent_burn_view,
                )?
                .ok_or_else(|| {
                    warn!(
                        "Cannot process Nakamoto block: could not find parent block's burnchain view";
                        "consensus_hash" => %next_ready_block.header.consensus_hash,
                        "stacks_block_hash" => %next_ready_block.header.block_hash(),
                        "stacks_block_id" => %next_ready_block.header.block_id(),
                        "parent_block_id" => %next_ready_block.header.parent_block_id
                    );
                    ChainstateError::InvalidStacksBlock("Failed to load burn view of parent block ID".into())
                })?;
                let handle = sort_db.index_handle_at_ch(&tenure_change.burn_view_consensus_hash)?;
                let connected_sort_id = get_ancestor_sort_id(&handle, parent_burn_view_sn.block_height, &handle.context.chain_tip)?
                    .ok_or_else(|| {
                        warn!(
                            "Cannot process Nakamoto block: could not find parent block's burnchain view";
                            "consensus_hash" => %next_ready_block.header.consensus_hash,
                            "stacks_block_hash" => %next_ready_block.header.block_hash(),
                            "stacks_block_id" => %next_ready_block.header.block_id(),
                            "parent_block_id" => %next_ready_block.header.parent_block_id
                        );
                        ChainstateError::InvalidStacksBlock("Failed to load burn view of parent block ID".into())
                    })?;
                if connected_sort_id != parent_burn_view_sn.sortition_id {
                    warn!(
                        "Cannot process Nakamoto block: parent block's burnchain view does not connect to own burn view";
                        "consensus_hash" => %next_ready_block.header.consensus_hash,
                        "stacks_block_hash" => %next_ready_block.header.block_hash(),
                        "stacks_block_id" => %next_ready_block.header.block_id(),
                        "parent_block_id" => %next_ready_block.header.parent_block_id
                    );
                    return Err(ChainstateError::InvalidStacksBlock(
                        "Does not connect to burn view of parent block ID".into(),
                    ));
                }
            }
            tenure_change.burn_view_consensus_hash.clone()
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2654-2697)
```rust
    fn validate_nakamoto_tenure_snapshot(
        db_handle: &SortitionHandleConn,
        block: &NakamotoBlock,
    ) -> Result<BlockSnapshot, ChainstateError> {
        // find the sortition-winning block commit for this block, as well as the block snapshot
        // containing the parent block-commit.  This is the snapshot that corresponds to when the
        // miner begain its tenure; it may not be the burnchain tip.
        let consensus_hash = &block.header.consensus_hash;

        let sort_tip = SortitionDB::get_canonical_burn_chain_tip(db_handle)?;

        // burn chain tip that selected this commit's block (the tenure sortition)
        let Some(tenure_burn_chain_tip) =
            SortitionDB::get_block_snapshot_consensus(db_handle, consensus_hash)?
        else {
            warn!("No sortition for {}", consensus_hash);
            return Err(ChainstateError::InvalidStacksBlock(
                "No sortition for block's consensus hash".into(),
            ));
        };

        // tenure sortition is canonical
        let Some(ancestor_sort_id) = get_ancestor_sort_id(
            db_handle,
            tenure_burn_chain_tip.block_height,
            &sort_tip.sortition_id,
        )?
        else {
            // not canonical
            warn!("Invalid consensus hash: snapshot is not canonical"; "consensus_hash" => %consensus_hash);
            return Err(ChainstateError::InvalidStacksBlock(
                "No sortition for block's consensus hash -- not canonical".into(),
            ));
        };
        if ancestor_sort_id != tenure_burn_chain_tip.sortition_id {
            // not canonical
            warn!("Invalid consensus hash: snapshot is not canonical"; "consensus_hash" => %consensus_hash);
            return Err(ChainstateError::InvalidStacksBlock(
                "No sortition for block's consensus hash -- not canonical".into(),
            ));
        };

        Ok(tenure_burn_chain_tip)
    }
```

**File:** stackslib/src/chainstate/burn/db/sortdb.rs (L2657-2676)
```rust
    /// Is a consensus hash's sortition valid on the fork represented by this handle?
    /// Return Ok(true) if so
    /// Return Ok(false) if not (including if there is no sortition with this consensus hash)
    /// Return Err(..) on DB error
    pub fn has_consensus_hash(&self, consensus_hash: &ConsensusHash) -> Result<bool, db_error> {
        let Some(sn) = SortitionDB::get_block_snapshot_consensus(self, consensus_hash)? else {
            // no sortition with this consensus hash
            return Ok(false);
        };

        let Some(expected_sortition_id) =
            get_ancestor_sort_id(self, sn.block_height, &self.context.chain_tip)?
        else {
            // no ancestor at this sortition height relative to this chain tip
            // (e.g. perhaps this consensus hash is in the "future" relative to this chain tip)
            return Ok(false);
        };

        Ok(sn.sortition_id == expected_sortition_id)
    }
```
