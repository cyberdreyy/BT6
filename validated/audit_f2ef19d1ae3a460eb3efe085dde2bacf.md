### Title
Unbounded Client-Side Pagination in `get_tenure_forking_info` Lets a Single Miner Force Excessive Iteration / CPU-and-Network DoS on Every Signer - ([File: stacks-signer/src/client/stacks_client.rs])

### Summary
`StacksClient::get_tenure_forking_info` (`stacks-signer/src/client/stacks_client.rs:318-357`) paginates the node's `/v3/tenures/fork_info/{start}/{stop}` endpoint with a `while` loop that has **no bound on the number of round trips**. It is invoked synchronously, on the signer's single event-processing path, from `SortitionData::check_parent_tenure_choice` (`stacks-signer/src/chainstate/mod.rs:170-195`) whenever a proposed block's tenure does not build on the prior sortition — a condition a miner fully controls by pointing their block-commit's parent at an old tenure. This mirrors the PDFBox CVE-2021-27807 bug class (CWE-834, uncontrolled iteration driven by attacker-supplied structure): a single crafted input (here, a block-commit / tenure-change with a distant `parent_tenure_id`) forces the victim (every signer) into an unbounded work loop before any rejection is issued.

### Finding Description
- `check_parent_tenure_choice` is reached from `check_proposal` (v1: `stacks-signer/src/chainstate/v1.rs:180-202`, v2: `validate_tenure_change_payload` in `stacks-signer/src/chainstate/v2.rs`) any time `cur_sortition.parent_tenure_id != prior_sortition`. That mismatch is exactly the "reorg attempt" shape that miners are allowed to construct — it is the same code path exercised by the repo's own `allow_reorg_within_first_proposal_burn_block_timing_secs` / `mark_miner_as_invalid_if_reorg_is_rejected_v1` tests, i.e. a single miner winning one sortition with an out-of-date parent pointer. [1](#0-0) [2](#0-1) 

