### Title
Signer block-validation RPC checks tenure canonicity against an attacker-chosen stale burn view instead of the SortitionDB's true canonical Bitcoin tip - (File: stackslib/src/net/api/postblock_proposal.rs)

### Summary
`BlockValidateRequest::validate` derives the `SortitionHandleConn` used for canonicity checks from `NakamotoChainState::get_block_burn_view`, which for a `TenureChange::Extend` returns whatever `burn_view_consensus_hash` the miner supplied, not the SortitionDB's live canonical tip. `check_block_has_valid_tenure` then calls `db_handle.has_consensus_hash(tenure_id)`, which evaluates ancestry relative to that attacker-chosen reference point rather than `SortitionDB::get_canonical_burn_chain_tip`.

### Finding Description
In `validate()` [1](#0-0) , the sortition handle used for all downstream canonicity/burn checks is built from `sort_tip`, which comes from `NakamotoChainState::get_block_burn_view` resolving the block's declared burn view (miner-controlled via `TenureChange`) rather than from `SortitionDB::get_canonical_burn_chain_tip`. `get_block_burn_view` [2](#0-1)  only checks that the miner-chosen `burn_view_consensus_hash` connects backward to the *already-established parent's* burn view at that specific height — it never checks that this consensus hash lies on the SortitionDB's presently canonical fork.

`check_block_has_valid_tenure` then calls `db_handle.has_consensus_hash(tenure_id)` [3](#0-2) , and `has_consensus_hash` computes ancestry strictly relative to `self.context.chain_tip` (i.e. the handle's fixed reference point, which is `sort_tip.sortition_id`) [4](#0-3) . Contrast this with the equivalent node-consensus check, `validate_nakamoto_tenure_snapshot`, which explicitly anchors ancestry to `SortitionDB::get_canonical_burn_chain_tip(db_handle)` [5](#0-4) . The RPC/signer validation path used by `postblock_proposal.rs` does not perform this same live-canonical-tip anchoring.

Because the SortitionDB retains historical (now-orphaned) sortition rows after a reorg, `SortitionDB::get_block_snapshot_consensus` will happily resolve an attacker-supplied, no-longer-canonical `burn_view_consensus_hash` from a pre-reorg fork tip, as long as it connects backward to the parent block's own burn view. If a shallow Bitcoin reorg occurs after a tenure-start sortition has already been accepted by the node/signer, an attacker (the sole miner of that tenure) can craft a `TenureChange::Extend` whose `burn_view_consensus_hash` points to a later, now-orphaned sortition from the pre-reorg fork instead of the real, currently canonical burn view. `check_block_has_valid_tenure`'s canonicity check will be performed relative to that orphaned reference frame and can pass even though the tenure and its extension no longer belong to the SortitionDB's actual canonical Bitcoin history.

### Impact Explanation
If exploited, `BlockValidateOk` is returned by the node's block-proposal validation RPC for a block whose tenure/burn view is rooted in a fork that the node's own SortitionDB has already determined is non-canonical. A signer that trusts this response could sign a block that is not canonical under the current Bitcoin tip — a chain-safety violation (Critical: signer signing a non-canonical/forked block).

### Likelihood Explanation
This requires a real (even shallow) Bitcoin reorg to occur after a tenure has started and while the tenure is being extended, plus the attacker controlling that tenure's mining slot and being able to submit a proposal whose `TenureChange::Extend.burn_view_consensus_hash` still connects structurally to the parent's already-accepted burn view but points into the now-orphaned continuation. This is a narrow timing window tied to burnchain-fork depth and node/signer state at the moment of validation, making it possible but not trivially/always reproducible; it also overlaps substantially with node-level chainstate/consensus reorg-handling behavior rather than being purely signer-side logic.

### Recommendation
In `postblock_proposal.rs`'s `validate()`, after resolving `sort_tip` from the block's declared burn view, additionally verify that `sort_tip.sortition_id` (or its consensus hash) is an ancestor of `SortitionDB::get_canonical_burn_chain_tip(sortdb.conn())` — mirroring the check already performed in `validate_nakamoto_tenure_snapshot` — before trusting `db_handle` for `check_block_has_valid_tenure` and subsequent checks.

### Proof of Concept
A Rust test in `stackslib` would: (1) advance a test peer/sortdb through Nakamoto epoch, start a tenure at height H with consensus hash CH_H; (2) mine an additional burn block to height H+1 with consensus hash CH_old, and have the tenure record a burn view at CH_old (still canonical at this point); (3) force a reorg at height H+1 replacing CH_old with CH_new (the new canonical tip), while retaining CH_old's sortition row in the DB; (4) submit a `NakamotoBlock` with a `TenureChange::Extend` carrying `tenure_consensus_hash = CH_H` and `burn_view_consensus_hash = CH_old`; (5) call `BlockValidateRequest::validate(...)` and assert it currently returns `Ok(BlockValidateOk)` (demonstrating the bug) instead of `Err(ValidateRejectCode::NonCanonicalTenure)`, then assert that after adding a canonical-tip ancestry check the same call returns `Err(ValidateRejectCode::NonCanonicalTenure)`.

### Citations

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

**File:** stackslib/src/net/api/postblock_proposal.rs (L587-598)
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
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2201-2248)
```rust
    pub fn get_block_burn_view(
        sort_db: &SortitionDB,
        next_ready_block: &NakamotoBlock,
        parent_header_info: &StacksHeaderInfo,
    ) -> Result<ConsensusHash, ChainstateError> {
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

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2654-2696)
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
