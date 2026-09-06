### Title
Miner-controlled deep-reorg parent tenure forces every signer into an unbounded, synchronous tenure-history walk during proposal evaluation - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`SortitionData::check_parent_tenure_choice` is invoked synchronously on every block-proposal evaluation (both `v1::SortitionsView::check_proposal` and the `v2` tenure-change validation path) whenever a proposal's committed parent tenure differs from the signer's locally tracked prior sortition. It calls the client-side `StacksClient::get_tenure_forking_info`, which repeatedly pages through tenure ancestry (10 tenures per HTTP round trip, each wrapped in `retry_with_exponential_backoff`) until it walks all the way back to the miner-supplied `parent_tenure_id`. A single miner who wins one sortition slot fully controls the block-commit's parent pointer (the very "bad parent_block_ptr / vtxindex=0" case this code's own comment calls out), and can therefore point it at an arbitrarily old, but real, ancestor sortition. This forces every signer in the network to perform an unbounded, sequential chain of blocking network calls before it can even decide to reject the proposal.

### Finding Description
`check_parent_tenure_choice` short-circuits only when `self.prior_sortition == self.parent_tenure_id`: [1](#0-0) 

Otherwise it calls `client.get_tenure_forking_info(&self.parent_tenure_id, &self.prior_sortition)`, whose client implementation loops, issuing one HTTP request per up-to-10-tenure "page," until the returned chain reaches `chosen_parent`: [2](#0-1) 

Each page is capped server-side at `DEPTH_LIMIT = 10` per request: [3](#0-2) [4](#0-3) 

But the client loop in `get_tenure_forking_info` has no upper bound on the number of pages it will request — it keeps calling `get_tenure_forking_info_step` until it reaches the caller-supplied `chosen_parent`, which is entirely attacker-controlled (`self.parent_tenure_id` comes straight from the sortition winner's block-commit parent pointer, which the function's own doc comment says exists precisely "to catch block commits with bad parent_block_ptr"): [5](#0-4) 

This path is reached synchronously from `check_proposal`, which is invoked directly from the signer's proposal-handling flow before any node validation is even requested: [6](#0-5) 

The equivalent v2 tenure-change path calls the same `check_parent_tenure_choice` from `validate_tenure_change_payload`, also before submission for node validation: [7](#0-6) 

Because a miner needs only a single winning sortition (no majority, no other signer's key, no auth token) to set an arbitrarily old but real ancestor as the claimed parent tenure, the depth of the walk is bounded only by the actual chain height, not by any signer- or client-side limit. Every signer that receives the proposal performs this same walk before it can reach a verdict (accept, reject as `ReorgNotAllowed`/`InvalidParentBlock`, or otherwise), each step being a blocking HTTP call to its own node with exponential-backoff retries.

### Impact Explanation
This is a liveness wedge in the signer's proposal-evaluation path: a single one-slot miner can force every signer to spend an unbounded amount of wall-clock time (proportional to real chain depth, in units of 10-tenure network round trips, each subject to retry/backoff) synchronously inside `check_proposal`/`validate_tenure_change_payload` before it can move on to evaluate the next proposal or pre-commit. Because this runs on the signer's block-handling path prior to node validation, it can delay a signer's ability to timely evaluate concurrent (legitimate) proposals, directly matching the "signer wedged into never signing valid blocks" high-impact category from an attacker requiring nothing beyond a single mined tenure with a crafted (deep) parent pointer — analogous to PDFBox's crafted page tree triggering an extremely long-running parse.

### Likelihood Explanation
Likelihood is high for the trigger condition (any single miner can supply an old parent tenure hash in their block-commit/tenure-change), but the magnitude of the resulting delay scales with how deep into history a still-valid ancestor sortition can be referenced, and is capped by real chain height — so the worst case grows with chain age. The actual network latency contributed per signer's node (local RPC, typically low latency) somewhat limits per-hop cost, but the number of hops for a long-lived chain can be very large. Whether operators have observed or bounded this in practice is not verifiable from the available code alone, and I was not able to determine whether a global timeout wraps the whole `check_proposal` call.

### Recommendation
Bound the total depth (or total wall-clock time) that `StacksClient::get_tenure_forking_info` is allowed to walk in `stacks-signer/src/client/stacks_client.rs`, and treat exceeding that bound as an immediate rejection of the proposal (e.g., "reorg too deep, reject as `ReorgNotAllowed`") rather than continuing to page indefinitely. Consider enforcing this bound consistently with `MAX_FORK_DEPTH`, which is already used elsewhere in the signer to reason about how deep a reorg can plausibly be honored.

### Proof of Concept
1. A miner wins a single sortition and submits a block-commit / tenure-change whose parent pointer (`prev_tenure_consensus_hash`) references a real but very old ancestor sortition (e.g., near genesis) instead of the actual prior sortition.
2. Each signer receiving the resulting block proposal runs `SortitionsView::check_proposal` (v1) or the v2 tenure-change validation, both of which detect `parent_tenure_id != prior_sortition` and call `SortitionData::check_parent_tenure_choice`.
3. `check_parent_tenure_choice` calls `client.get_tenure_forking_info(parent_tenure_id, prior_sortition)`, which loops issuing one `/v3/tenures/fork_info/:start/:stop` request per 10 tenures of chain depth until it reaches `parent_tenure_id`.
4. Because the client-side loop has no cap, the signer performs `O(chain_depth / 10)` sequential blocking HTTP calls (each with its own retry/backoff) before it can even reject the proposal, stalling its evaluation of that and subsequent proposals for a duration proportional to chain age. [8](#0-7)

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L176-195)
```rust
        // if the parent tenure is the last sortition, it is a valid choice.
        // if the parent tenure is a reorg, then all of the reorged sortitions
        //  must either have produced zero blocks _or_ produced their first (and only) block
        //  very close to the burn block transition.
        if self.prior_sortition == self.parent_tenure_id {
            return Ok(true);
        }
        info!(
            "Most recent miner's tenure does not build off the prior sortition, checking if this is valid behavior";
            "sortition_state.consensus_hash" => %self.consensus_hash,
            "sortition_state.prior_sortition" => %self.prior_sortition,
            "sortition_state.parent_tenure_id" => %self.parent_tenure_id,
        );

        let tenures_reorged =
            client.get_tenure_forking_info(&self.parent_tenure_id, &self.prior_sortition)?;
        if tenures_reorged.is_empty() {
            warn!("Miner is not building off of most recent tenure, but stacks node was unable to return information about the relevant sortitions. Marking miner invalid.");
            return Ok(false);
        }
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

**File:** stacks-signer/src/chainstate/v1.rs (L180-202)
```rust
            if !consensus_hash_match && !parent_tenure_id_match {
                // More expensive check, so do it only if we need to.
                let is_valid_parent_tenure = self.cur_sortition.data.check_parent_tenure_choice(
                    signer_db,
                    client,
                    &self.config.first_proposal_burn_block_timing,
                )?;
                if !is_valid_parent_tenure {
                    warn!(
                        "Current sortition does not build off of canonical tip tenure, marking as invalid";
                        "current_sortition_parent" => ?self.cur_sortition.data.parent_tenure_id,
                        "tip_consensus_hash" => ?tip.block.header.consensus_hash,
                    );
                    self.cur_sortition.miner_status =
                        SortitionMinerStatus::InvalidatedBeforeFirstBlock;

                    // If the current proposal is also for this current
                    // sortition, then we can return early here.
                    if self.cur_sortition.data.consensus_hash == block.header.consensus_hash {
                        return Err(RejectReason::ReorgNotAllowed);
                    }
                }
            }
```

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

**File:** stacks-signer/src/chainstate/v1.rs (L496-504)
```rust
        // now, we have to check if the parent tenure was a valid choice.
        let is_valid_parent_tenure = proposed_by.state().data.check_parent_tenure_choice(
            signer_db,
            client,
            &self.config.first_proposal_burn_block_timing,
        )?;
        if !is_valid_parent_tenure {
            return Err(RejectReason::ReorgNotAllowed);
        }
```