- Once triggered, the signer calls `client.get_tenure_forking_info(&self.parent_tenure_id, &self.prior_sortition)`, which walks backwards from `prior_sortition` toward the claimed `parent_tenure_id` in pages, re-issuing HTTP requests until the requested `chosen_parent` is reached: [3](#0-2) 

  The outer `while tenures.back()... != Some(chosen_parent)` loop has no page-count cap, no total-distance cap, and no timeout other than the retry backoff on each individual HTTP call. The node-side handler (`get_tenures_fork_info.rs`) only bounds *one page* by `DEPTH_LIMIT`; it does not prevent the client from requesting arbitrarily many pages: [4](#0-3) 

- Because a miner's block-commit `parent_block_ptr` can legitimately point at any earlier tenure (this is precisely the mechanism the reorg-timing tests exercise), a single miner can set `parent_tenure_id` to a tenure thousands of sortitions in the past. Every signer that receives this proposal will then perform `O(distance / DEPTH_LIMIT)` sequential, blocking HTTP round-trips to its own node — each page itself doing DB reads for every tenure in it — **before** `check_parent_tenure_choice` ever gets to evaluate/reject the reorg. The iteration cost is entirely a function of the attacker-chosen distance, unbounded by the size of the actual block proposal.

- Because `process_event`/`handle_event_match`/`handle_block_proposal` run on the signer's single event-processing path (there is no separate thread pool per proposal in `stacks-signer/src/v0/signer.rs`), this call blocks that signer from processing anything else — other proposals, `BlockPreCommit`/`BlockResponse` messages, timeouts, `NewBurnBlock`/`NewBlock` events — for the full duration of the walk. [5](#0-4) [6](#0-5) 

### Impact Explanation
This is a **liveness wedge**: a single, otherwise-valid sortition winner can force every signer's event loop to stall for the duration of an unbounded, self-inflicted pagination walk (bandwidth- and DB-query-bound, scaling with attacker-chosen tenure distance) before the proposal is even rejected. While stalled, the signer cannot:
- respond to pre-commits/responses for other, legitimate block proposals (risking missed 70% thresholds/timeouts),
- process `NewBurnBlock`/`NewBlock` housekeeping (`handle_pending_update`, `check_miner_inactivity`, `capitulate_viewpoint`),
- submit or receive validation results for other proposals it has queued.

This matches the "High" bucket: a signer wedged into not promptly signing valid blocks or updating state, driven purely by a single-slot miner's crafted input — no majority collusion required.

### Likelihood Explanation
Moderate-to-high. A miner does not need any collusion or a compromised key: they simply need to win one sortition with a block-commit whose parent pointer references a distant historical tenure — a scenario the codebase itself acknowledges and tests as a normal (if usually rejected) "reorg attempt." The attacker does not need the reorg to succeed; the expensive pagination happens regardless of the eventual accept/reject outcome, and can be repeated on every sortition the attacker wins (or via repeated proposals for the same tenure, since `should_reevaluate_block` routes non-decided proposals back through fresh evaluation).

### Recommendation
- Bound `StacksClient::get_tenure_forking_info`'s pagination loop with a hard maximum number of pages/round trips (and/or a wall-clock timeout), returning an error (treated as "invalid parent tenure") once exceeded, mirroring the `MAX_FORK_DEPTH` cap already used elsewhere in the signer for superseded-tenure bookkeeping.
- Alternatively/additionally, have the node-side `/v3/tenures/fork_info` handler enforce and communicate a global depth budget so the client can fail fast instead of re-paging indefinitely.
- Move `check_parent_tenure_choice`'s network-bound work off the signer's main event-processing path (e.g., into the same asynchronous validation-submission flow used for node block validation) so a slow/adversarial fork-info walk cannot block unrelated event processing.

### Proof of Concept
1. Attacker (miner) constructs a block-commit for their won sortition whose `parent_block_ptr` targets a tenure very far back in history (e.g., thousands of tenures/sortitions before the current `prior_sortition`), while everything else about the commit/proposal is otherwise well-formed (as exercised by `allow_reorg_within_first_proposal_burn_block_timing_secs` / `mark_miner_as_invalid_if_reorg_is_rejected_v1`).
2. The miner proposes a tenure-change block for this sortition and broadcasts it to signers.
3. Every signer's `check_proposal` detects `parent_tenure_id != prior_sortition` and calls `check_parent_tenure_choice`, which calls `StacksClient::get_tenure_forking_info(parent_tenure_id, prior_sortition)`. [7](#0-6) 
4. `get_tenure_forking_info`'s unbounded `while` loop issues repeated `/v3/tenures/fork_info/{start}/{stop}` requests, each processing up to `DEPTH_LIMIT` tenures server-side, continuing until it has walked the entire attacker-chosen distance back to `parent_tenure_id`. [8](#0-7) 
5. During this walk (which scales with the attacker-chosen distance and can be made arbitrarily large), the signer's single-threaded event loop is blocked from handling any other block proposals, pre-commits, or timeouts, matching the liveness-wedge impact described above.

Note: I was not able to execute this against a live signer/node instance to measure exact wall-clock impact; the finding is based on static analysis of the pagination and call-graph code shown above. The magnitude of the DoS (seconds vs. minutes) depends on `DEPTH_LIMIT`'s concrete value and the round-trip latency between signer and node, which I could not confirm from the indexed snippets alone.

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

**File:** stacks-signer/src/chainstate/v1.rs (L176-202)
```rust
            let consensus_hash_match =
                self.cur_sortition.data.consensus_hash == tip.block.header.consensus_hash;
            let parent_tenure_id_match =
                self.cur_sortition.data.parent_tenure_id == tip.block.header.consensus_hash;
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

**File:** stackslib/src/net/api/get_tenures_fork_info.rs (L211-259)
```rust
        let result = node.with_node_state(|network, sortdb, chainstate, _mempool, _rpc_args| {
            let start_from = self
                .stop_sortition
                .clone()
                .ok_or_else(|| ChainError::NoSuchBlockError)?;
            let recurse_end = self
                .start_sortition
                .clone()
                .ok_or_else(|| ChainError::NoSuchBlockError)?;
            let recurse_end_snapshot =
                SortitionDB::get_block_snapshot_consensus(sortdb.conn(), &recurse_end)?
                    .ok_or_else(|| ChainError::NoSuchBlockError)?;
            let height_bound = recurse_end_snapshot.block_height;

            let mut results = vec![];
            let mut cursor = SortitionDB::get_block_snapshot_consensus(sortdb.conn(), &start_from)?
                .ok_or_else(|| ChainError::NoSuchBlockError)?;
            results.push(TenureForkingInfo::from_snapshot(
                &cursor,
                sortdb,
                chainstate,
                &network.stacks_tip.block_id(),
            )?);
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

**File:** stacks-signer/src/v0/signer.rs (L332-416)
```rust
    /// Process the event
    fn process_event(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        event: Option<&SignerEvent<SignerMessage>>,
        _res: &Sender<SignerResult>,
        current_reward_cycle: u64,
    ) {
        self.check_submitted_block_proposal();
        self.check_pending_block_validations(stacks_client);

        let mut prior_state = self.local_state_machine.clone();
        let local_signer_protocol_version = self.get_signer_protocol_version();
        if self.reward_cycle <= current_reward_cycle {
            self.local_state_machine.handle_pending_update(&mut self.signer_db, stacks_client,
                &self.proposal_config,
                &mut self.tx_replay_scope, &self.global_state_evaluator, local_signer_protocol_version)
                .unwrap_or_else(|e| error!("{self}: failed to update local state machine for pending update"; "err" => ?e));
        }
        // See if we should capitulate our viewpoint...
        self.local_state_machine.capitulate_viewpoint(
            stacks_client,
            &mut self.signer_db,
            &mut self.global_state_evaluator,
            local_signer_protocol_version,
            sortition_state,
            self.capitulate_miner_view_timeout,
            self.proposal_config.tenure_last_block_proposal_timeout,
            &mut self.last_capitulate_miner_view,
        );

        if prior_state != self.local_state_machine {
            let version = self.get_signer_protocol_version();
            self.local_state_machine
                .send_signer_update_message(&mut self.stackerdb, version);
            prior_state = self.local_state_machine.clone();
        }

        let event_parity = match event {
            // Block proposal events do have reward cycles, but each proposal has its own cycle,
            //  and the vec could be heterogeneous, so, don't differentiate.
            Some(SignerEvent::BlockValidationResponse(_))
            | Some(SignerEvent::MinerMessages(..))
            | Some(SignerEvent::NewBurnBlock { .. })
            | Some(SignerEvent::NewBlock { .. })
            | Some(SignerEvent::StatusCheck)
            | None => None,
            Some(SignerEvent::SignerMessages { signer_set, .. }) => {
                Some(u64::from(*signer_set) % 2)
            }
        };
        let other_signer_parity = (self.reward_cycle + 1) % 2;
        if event_parity == Some(other_signer_parity) {
            return;
        }
        debug!("{self}: Processing event: {event:?}");
        let Some(event) = event else {
            // No event. Do nothing.
            debug!("{self}: No event received");
            return;
        };
        if self.reward_cycle > current_reward_cycle
            && !matches!(
                event,
                SignerEvent::StatusCheck | SignerEvent::NewBurnBlock { .. }
            )
        {
            // The reward cycle has not yet started for this signer instance
            // Do not process any events other than status checks or new burn blocks
            debug!("{self}: Signer reward cycle has not yet started. Ignoring event.");
            return;
        }

        self.handle_event_match(stacks_client, sortition_state, event, current_reward_cycle);

        self.check_submitted_block_proposal();
        self.check_pending_block_validations(stacks_client);

        if prior_state != self.local_state_machine {
            let version = self.get_signer_protocol_version();
            self.local_state_machine
                .send_signer_update_message(&mut self.stackerdb, version);
        }
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1574-1673)
```rust
    /// Handle block proposal messages submitted to signers stackerdb
    fn handle_block_proposal(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        block_proposal: &BlockProposal,
    ) {
        debug!("{self}: Received a block proposal: {block_proposal:?}");
        if block_proposal.reward_cycle != self.reward_cycle {
            // We are not signing for this reward cycle. Ignore the block.
            debug!(
                "{self}: Received a block proposal for a different reward cycle. Ignore it.";
                "requested_reward_cycle" => block_proposal.reward_cycle
            );
            return;
        }

        let signer_signature_hash = block_proposal.block.header.signer_signature_hash();
        let prior_block_info = self.block_lookup_by_reward_cycle(&signer_signature_hash);
        if let Some(block_info) = &prior_block_info {
            // If we have already decided on this block, resend that decision (or ignore
            // the proposal) rather than evaluating it again.
            if !self.should_reevaluate_block(
                stacks_client,
                sortition_state,
                block_info,
                block_proposal,
            ) {
                return;
            }
        }

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

        let pending_responses = if prior_block_info.is_some() {
            PendingBlockResponses::empty()
        } else {
            info!(
                "{self}: received a block proposal for a new block.";
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_proposal.block.block_id(),
                "block_height" => block_proposal.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "consensus_hash" => %block_proposal.block.header.consensus_hash,
            );
            self.signer_db
                .drain_pending_block_responses(&signer_signature_hash)
                .unwrap_or_else(|e| {
                    warn!(
                        "{self}: Failed to drain pending block responses for block proposal: {e:?}";
                        "signer_signature_hash" => %signer_signature_hash,
                        "block_id" => %block_proposal.block.block_id(),
                    );
                    PendingBlockResponses::empty()
                })
        };
        crate::monitoring::actions::increment_block_proposals_received();
        // Creating a new proposal will overwrite any prior proposal info on the block if it exists, e.g. validity, signed_timestamps, etc.
        let mut block_info = BlockInfo::from(block_proposal.clone());

        // Get sortition view if we don't have it
        if sortition_state.is_none() {
            *sortition_state =
                SortitionsView::fetch_view(self.proposal_config.clone(), stacks_client)
                    .inspect_err(|e| {
                        warn!(
                            "{self}: Failed to update sortition view: {e:?}";
                            "signer_signature_hash" => %signer_signature_hash,
                            "block_id" => %block_proposal.block.block_id(),
                        )
                    })
                    .ok();
        }

        // Check if proposal can be rejected now if not valid against sortition view
        let block_rejection =
            self.check_block_against_state(stacks_client, sortition_state, &block_info);

```
