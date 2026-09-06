### Title
Signer block validation anchors canonicity checks to an attacker-controlled, possibly non-canonical burn view instead of the true canonical Bitcoin tip - ([File: stackslib/src/net/api/postblock_proposal.rs])

### Summary
`NakamotoBlockProposal::validate` derives its sortition "tip" (`sort_tip`) from `NakamotoChainState::get_block_burn_view`, which for a `TenureChange::Extend` simply trusts the miner-supplied `burn_view_consensus_hash` as long as it is a self-consistent descendant of the parent block's own burn view. `check_block_has_valid_tenure` then checks canonicity of the block's tenure relative to that attacker-influenced `sort_tip`, not relative to the SortitionDB's actual canonical Bitcoin tip.

### Finding Description
The equality that must hold is: *the tenure the signer approves must be canonical under the current Bitcoin tip*. Instead, the code checks canonicity relative to whatever sortition the `TenureChange`'s `burn_view_consensus_hash` resolves to.

In `validate()`:
```
let burn_view_consensus_hash =
    NakamotoChainState::get_block_burn_view(sortdb, &self.block, &parent_stacks_header)?;
let sort_tip =
    SortitionDB::get_block_snapshot_consensus(sortdb.conn(), &burn_view_consensus_hash)?...;
let db_handle = sortdb.index_handle(&sort_tip.sortition_id);
Self::check_block_has_valid_tenure(&db_handle, &self.block.header.consensus_hash)?;
``` [1](#0-0) 

`get_block_burn_view` only checks that the claimed `burn_view_consensus_hash` (for `Extend`) descends from the *parent block's own recorded burn view* — it never compares against `SortitionDB::get_canonical_burn_chain_tip`:
```
let handle = sort_db.index_handle_at_ch(&tenure_change.burn_view_consensus_hash)?;
let connected_sort_id = get_ancestor_sort_id(&handle, parent_burn_view_sn.block_height, &handle.context.chain_tip)?...
if connected_sort_id != parent_burn_view_sn.sortition_id { ... error ... }
tenure_change.burn_view_consensus_hash.clone()
``` [2](#0-1) 

`SortitionDB::get_block_snapshot_consensus` performs a lookup by consensus hash across all known sortition rows, canonical or not (it is used elsewhere purely to fetch a historical snapshot before a separate canonicity check is performed, e.g. in `check_nakamoto_tenure`/`check_valid_consensus_hash`) [3](#0-2) . `check_block_has_valid_tenure` then only calls `db_handle.has_consensus_hash(tenure_id)` where `db_handle = sortdb.index_handle(&sort_tip.sortition_id)` — i.e. the handle's ancestry root is `sort_tip`, the attacker-supplied stale snapshot, not the node's canonical burn chain tip:
```
pub(crate) fn check_block_has_valid_tenure(
    db_handle: &SortitionHandleConn,
    tenure_id: &ConsensusHash,
) -> Result<(), BlockValidateRejectReason> {
    if !db_handle.has_consensus_hash(tenure_id)? { ... NonCanonicalTenure ... }
    Ok(())
}
``` [4](#0-3) 

Contrast this with the actual consensus-critical block-append path, `validate_nakamoto_tenure_snapshot`, which explicitly anchors to `SortitionDB::get_canonical_burn_chain_tip` before checking ancestry:
```
let sort_tip = SortitionDB::get_canonical_burn_chain_tip(db_handle)?;
...
let Some(ancestor_sort_id) = get_ancestor_sort_id(db_handle, tenure_burn_chain_tip.block_height, &sort_tip.sortition_id)? ...
``` [5](#0-4) 

The signer-facing `validate()` path never performs this canonical-tip anchoring; it substitutes the miner-supplied burn view for the canonical tip, and the comment in the code even labels these checks explicitly "(For the signer)" — implying they are meant to substitute for chainstate-level canonicity checks but do not enforce the same guarantee.

Exploit flow: an attacker who has won one legitimate sortition/tenure on a Bitcoin fork that later gets reorged away (its sortition rows remain in the node's sortdb, unpruned) can, after the reorg, submit a `BlockProposal` containing a `TenureChange::Extend` whose `burn_view_consensus_hash` points to a later sortition on that same now-orphaned fork. Because `get_block_burn_view`'s only check is "does the claimed view descend from the parent's own view" — both of which live in the same orphaned fork — the check passes. `validate()` then opens `db_handle` rooted at that orphaned sortition and confirms the block's own tenure consensus hash is an ancestor of it, which trivially succeeds, yielding `BlockValidateOk` for a tenure that is not canonical under the current Bitcoin tip.

### Impact Explanation
This breaks the canonicity safety property: a signer that trusts `BlockValidateOk` from this endpoint can sign a block belonging to a tenure that is not part of the currently-canonical Bitcoin fork. If enough signer weight is fooled the same way (each independently re-deriving the same orphaned-but-still-known view), a fork block could accumulate valid signatures — a chain-safety violation matching the "Critical: signer signing an invalid/non-canonical block" category.

### Likelihood Explanation
Preconditions: a Bitcoin reorg must occur, the node must still retain the orphaned sortition rows (typical, since Stacks sortition history is not pruned on reorg — only the canonical pointer changes), and the attacker's own tenure must be rooted in that orphaned fork. Attacker cost is exactly one miner slot (to legitimately obtain a tenure and craft `TenureChange` transactions) plus ordinary gossip of a `BlockProposal` — no majority-signer collusion, no node operator/local access, and no auth token needed. The scenario is repeatable any time a live reorg leaves old sortition rows queryable.

### Recommendation
In `NakamotoBlockProposal::validate` (and in `get_block_burn_view`), require that the resolved `sort_tip`/`burn_view_consensus_hash` be an ancestor of (or equal to) `SortitionDB::get_canonical_burn_chain_tip`, not merely a self-consistent continuation of the parent's recorded burn view. Concretely, after computing `sort_tip`, fetch the true canonical tip and call `get_ancestor_sort_id` from the canonical tip down to `sort_tip.block_height`, rejecting the proposal with `ValidateRejectCode::NonCanonicalTenure` if the ancestor's `sortition_id` does not match `sort_tip.sortition_id`.

### Proof of Concept
Rust test plan (stackslib, `postblock_proposal.rs` test module):
1. Build a two-peer test harness; mine a tenure T1 for the attacker miner at Bitcoin height H, and continue several more sortitions on that fork (T1..T3), keeping their sortition rows.
2. Force a Bitcoin reorg at height H+1 onto a different, now-canonical fork (T1'..T3'), and process it via `SortitionDB`, so `SortitionDB::get_canonical_burn_chain_tip` now returns a tip in the new fork.
3. Confirm the old T2/T3 sortition rows are still queryable via `SortitionDB::get_block_snapshot_consensus`.
4. Construct a `NakamotoBlockProposal` whose block contains a `TenureChange::Extend` with `burn_view_consensus_hash = T3.consensus_hash` (from the orphaned fork) and a parent block whose recorded burn view is `T2.consensus_hash` (also orphaned).
5. Call `NakamotoChainState::get_block_burn_view` and assert it returns `Ok(T3.consensus_hash)` (showing the internal-consistency check alone is satisfied).
6. Call `proposal.validate(...)` and assert it currently returns `Ok(BlockValidateOk)` (demonstrating the bug) — after the fix, assert it must return `Err(BlockValidateRejectReason { reason_code: ValidateRejectCode::NonCanonicalTenure, .. })` by re-deriving canonicity against `SortitionDB::get_canonical_burn_chain_tip`.

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

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2663-2687)
```rust
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
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L677-689)
```rust
        // all consensus hashes must be on the canonical burnchain fork, if they're not the first-ever
        let Some(tenure_sn) =
            Self::check_valid_consensus_hash(sort_handle, &tenure_payload.tenure_consensus_hash)?
        else {
            return Ok(None);
        };
        let Some(sortition_sn) = Self::check_valid_consensus_hash(
            sort_handle,
            &tenure_payload.burn_view_consensus_hash,
        )?
        else {
            return Ok(None);
        };
```
