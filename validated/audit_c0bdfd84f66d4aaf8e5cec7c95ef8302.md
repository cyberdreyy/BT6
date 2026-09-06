### Title
Repeated invalid/reorging block proposals from the current-slot miner reset `last_activity_time` and suppress the inactivity timeout, wedging signers so they never fall back to the prior miner - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
`SortitionState::is_timed_out` (v1 and v2) is the only liveness escape hatch that lets signers abandon a stuck/unresponsive current-slot miner and fall back to the prior tenure. It is driven entirely by `SignerDb::get_last_activity_time`, which is bumped by `check_latest_block_in_tenure` whenever a proposal conflicts with what the signer already knows — even though such a proposal is always rejected. A miner never needs to produce a valid, signable block to keep resetting this timer; sending cheap, doomed proposals is enough, exactly as `chargeFundingRate` in the referenced report can be called with an empty `quoteIds` array to bump `partyANonces` while doing no real work.

### Finding Description
`SortitionState::is_timed_out` treats a tenure as still active as long as `now - last_activity_time <= block_proposal_timeout`, and `last_activity_time` defaults to the burn-block-received time if never set: [1](#0-0) [2](#0-1) 

The only place `update_last_activity_time` is written is inside `check_latest_block_in_tenure`, on the branch that handles a proposal that does **not** advance the tenure (`block.header.chain_length <= info.block.header.chain_length`) or that conflicts with a fresh pre-commit: [3](#0-2) 

Both conditions gating the write are satisfied by proposals that will certainly be rejected:
- The first branch fires whenever `info.signed_group` is `None` (i.e., nothing has reached group signature yet) *or* the group signature is younger than `reorg_attempts_activity_timeout` — this is true throughout most of a tenure's life, especially right after the tenure starts and before any block has reached the 70% threshold.
- The comment explicitly documents the intent as "count this invalid block proposal towards valid miner activity" — the code path is designed to treat some invalid proposals as activity, but it does not require the block to be well-formed beyond deserializing into a `NakamotoBlock` header with a valid `chain_length`/`consensus_hash`, which a miner fully controls and can regenerate at will.

This same routine is invoked for both same-tenure checks (`confirms_latest_block_in_same_tenure`) and tenure-change checks against the parent tenure (`check_tenure_change_confirms_parent`), so the currently-active miner can hit this branch simply by proposing a same-tenure block at or below the last known height, repeatedly: [4](#0-3) 

Because `is_timed_out` short-circuits to `false` whenever `has_signed_block_in_tenure` is true, and otherwise depends solely on this activity timestamp, a miner who never gets a signature (e.g. because it never proposes anything the signer set will actually approve) can nonetheless indefinitely postpone timeout by feeding a stream of cheap, guaranteed-to-be-rejected low-height/duplicate proposals that only need to pass `check_latest_block_in_tenure`'s early activity-touching branch, not full validation.

### Impact Explanation
This breaks the liveness guarantee that `block_proposal_timeout` / `reorg_attempts_activity_timeout` are meant to enforce: once a miner is inactive (has stopped producing valid, signable blocks), signers should time it out and `SortitionsView::check_proposal` should invalidate it (`SortitionMinerStatus::InvalidatedBeforeFirstBlock`), letting the network fall back to the prior tenure's miner: [5](#0-4) 

If the current-slot miner keeps this timer alive with worthless proposals, `is_timed_out` never returns true, the miner is never marked invalid, and the prior-miner fallback path is never reached — a liveness wedge matching the "High" severity criteria (a signer prevented from ever falling back / never signing valid blocks because it keeps deferring to a miner that is effectively dead). No signature or consensus rule is broken directly, but the chain's block-production liveness for that tenure is stalled at the discretion of a single actor holding the current mining slot, mirroring how a single `partyB` could indefinitely block `partyA`'s operations at negligible cost.

### Likelihood Explanation
Likelihood is high given the ease of triggering the vulnerable branch: any miner who has won the current sortition slot can, at zero cost beyond block construction/broadcast, submit a proposal that is guaranteed to fail `check_latest_block_in_tenure`'s height/duplicate check (e.g., a proposal that doesn't increase chain length, or repeats an already-seen height) and still update `last_activity_time`. This requires only being the active miner for the slot (a role the protocol already grants to one entity at a time), not a majority of signers or any other privileged access, and does not require producing anything that could ever be signed.

### Recommendation
Do not treat a proposal that fails `check_latest_block_in_tenure` as miner activity unless it is at least "close enough" to plausible progress (e.g., require it to actually extend the chain length beyond the last *pre-committed* height, not merely be within a freshness window of the last group signature), or bound the number/rate of such activity-only touches per tenure so a miner cannot indefinitely re-arm the inactivity timer purely by resubmitting known-bad proposals. Alternatively, gate the activity bump on the proposal having passed some minimal structural/PoW-adjacent cost (e.g., requiring a fresh, higher burn view or elapsed time since the last touch) so repeated submissions of the same or lower-height block cannot be used to perpetually reset the timer.

### Proof of Concept
1. Win the current sortition slot as the active miner.
2. Never propose a block that the signer set can actually sign (e.g., because you intend to stall, or you know it will be rejected on other grounds).
3. Every `reorg_attempts_activity_timeout`-ish interval (well under `block_proposal_timeout`), submit a new block proposal at or below the tenure's currently-known height (or one that conflicts with a fresh pre-commit) for the same tenure.
4. Each such proposal is rejected by `check_latest_block_in_tenure`'s height check, but before rejecting it calls `signer_db.update_last_activity_time(&block.header.consensus_hash, get_epoch_time_secs())` because `info.signed_group` is either `None` or fresher than `reorg_attempts_activity_timeout`.
5. `SortitionState::is_timed_out` for this tenure keeps measuring `elapsed` from the freshly bumped `last_activity_time`, so it never exceeds `block_proposal_timeout`.
6. `SortitionsView::check_proposal` never marks the current miner `InvalidatedBeforeFirstBlock`, so signers never fall back to the prior tenure, stalling block production for as long as the miner keeps resubmitting cheap, doomed proposals.

I was not able to execute this against a live signer/testnet from this environment (no filesystem/terminal access here), so the timing thresholds (`reorg_attempts_activity_timeout` vs `block_proposal_timeout`) and exact reproduction steps should be validated by a Devin session with repo access if a concrete exploit script or integration test is desired.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L72-93)
```rust
        let Some(received_ts) = db.get_burn_block_receive_time_ch(sortition)? else {
            return Ok(false);
        };
        let received_time = UNIX_EPOCH + Duration::from_secs(received_ts);
        let last_activity = db
            .get_last_activity_time(sortition)?
            .map(|time| UNIX_EPOCH + Duration::from_secs(time))
            .unwrap_or(received_time);

        let Ok(elapsed) = std::time::SystemTime::now().duration_since(last_activity) else {
            return Ok(false);
        };

        if elapsed > block_proposal_timeout {
            info!(
                "Tenure miner was inactive too long and timed out";
                "tenure_ch" => %sortition,
                "elapsed_inactive" => elapsed.as_secs(),
                "config_block_proposal_timeout" => block_proposal_timeout.as_secs()
            );
        }
        Ok(elapsed > block_proposal_timeout)
```

**File:** stacks-signer/src/chainstate/v1.rs (L144-163)
```rust
        if self.cur_sortition.miner_status == SortitionMinerStatus::Valid
            && SortitionState::is_timed_out(
                &self.cur_sortition.data.consensus_hash,
                signer_db,
                self.config.block_proposal_timeout,
            )?
        {
            info!(
                "Current miner timed out, marking as invalid.";
                "block_height" => block.header.chain_length,
                "block_proposal_timeout" => ?self.config.block_proposal_timeout,
                "current_sortition_consensus_hash" => ?self.cur_sortition.data.consensus_hash,
            );
            self.cur_sortition.miner_status = SortitionMinerStatus::InvalidatedBeforeFirstBlock;

            // If the current proposal is also for this current
            // sortition, then we can return early here.
            if self.cur_sortition.data.consensus_hash == block.header.consensus_hash {
                return Err(RejectReason::InvalidMiner);
            }
```

**File:** stacks-signer/src/chainstate/v2.rs (L67-89)
```rust
        let Some(received_ts) =
            signer_db.get_burn_block_received_time_from_signers(eval, sortition, local_address)?
        else {
            return Ok(false);
        };
        let received_time = UNIX_EPOCH + Duration::from_secs(received_ts);
        let last_activity = signer_db
            .get_last_activity_time(sortition)?
            .map(|time| UNIX_EPOCH + Duration::from_secs(time))
            .unwrap_or(received_time);

        let Ok(elapsed) = std::time::SystemTime::now().duration_since(last_activity) else {
            return Ok(false);
        };
        if elapsed > timeout {
            info!("Sortition has timed out";
                "sorition" => %sortition,
                "timeout" => %timeout.as_secs(),
                "elapsed" => %elapsed.as_secs()
            )
        }
        Ok(elapsed > timeout)
    }
```

**File:** stacks-signer/src/chainstate/mod.rs (L390-447)
```rust
        if let Some(info) = last_block_info {
            // N.B. this block might not be the last globally accepted block across the network;
            // it's just the highest one in this tenure that we know about.  If this given block is
            // no higher than it, then it's definitely no higher than the last globally accepted
            // block across the network, so we can do an early rejection here.
            if block.header.chain_length <= info.block.header.chain_length {
                warn!(
                    "Miner's block proposal does not confirm as many blocks as we expect";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "proposed_chain_length" => block.header.chain_length,
                    "expected_at_least" => info.block.header.chain_length + 1,
                );
                if info.signed_group.is_none_or(|signed_time| {
                    signed_time + reorg_attempts_activity_timeout.as_secs() > get_epoch_time_secs()
                }) {
                    // Note if there is no signed_group time, this is a locally accepted block (i.e. tenure_last_block_proposal_timeout has not been exceeded).
                    // Treat any attempt to reorg a locally accepted block as valid miner activity.
                    // If the call returns a globally accepted block, check its globally accepted time against a quarter of the block_proposal_timeout
                    // to give the miner some extra buffer time to wait for its chain tip to advance
                    // The miner may just be slow, so count this invalid block proposal towards valid miner activity.
                    if let Err(e) = signer_db.update_last_activity_time(
                        &block.header.consensus_hash,
                        get_epoch_time_secs(),
                    ) {
                        warn!("Failed to update last activity time: {e}");
                    }
                }
                return Ok(false);
            }
        }

        // A block we have only pre-committed to must NOT veto this proposal, but, similar to above
        // this should still count as activity for the miner.
        let last_accepted_block = signer_db
            .get_last_accepted_block(tenure_id)
            .map_err(|e| ClientError::InvalidResponse(e.to_string()))?;
        if let Some(info) = last_accepted_block {
            let is_fresh_pre_commit = info.state == BlockState::PreCommitted
                && info.approved_time.is_some_and(|approved_time| {
                    approved_time.saturating_add(tenure_last_block_proposal_timeout.as_secs())
                        > get_epoch_time_secs()
                });
            if is_fresh_pre_commit && block.header.chain_length <= info.block.header.chain_length {
                info!(
                    "Miner's block proposal conflicts with a block we have only pre-committed to. Counting it as miner activity, but not rejecting the proposal.";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "proposed_chain_length" => block.header.chain_length,
                    "pre_committed_signer_signature_hash" => %info.block.header.signer_signature_hash(),
                    "pre_committed_chain_length" => info.block.header.chain_length,
                );
                if let Err(e) = signer_db
                    .update_last_activity_time(&block.header.consensus_hash, get_epoch_time_secs())
                {
                    warn!("Failed to update last activity time: {e}");
                }
            }
```

**File:** stacks-signer/src/chainstate/mod.rs (L506-520)
```rust
    fn confirms_latest_block_in_same_tenure(
        block: &NakamotoBlock,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        proposal_config: &ProposalEvalConfig,
    ) -> Result<bool, ClientError> {
        Self::check_latest_block_in_tenure(
            &block.header.consensus_hash,
            block,
            signer_db,
            client,
            proposal_config.tenure_last_block_proposal_timeout,
            proposal_config.reorg_attempts_activity_timeout,
        )
    }
```
