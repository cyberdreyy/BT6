### Title
`check_block_has_valid_tenure` checks canonicity relative to an attacker-influenced, potentially stale `sort_tip` instead of the node's true canonical burnchain tip - (File: `stackslib/src/net/api/postblock_proposal.rs`)

### Summary
In `NakamotoBlockProposal::validate`, the `db_handle` used to decide whether a proposed block's tenure is "on the canonical Bitcoin fork" is opened at `sort_tip.sortition_id`, where `sort_tip` is obtained by an un-canonicity-checked lookup of the block's own claimed burn view (`SortitionDB::get_block_snapshot_consensus`), rather than the node's true canonical tip (`SortitionDB::get_canonical_burn_chain_tip`, as used correctly in `NakamotoChainState::validate_nakamoto_tenure_snapshot`). This lets a miner who won a sortition on a fork that is later orphaned by a Bitcoin reorg construct a self-consistent, but non-canonical, `consensus_hash` / `burn_view_consensus_hash` pair that satisfies `check_block_has_valid_tenure`'s ancestor check.

### Finding Description
The claimed equality "signer approves the canonical tenure == attacker's claimed tenure" is broken because canonicity is evaluated relative to a value the attacker (indirectly) controls.

Path in `stackslib/src/net/api/postblock_proposal.rs`:
```
let burn_view_consensus_hash =
    NakamotoChainState::get_block_burn_view(sortdb, &self.block, &parent_stacks_header)?;
let sort_tip =
    SortitionDB::get_block_snapshot_consensus(sortdb.conn(), &burn_view_consensus_hash)?...
let db_handle = sortdb.index_handle(&sort_tip.sortition_id);
Self::check_block_has_valid_tenure(&db_handle, &self.block.header.consensus_hash)?;
``` [1](#0-0) 

`get_block_burn_view` for a tenure-change block simply takes `tenure_change.burn_view_consensus_hash` from the block payload (fully attacker-authored) and only checks that it "descends from the parent," using `index_handle_at_ch` opened on that same claimed burn view — a self-referential check, not a check against the live canonical tip. [2](#0-1) 

`SortitionDB::get_block_snapshot_consensus` performs a raw lookup by consensus hash across the sortition DB, which retains non-canonical/orphaned sortition history after a Bitcoin reorg; it does not filter for canonicity. [3](#0-2) 

`check_block_has_valid_tenure` then only asks whether `tenure_id` is an ancestor as seen from `db_handle` (anchored at `sort_tip.sortition_id`, i.e., the attacker's claimed burn view) — not whether that whole lineage is the chain the node currently recognizes as canonical:
```
pub(crate) fn check_block_has_valid_tenure(
    db_handle: &SortitionHandleConn,
    tenure_id: &ConsensusHash,
) -> Result<(), BlockValidateRejectReason> {
    if !db_handle.has_consensus_hash(tenure_id)? { ... NonCanonicalTenure ... }
    Ok(())
}
``` [4](#0-3) 

Contrast this with the pattern used for actual block acceptance/append in `NakamotoChainState::validate_nakamoto_tenure_snapshot`, which explicitly fetches the live canonical tip and checks ancestry against it:
```
let sort_tip = SortitionDB::get_canonical_burn_chain_tip(db_handle)?;
...
let ancestor_sort_id = get_ancestor_sort_id(db_handle, tenure_burn_chain_tip.block_height, &sort_tip.sortition_id)?
if ancestor_sort_id != tenure_burn_chain_tip.sortition_id { /* not canonical */ }
``` [5](#0-4) 

`postblock_proposal.rs::validate` never performs this true-canonical-tip comparison; it substitutes the attacker-influenced `sort_tip` for it.

Exploit sketch: the attacker wins a sortition (single miner slot) on a Bitcoin fork branch B that is later orphaned in favor of canonical branch A (natural or attacker-nudged short reorg). Before/around this reorg, an honest Stacks tenure/block under branch B may already be stored in the node's chainstate (`parent_block_id` resolvable via `NakamotoChainState::get_block_header`). After the reorg, the attacker crafts a `NakamotoBlockProposal` whose `header.consensus_hash` is the branch-B sortition consensus hash and whose `TenureChangePayload.burn_view_consensus_hash` also points into branch B (still present as a stale row in the sortition DB). `get_block_snapshot_consensus` finds this stale snapshot, `db_handle` is opened on it, and `has_consensus_hash` succeeds because the ancestry is internally consistent within branch B — even though branch B is not the node's current canonical fork. `check_block_has_valid_parent` and `get_expected_burns`/`validate_normal_nakamoto_block_burnchain` are also evaluated against this same non-canonical `db_handle`, so they do not catch the discrepancy either. The signer receives `BlockValidateOk` for a block whose tenure is not canonical.

I was not able to directly inspect the body of `SortitionHandleConn::has_consensus_hash` within the available context (only its call site and signature were confirmed); the analysis above rests on the documented contrast between this function's chain-tip-relative-to-`db_handle` semantics and the explicit `get_canonical_burn_chain_tip`-based check used elsewhere, and should be confirmed by reading `has_consensus_hash`'s implementation directly in `stackslib/src/chainstate/burn/db/sortdb.rs`.

### Impact Explanation
If confirmed, this breaks canonicity (chain safety): a signer could sign a block whose tenure is provably not on the chain the node's own sortition DB currently recognizes as canonical, satisfying the Critical impact category ("a signer signing an invalid/non-canonical block"). It requires only a single miner slot plus normal gossip of the resulting signed block/proposal — no majority of signers, no auth token, no local access. It would be repeatable any time a short Bitcoin reorg orphans a sortition the attacker won, as long as the attacker's earlier tenure/blocks remain resolvable in the node's local chainstate DB and the stale sortition snapshot remains queryable via `get_block_snapshot_consensus`.

### Likelihood Explanation
Requires: (1) attacker wins a sortition (achievable with their own BTC, single slot), (2) a subsequent Bitcoin reorg orphans that sortition while the node has already stored the attacker's prior tenure/block(s) locally, (3) the stale sortition snapshot is still present in `SortitionDB` (true for a normal, non-pruned DB after ordinary reorg handling), and (4) the attacker crafts a self-consistent `consensus_hash`/`burn_view_consensus_hash` pair on the orphaned branch. Preconditions 1–3 are realistic in any chain with occasional short Bitcoin reorgs; step 4 is fully within the attacker's control as the block/proposal author.

### Recommendation
In `NakamotoBlockProposal::validate` (`stackslib/src/net/api/postblock_proposal.rs`), replace the ancestry check in `check_block_has_valid_tenure` with one anchored at the node's true canonical burnchain tip, mirroring `NakamotoChainState::validate_nakamoto_tenure_snapshot`: call `SortitionDB::get_canonical_burn_chain_tip` and verify that `block.header.consensus_hash`'s snapshot is an ancestor of that live tip (via `get_ancestor_sort_id`), rather than relying on `db_handle.has_consensus_hash` anchored at the attacker-suppliable `sort_tip`/`burn_view_consensus_hash`. Additionally validate that `burn_view_consensus_hash` itself resolves to the canonical fork before using it to open `db_handle`.

### Proof of Concept
Rust test plan (integration-style, in `stackslib`/`stacks-node` test harness akin to `nakamoto_integrations.rs`'s proposal API tests):
1. Seed `SortitionDB` with two competing Bitcoin forks A (to become canonical) and B (to become orphaned), each with a winning sortition for the attacker's key, and mine/append a Nakamoto tenure-start block for branch B into `chainstate` before the reorg (so `get_block_header(parent_block_id)` succeeds later).
2. Trigger the reorg so branch A becomes canonical (`SortitionDB::get_canonical_burn_chain_tip` now returns A's tip); confirm branch B's snapshot rows still exist in the DB.
3. Construct `NakamotoBlockProposal` with `block.header.consensus_hash` = branch B's sortition consensus hash, `parent_block_id` = the previously stored branch-B block, and a `TenureChangePayload.burn_view_consensus_hash` also on branch B.
4. Call `validate()` directly (or via the HTTP endpoint) and assert:
   - Before fix: `validate()` returns `Ok(BlockValidateOk)` (bug reproduced) — assert this is NOT the case if the fix is applied.
   - After fix: assert `Err(BlockValidateRejectReason { reason_code: ValidateRejectCode::NonCanonicalTenure, .. })` is always returned once branch A is recognized canonical, matching the existing `"Non-canonical or absent tenure"` test case pattern already present in `nakamoto_integrations.rs`. [6](#0-5) 
5. Assert the signer's state machine (`stacks-signer`) never emits a `BlockResponse::Accepted`/signature for this proposal, by driving `SortitionsView::check_proposal` / the v0/v1 chainstate checks with the same crafted block and asserting a `RejectReason` (e.g. via the SortitionsView's own tenure/parent checks) is produced independent of the node-side validation result.

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

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2639-2647)
```rust
        let burn_view_sn =
            SortitionDB::get_block_snapshot_consensus(sort_handle.sqlite(), burn_view_ch)?
                .ok_or_else(|| {
                    warn!("Could not load expected burns -- no such burn view";
                          "burn_view_consensus_hash" => %burn_view_ch
                    );
                    ChainstateError::NoSuchBlockError
                })?;
        Ok(Some(burn_view_sn.total_burn))
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

**File:** stacks-node/src/tests/nakamoto_integrations.rs (L3542-3551)
```rust
        (
            "Non-canonical or absent tenure",
            {
                let mut sp = sign(&proposal);
                sp.block.header.consensus_hash.0[3] ^= 0x07;
                sp
            },
            HTTP_ACCEPTED,
            Some(Err(ValidateRejectCode::NonCanonicalTenure)),
        ),
```
