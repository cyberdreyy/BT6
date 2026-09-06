Based on my investigation, I found a genuine structural analog to the reported CVE class (an attacker-controlled backward-chasing walk that forces unbounded sequential work) reachable by a single one-slot miner in this codebase.

### Title
Signer-side unbounded backward tenure-fork walk triggered by an attacker-chosen ancient `prev_tenure_consensus_hash` causes a synchronous liveness wedge - (File: `stacks-signer/src/client/stacks_client.rs`)

### Summary
`SortitionData::check_parent_tenure_choice` (called synchronously from the signer's block-proposal-validation path, `validate_tenure_change_payload`, for every tenure-change proposal) calls `StacksClient::get_tenure_forking_info`, which walks backward through the *entire* canonical sortition history in fixed batches of 10 until it reaches the miner-supplied `parent_tenure_id`. A single miner who wins one sortition can set the `TenureChangePayload::prev_tenure_consensus_hash` to any arbitrarily old (but still canonical) consensus hash, forcing every signer that receives the proposal to perform O(chain_height / 10) sequential, blocking network round trips before the proposal can even be rejected.

### Finding Description
The proposal-time check path is: `handle_block_proposal` → `check_block_against_state`/`check_block_against_local_state`/`check_block_against_global_state` → `check_proposal` (v1) / delegated checks (v2) → `validate_tenure_change_payload` → `check_parent_tenure_choice`: [1](#0-0) 

This early-returns cheaply only when `prior_sortition == parent_tenure_id` (no reorg claimed). Otherwise it unconditionally calls `client.get_tenure_forking_info(&self.parent_tenure_id, &self.prior_sortition)` with no bound on how far back `parent_tenure_id` may be: [2](#0-1) 

`get_tenure_forking_info` on the signer side loops, calling the node's paginated `/v3/tenures/fork_info` endpoint (`get_tenure_forking_info_step`) repeatedly until the returned chain of tenures reaches the requested `chosen_parent`: [3](#0-2) 

Each server-side call advances at most `DEPTH_LIMIT = 10` real sortitions per request: [4](#0-3) [5](#0-4) 

Nothing in `check_parent_tenure_choice` or its callers bounds how old `parent_tenure_id` may be before issuing this walk — `validate_tenure_change_payload` (both v1 and v2) invokes it unconditionally on any tenure-change proposal whose declared parent tenure differs from the actual prior sortition: [6](#0-5) [7](#0-6) 

This entire chain of RPC calls happens synchronously inside the signer's proposal-evaluation path (`handle_block_proposal`), which runs on the signer's single-threaded event loop before the proposal is even submitted to the node for real block validation: [8](#0-7) 

The only guard on proposal freshness is a wall-clock timestamp check (`block_proposal_max_age_secs`), which does not restrict how far back the *claimed parent tenure* can be: [9](#0-8) 

### Impact Explanation
Because `prev_tenure_consensus_hash` is a value the winning miner places inside the tenure-change transaction, and only the block's *own* tenure (not the claimed parent) is checked for canonicity before this walk runs (`check_block_has_valid_tenure` checks the block's own consensus hash, not the payload's parent claim), a single one-slot miner can name an arbitrarily distant historical tenure as the "parent," forcing every signer that receives the proposal to perform a chain-length-proportional number of sequential, blocking HTTP round trips to its node before it can determine the proposal is invalid. Since this runs on the signer's main event-processing thread ahead of submission for node validation, it delays `process_event` for that signer for the duration of the walk, during which the signer cannot process other block proposals, pre-commits, or burn-block events. Because the proposal is broadcast to the whole signer set, this can stall many/most signers simultaneously, threatening the pre-commit/signing timing windows the protocol relies on (`tenure_last_block_proposal_timeout`, `capitulate_miner_view_timeout`) — a liveness wedge consistent with "a signer wedged into never signing valid blocks" in a timely fashion.

### Likelihood Explanation
Triggering this requires only a single sortition win (the "one-slot miner" precondition already assumed in scope) and crafting a tenure-change payload with an old `prev_tenure_consensus_hash`; no majority collusion, no other signer's key, and no auth token are needed. The cost to the attacker is bounded to what it costs to win one sortition; the cost imposed on every signer is proportional to the depth of history chosen, which the miner fully controls and can maximize by pointing to the genesis tenure.

### Recommendation
Bound the depth of the backward walk in `check_parent_tenure_choice`/`get_tenure_forking_info` — e.g., cap it (analogous to `MAX_FORK_DEPTH` already used for superseded-tenure pruning) and immediately reject/mark-invalid any tenure-change proposal whose claimed parent tenure lies beyond that cap, without needing to complete the full backward walk. Additionally, consider moving this validation off the signer's main event-processing thread so a long walk cannot delay handling of concurrent proposals, pre-commits, and state-machine updates.

### Proof of Concept
1. A miner wins a single sortition.
2. The miner builds a tenure-change block whose `TenureChangePayload::prev_tenure_consensus_hash` names a consensus hash from very early in the chain's history (e.g., near genesis) rather than the actual prior sortition, while the block's own tenure/consensus hash is legitimately canonical.
3. The miner broadcasts this `BlockProposal` to the signer set.
4. Each signer's `handle_block_proposal` → `validate_tenure_change_payload` → `check_parent_tenure_choice` detects `prior_sortition != parent_tenure_id` and calls `get_tenure_forking_info(parent_tenure_id, prior_sortition)`, which issues `chain_height / 10` sequential HTTP requests to the node, each doing sortition-DB and (for Nakamoto tenures) nakamoto-blocks-DB lookups, before returning (or ultimately rejecting the proposal as `ReorgNotAllowed`).
5. During this synchronous walk, the signer's event loop (`process_event`) is blocked from handling other proposals/pre-commits/burn-block events, and this happens on every signer that received the broadcast proposal at roughly the same time.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L170-195)
```rust
    pub fn check_parent_tenure_choice(
        &self,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        first_proposal_burn_block_timing: &Duration,
    ) -> Result<bool, SignerChainstateError> {
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

**File:** stackslib/src/net/api/get_tenures_fork_info.rs (L38-38)
```rust
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

**File:** stacks-signer/src/chainstate/v1.rs (L457-501)
```rust
    /// in tenure changes, we need to check:
    /// (1) if the tenure change confirms the expected parent block (i.e.,
    /// the last globally accepted block in the parent tenure)
    /// (2) if the parent tenure was a valid choice
    fn validate_tenure_change_payload(
        &self,
        proposed_by: &ProposedBy,
        tenure_change: &TenureChangePayload,
        block: &NakamotoBlock,
        signer_db: &mut SignerDb,
        client: &StacksClient,
    ) -> Result<(), RejectReason> {
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

        // Ensure that the tenure change block confirms the expected parent block
        let confirms_expected_parent = SortitionData::check_tenure_change_confirms_parent(
            tenure_change,
            block,
            signer_db,
            client,
            self.config.tenure_last_block_proposal_timeout,
            self.config.reorg_attempts_activity_timeout,
        )
        .map_err(SignerChainstateError::from)?;
        if !confirms_expected_parent {
            return Err(RejectReason::InvalidParentBlock);
        }
        // now, we have to check if the parent tenure was a valid choice.
        let is_valid_parent_tenure = proposed_by.state().data.check_parent_tenure_choice(
            signer_db,
            client,
            &self.config.first_proposal_burn_block_timing,
        )?;
```

**File:** stacks-signer/src/chainstate/v2.rs (L303-339)
```rust
    /// in tenure changes, we need to check:
    /// if the tenure change confirms the expected parent block (i.e.,
    /// the last globally accepted block in the parent tenure)
    fn validate_tenure_change_payload(
        tenure_change: &TenureChangePayload,
        block: &NakamotoBlock,
        parent_tenure_id: &ConsensusHash,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        config: &ProposalEvalConfig,
    ) -> Result<(), RejectReason> {
        // Check that the tenure change's prev_tenure matches the signer's known parent tenure.
        // This catches block commits with bad parent_block_ptr (e.g., vtxindex=0 exploit).
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

        // Ensure that the tenure change block confirms the expected parent block
        let confirms_expected_parent = SortitionData::check_tenure_change_confirms_parent(
            tenure_change,
            block,
            signer_db,
            client,
            config.tenure_last_block_proposal_timeout,
            config.reorg_attempts_activity_timeout,
        )
        .map_err(SignerChainstateError::from)?;
        if !confirms_expected_parent {
            return Err(RejectReason::InvalidParentBlock);
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1606-1628)
```rust
        if block_proposal
            .block
            .header
            .timestamp
            .saturating_add(self.block_proposal_max_age_secs)
            < get_epoch_time_secs()
        {
            // Block is too old. Reject it (without validating) rather than silently
            // dropping it: the miner's proposal loop re-sends the same block until it
            // accumulates rejection weight, so a silent drop from the whole signer set
            // would livelock the tenure until the next sortition.
            warn!("{self}: Received a block proposal that is more than {} secs old. Rejecting...", self.block_proposal_max_age_secs;
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_proposal.block.block_id(),
                "block_height" => block_proposal.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "timestamp" => block_proposal.block.header.timestamp,
            );
            let rejection =
                self.create_block_rejection(RejectReason::ProposalTooOld, &block_proposal.block);
            self.send_block_response(&block_proposal.block, rejection.into());
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1670-1672)
```rust
        // Check if proposal can be rejected now if not valid against sortition view
        let block_rejection =
            self.check_block_against_state(stacks_client, sortition_state, &block_info);
```
