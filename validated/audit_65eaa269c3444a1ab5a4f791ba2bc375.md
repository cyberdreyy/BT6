Based on my investigation, I found a clear analog: the `check_latest_block_in_tenure` function's activity-refresh mechanism lets a single miner (the report's "one-slot miner") indefinitely postpone `is_timed_out`, mirroring the external bug's pattern of a permissionless, repeatable, self-timestamp-refreshing call that defeats an accrual/threshold check.

### Title
Miner can indefinitely suppress tenure-inactivity timeout by repeatedly re-proposing stale/conflicting blocks - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`SortitionData::check_latest_block_in_tenure` refreshes `tenure_activity.last_activity_time` any time a miner submits a block proposal that conflicts with (or is no higher than) the tenure's current tip — even though the proposal itself is rejected/superseded and produces no real progress. Because this refresh has no rate limit and is driven entirely by miner-controlled resubmission, a self-interested miner can keep calling this path (by re-sending non-advancing/conflicting proposals) forever, resetting the clock each time before `is_timed_out` in `chainstate/v1.rs`/`v2.rs` ever fires.

### Finding Description
`check_latest_block_in_tenure` [1](#0-0)  is invoked on every proposed block for a tenure. When the proposed block does not confirm more blocks than the tenure's known tip (`block.header.chain_length <= info.block.header.chain_length`), the function still calls `signer_db.update_last_activity_time(&block.header.consensus_hash, get_epoch_time_secs())` [2](#0-1)  as long as the conflict is "fresh" (`reorg_attempts_activity_timeout`). The same reset happens again for a conflicting proposal against a pre-committed (unsigned) block [3](#0-2) .

This activity timestamp is exactly what gates the miner-inactivity/wedge check that both chainstate versions rely on: `is_timed_out` computes `elapsed = now - last_activity_time` and returns `elapsed > block_proposal_timeout` [4](#0-3)  and equivalently in v2 [5](#0-4) . `check_miner_inactivity` only demotes the current miner to the prior tenure's miner once `is_timed_out` returns true [6](#0-5) .

This is structurally the same bug class as the Sherlock report: a permissionless, cheap, repeatable call resets a "last update" timestamp used by a duration-based accrual/threshold check, and the caller who benefits from resetting it (the staker calling `getRewardFor` on themselves in the original; here the miner whose own inactivity is being measured) can keep the delta near zero forever, indefinitely defeating the intended safety/liveness mechanism. Here the equality/guarantee broken is the liveness guarantee documented in `docs/signer-flows.md`: "current tenure timed out? → fall back to prior tenure" is supposed to fire once `block_proposal_timeout` elapses since real miner activity, but "activity" is satisfied by proposals that make no chain progress at all (conflicting/no-higher proposals), as the code comment itself acknowledges: *"The miner may just be slow, so count this invalid block proposal towards valid miner activity."* [7](#0-6) 

### Impact Explanation
A single miner who currently holds a tenure — i.e., exactly the "one-slot miner (plus gossip)" actor class permitted by the rules, since only that miner's proposals are being evaluated against their own tenure — can keep re-submitting stale/conflicting block proposals (well within `reorg_attempts_activity_timeout`) to repeatedly refresh `last_activity_time` without ever advancing the chain. This prevents `is_timed_out` from ever returning true, so signers never fall back to the prior tenure's miner. This is a signer-liveness wedge: the state machine is stuck honoring a miner that is not making progress, matching the "High" impact bucket ("a signer wedged into never ... acting on a stale... miner view") in the rules.

### Likelihood Explanation
The trigger requires only the current miner's own proposal-submission capability (no majority of signers, no other signer's key, no auth_token) — the miner simply needs to keep sending non-advancing/conflicting `BlockProposal` messages over the `.miners` StackerDB contract before `reorg_attempts_activity_timeout` elapses each time, which is trivially automatable and cheap (equivalent effort to the original bug's "call at most every 6 seconds"). This makes the likelihood moderate-to-high assuming a miner is willing to grief its own tenure's timeout window, though the practical benefit to the miner (stalling instead of just mining validly) somewhat limits the incentive compared to the original reward-denial exploit.

### Recommendation
Do not treat non-advancing/conflicting proposals as full resets of the inactivity clock. Options:
1. Only refresh `last_activity_time` when the proposal actually confirms more blocks than the current tip (real progress), not merely when it is "fresh" relative to `reorg_attempts_activity_timeout`.
2. Cap the number of times `update_last_activity_time` can be refreshed by non-advancing proposals within a `block_proposal_timeout` window, so repeated resets cannot exceed the intended timeout budget.
3. Track "genuine activity" and "conflict noise" as separate counters, and base `is_timed_out` only on genuine activity, using the conflict counter purely for diagnostic/rate-limiting purposes.

### Proof of Concept
Not executed (analysis-only, no sandbox access). Conceptually: a miner controlling tenure `T` repeatedly submits a block proposal `B'` with `B'.chain_length <= tip.chain_length` (a trivial conflicting/duplicate proposal) at an interval less than `reorg_attempts_activity_timeout` (default a few seconds per `stacks-signer/src/tests/signer_state.rs` usage of `Duration::from_secs(3)`). Each call re-enters `check_latest_block_in_tenure`, hits the branch at `stacks-signer/src/chainstate/mod.rs:403-417`, and calls `update_last_activity_time` with the current timestamp, resetting the clock `is_timed_out` reads. As long as the miner keeps this cadence up, `elapsed` in `is_timed_out` never exceeds `block_proposal_timeout`, so `check_miner_inactivity` never demotes the miner [6](#0-5) .

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L376-418)
```rust
    pub fn check_latest_block_in_tenure(
        tenure_id: &ConsensusHash,
        block: &NakamotoBlock,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        tenure_last_block_proposal_timeout: Duration,
        reorg_attempts_activity_timeout: Duration,
    ) -> Result<bool, ClientError> {
        let last_block_info = SortitionData::get_tenure_last_block_info(
            tenure_id,
            signer_db,
            tenure_last_block_proposal_timeout,
        )?;

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
```

**File:** stacks-signer/src/chainstate/mod.rs (L422-447)
```rust
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

**File:** stacks-signer/src/chainstate/v1.rs (L76-93)
```rust
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

**File:** stacks-signer/src/chainstate/v2.rs (L67-88)
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
```

**File:** stacks-signer/src/v0/signer_state.rs (L304-316)
```rust
        let is_timed_out = SortitionState::is_timed_out(
            &version,
            tenure_id,
            db,
            client.get_signer_address(),
            proposal_config,
            eval,
        )?;

        if !is_timed_out {
            return Ok(());
        }

```
