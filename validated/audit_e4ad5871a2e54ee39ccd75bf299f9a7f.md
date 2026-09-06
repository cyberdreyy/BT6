### Title
Unbounded reorg-depth pagination in `check_parent_tenure_choice` lets a single miner stall the signer's event loop with an attacker-chosen-depth tenure walk - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary

### Finding Description
When a miner's tenure-change block does not build off the prior sortition, the signer must decide whether the implied reorg is legitimate. `SortitionData::check_parent_tenure_choice` fetches the full list of tenures between the miner's claimed parent tenure and the last sortition via `client.get_tenure_forking_info(&self.parent_tenure_id, &self.prior_sortition)`, then iterates over every returned tenure, issuing a DB query per tenure (`get_globally_accepted_block_count_in_tenure`, `get_first_approved_block_in_tenure`) to decide whether it may be superseded. [1](#0-0) 

The size of that walk is controlled by how far back the miner's chosen `parent_tenure_id` is from the actual chain tip (`prior_sortition`) - a value the miner freely picks by pointing the block's parent at any real, historical block, since parent-block validity is checked independently on the node and does not bound reorg depth.

On the client side, `StacksClient::get_tenure_forking_info` paginates the walk with no upper bound of its own: it keeps calling `get_tenure_forking_info_step` and extending a `VecDeque` until the cursor reaches `chosen_parent`. [2](#0-1) 

On the node side, each single HTTP call is capped by `DEPTH_LIMIT = 10` sortitions, but the client simply issues however many sequential round trips are needed to cover the full requested depth: [3](#0-2) [4](#0-3) 

So the total work (number of HTTP round trips, size of the in-memory tenure list, and number of per-tenure `SignerDb` queries) is `O(depth)` where `depth` is the burn-height distance between the miner's claimed parent tenure and the actual prior sortition - a quantity the miner controls by simply crafting a tenure-change block whose parent points arbitrarily far back in real chain history. This mirrors the reported bug class: a loop/pagination whose iteration count is derived from an externally/adversary-influenced index difference with no cap, so cost grows unboundedly with that difference (there, "epochIndex to tokenEpochs"; here, "prior_sortition to parent_tenure_id").

### Impact Explanation
The signer's event processing (`process_event` → `handle_event_match` → block-proposal validation path) is effectively single-threaded per signer instance. A `check_parent_tenure_choice` call spanning tens of thousands of sortitions (the whole chain's history is a real historical ceiling) forces that many sequential node round trips plus that many local DB lookups before the signer can render any verdict on the proposal, or move on to the next event. While this evaluation is in flight, the signer cannot progress on other proposals or state-machine housekeeping, so a miner can repeatedly submit tenure-change proposals with deep bogus parents to keep the signer busy walking history instead of evaluating legitimate proposals - a liveness wedge ("a signer wedged into never signing valid blocks") triggerable by a single miner with no cooperation from other signers.

### Likelihood Explanation
Triggering the expensive path only requires a miner to win one sortition slot and submit a tenure-change block whose declared parent tenure is a deep, legitimate historical tenure rather than the immediately preceding sortition - well within a single miner's unilateral control, and repeatable at each tenure the miner wins.

### Recommendation
Bound the total reorg depth `check_parent_tenure_choice`/`get_tenure_forking_info` is willing to walk (e.g. reject or fast-fail once depth exceeds a small, configurable maximum reorg window, similar in spirit to the existing `MAX_FORK_DEPTH` used elsewhere in the signer for burn-block record pruning), and enforce the same cap on the node's `/v3/tenures/fork_info` handler so a single request/response pair cannot be chained indefinitely by the client.

### Proof of Concept
1. A miner wins a sortition and builds a tenure-change block whose `prev_tenure_consensus_hash` legitimately resolves to a real tenure many thousands of sortitions behind the current tip (any real ancestor works; the node's parent-block checks in `postblock_proposal.rs` only require the referenced parent to exist, not that it be recent).
2. The signer's `validate_tenure_change_payload` (v1/v2) calls `check_parent_tenure_choice`, which calls `client.get_tenure_forking_info(parent_tenure_id, prior_sortition)`.
3. `get_tenure_forking_info` issues `depth / DEPTH_LIMIT` (`DEPTH_LIMIT = 10`) sequential HTTP requests to `/v3/tenures/fork_info`, accumulating the full list of `depth` tenures in memory.
4. `check_parent_tenure_choice` then iterates the full list, issuing two `SignerDb` queries per tenure, before returning a verdict - all within the signer's single event-processing pass, blocking it from handling subsequent events until the walk completes.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L190-212)
```rust
        let tenures_reorged =
            client.get_tenure_forking_info(&self.parent_tenure_id, &self.prior_sortition)?;
        if tenures_reorged.is_empty() {
            warn!("Miner is not building off of most recent tenure, but stacks node was unable to return information about the relevant sortitions. Marking miner invalid.");
            return Ok(false);
        }

        // this value *should* always be some, but try to do the best we can if it isn't
        let sortition_state_received_time =
            signer_db.get_burn_block_receive_time(&self.burn_block_hash)?;

        // Track which tenures are superseded by the reorg, then mark them in
        // the DB after the reorg is permitted.
        let mut superseded_tenures = Vec::new();
        for tenure in tenures_reorged.iter() {
            if tenure.consensus_hash == self.parent_tenure_id {
                // this was a built-upon tenure, no need to check this tenure as part of the reorg.
                continue;
            }

            // disallow reorg if more than one block has already been signed
            let globally_accepted_blocks =
                signer_db.get_globally_accepted_block_count_in_tenure(&tenure.consensus_hash)?;
```

**File:** stacks-signer/src/client/stacks_client.rs (L318-357)
```rust
    /// Get information about the tenures between `chosen_parent` and `last_sortition`
    pub fn get_tenure_forking_info(
        &self,
        chosen_parent: &ConsensusHash,
        last_sortition: &ConsensusHash,
    ) -> Result<Vec<TenureForkingInfo>, ClientError> {
        debug!("StacksClient: Getting tenure forking info";
            "chosen_parent" => %chosen_parent,
            "last_sortition" => %last_sortition,
        );
        let mut tenures: VecDeque<TenureForkingInfo> =
            self.get_tenure_forking_info_step(chosen_parent, last_sortition)?;
        if tenures.is_empty() {
            return Ok(vec![]);
        }
        while tenures.back().map(|x| &x.consensus_hash) != Some(chosen_parent) {
            let new_start = tenures.back().ok_or_else(|| {
                ClientError::InvalidResponse(
                    "Should have tenure data in forking info response".into(),
                )
            })?;
            let mut next_results =
                self.get_tenure_forking_info_step(chosen_parent, &new_start.consensus_hash)?;
            if next_results.pop_front().is_none() {
                return Err(ClientError::InvalidResponse(
                    "Could not fetch forking info all the way back to the requested chosen_parent"
                        .into(),
                ));
            }
            if next_results.is_empty() {
                return Err(ClientError::InvalidResponse(
                    "Could not fetch forking info all the way back to the requested chosen_parent"
                        .into(),
                ));
            }
            tenures.extend(next_results);
        }

        Ok(tenures.into_iter().collect())
    }
```

**File:** stackslib/src/net/api/get_tenures_fork_info.rs (L36-38)
```rust
pub static RPC_TENURE_FORKING_INFO_PATH: &str = "/v3/tenures/fork_info";

static DEPTH_LIMIT: usize = 10;
```

**File:** stackslib/src/net/api/get_tenures_fork_info.rs (L234-259)
```rust
            let mut depth = 0;
            while depth < DEPTH_LIMIT && cursor.consensus_hash != recurse_end {
                if height_bound >= cursor.block_height {
                    return Err(ChainError::NotInSameFork);
                }
                cursor =
                    SortitionDB::get_block_snapshot(sortdb.conn(), &cursor.parent_sortition_id)?
                        .ok_or_else(|| ChainError::NoSuchBlockError)?;
                if cursor.sortition
                    || chainstate
                        .nakamoto_blocks_db()
                        .is_shadow_tenure(&cursor.consensus_hash)?
                {
                    results.push(TenureForkingInfo::from_snapshot(
                        &cursor,
                        sortdb,
                        chainstate,
                        &network.stacks_tip.block_id(),
                    )?);
                }
                if cursor.sortition {
                    // don't count shadow blocks towards the depth, since there can be a large
                    // swath of them.
                    depth += 1;
                }
            }
```
