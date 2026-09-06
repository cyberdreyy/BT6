### Title
Miner can indefinitely refresh its own inactivity timer with rejected/non-progressing block proposals, defeating the signer set's fallback-to-prior-miner liveness guard - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
The signer set protects against a stalled or misbehaving current-tenure miner by timing it out: `SortitionState::is_timed_out` measures elapsed time since `last_activity_time` (or the burn block receive time) against `block_proposal_timeout`, and if exceeded, `check_miner_inactivity` falls back to the prior tenure's miner so the chain keeps producing blocks. However, `check_latest_block_in_tenure` refreshes `last_activity_time` on *every* incoming block proposal that fails to extend the tenure (i.e. is rejected as not confirming enough prior blocks), as long as the locally accepted/pre-committed block it conflicts with hasn't itself gone stale. This lets the very miner the timeout is meant to catch reset its own clock indefinitely by re-submitting stale/duplicate/non-progressing proposals, without needing majority collusion, another signer's key, or any privileged access.

### Finding Description
`is_timed_out` in `stacks-signer/src/chainstate/v1.rs` computes: [1](#0-0) 
It bases the timeout purely on `last_activity_time` (falling back to burn-block-receive time), and if `elapsed > block_proposal_timeout`, the tenure is deemed inactive.

`check_miner_inactivity` in `stacks-signer/src/v0/signer_state.rs` calls this check on every housekeeping pass and, if timed out, attempts to fall back to the prior sortition's miner so the chain does not stall: [2](#0-1) 

The `last_activity_time` value is written by `check_latest_block_in_tenure` in `stacks-signer/src/chainstate/mod.rs`, which runs on every incoming block proposal for the tenure (as part of proposal evaluation), including proposals that are ultimately rejected because they do not extend the tenure far enough: [3](#0-2) 
The comment explicitly documents the intent: "The miner may just be slow, so count this invalid block proposal towards valid miner activity." A second branch does the same for proposals that only conflict with a pre-committed (unsigned) block: [4](#0-3) 

The breaking equality is: **"activity that counts as evidence the miner is alive" vs. "activity that constitutes actual chain progress."** The current implementation conflates them — a miner that never produces a block taller than its own last accepted/pre-committed block, and simply keeps re-sending the same (or trivially varied) rejected proposal, keeps stamping `last_activity_time` forward on every signer that evaluates the proposal. Since sending a `BlockProposal` requires only the already-won mining slot and is pure gossip (no signer majority, no other signer's key, no local access), a single stalled/malicious miner can perpetually suppress `is_timed_out`, and thus perpetually block the signer set's fallback path to the prior (valid) miner.

This is the direct analog of the reported Ditto bug: there, submitting a trivial new order to the last short record updated `short.updatedAt` via `merge`, resetting the liquidation eligibility clock even though the position remained below the liquidation ratio. Here, submitting a trivial (rejected) block proposal updates `last_activity_time` via `update_last_activity_time`, resetting the miner-inactivity clock even though the miner has produced no new canonical progress. In both cases, an action with no substantive effect on the underlying "health" condition is nonetheless treated as if it were, defeating a time-based safety/liveness mechanism.

### Impact Explanation
This is a liveness wedge: the signer set can be prevented from ever falling back to the prior miner when the current miner is unresponsive or malicious, because the current miner (or anyone relaying its gossip) can keep the tenure's `last_activity_time` fresh by repeatedly proposing blocks that do not progress the chain. This matches the specified High-impact category: "a signer wedged into never signing valid blocks" — here the wedge is against ever *falling back* to a miner that would sign valid blocks, stalling tenure progress for as long as the attacker keeps sending non-progressing proposals, which costs nothing but gossip bandwidth.

### Likelihood Explanation
Likelihood is high given reachability: any current-tenure miner already possesses the ability to submit `BlockProposal` messages, and the refresh path is unconditionally executed by every signer evaluating each proposal (no threshold or majority required). The only requirement is that the "locally accepted"/"pre-committed" reference block hasn't itself gone stale beyond `reorg_attempts_activity_timeout`, which the attacker fully controls since they are the one repeatedly re-proposing.

### Recommendation
Do not let a rejected/non-progressing proposal refresh `last_activity_time` unconditionally. Restrict the activity-refresh to proposals that make genuine, distinguishable progress attempts bounded in rate (e.g., only the first rejection of a given signature/height combination counts, or require the proposal's own timestamp/burn view to advance), or decouple the inactivity timeout entirely from any signal the miner itself can manufacture, mirroring the recommendation in the analog report to impose stricter conditions before letting a self-serving update suppress a safety check.

### Proof of Concept
Not independently executed against a live signer/node; based on static code-path analysis:
1. A miner wins a sortition and produces one block that is locally accepted/pre-committed by signers (sets `last_activity_time` implicitly via normal proposal flow).
2. The miner then goes idle instead of producing further valid blocks, but every `block_proposal_timeout` interval (or faster), re-broadcasts a `BlockProposal` for a block at or below its already-accepted/pre-committed height (a trivial resubmission or duplicate).
3. Each such proposal is evaluated by `check_latest_block_in_tenure` (`stacks-signer/src/chainstate/mod.rs:376-419`); since it doesn't exceed the existing accepted block's `chain_length`, it's rejected via `Ok(false)`, but because the referenced block's `signed_group`/pre-commit hasn't gone stale (attacker-controlled by proposing often enough), the branch calls `signer_db.update_last_activity_time(...)`.
4. `SortitionState::is_timed_out` (`stacks-signer/src/chainstate/v1.rs:55-94`) never exceeds `block_proposal_timeout` because `last_activity_time` keeps advancing, so `check_miner_inactivity` (`stacks-signer/src/v0/signer_state.rs:284-374`) never triggers the fallback to the prior miner, wedging the tenure indefinitely despite no real progress.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L55-94)
```rust
    pub fn is_timed_out(
        sortition: &ConsensusHash,
        db: &SignerDb,
        block_proposal_timeout: Duration,
    ) -> Result<bool, SignerChainstateError> {
        // If we've already signed a block in this tenure, the miner can't have timed out: we have
        // committed a signature to this tenure and must not help abandon it.
        //
        // Importantly, a block we have only pre-committed to does not count! A pre-commit carries
        // no signature, and if it never reaches the pre-commit threshold the tenure can stall
        // indefinitely. Treating it as signed here would suppress the inactivity timeout for
        // exactly the signers that pre-committed, so they could never fall back to the prior
        // miner and the tenure could never recover.
        let has_block = db.has_signed_block_in_tenure(sortition)?;
        if has_block {
            return Ok(false);
        }
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
    }
```

**File:** stacks-signer/src/v0/signer_state.rs (L284-316)
```rust
    pub fn check_miner_inactivity(
        &mut self,
        db: &mut SignerDb,
        client: &StacksClient,
        proposal_config: &ProposalEvalConfig,
        eval: &GlobalStateEvaluator,
    ) -> Result<(), SignerChainstateError> {
        let Self::Initialized(ref mut state_machine) = self else {
            // no inactivity if the state machine isn't initialized
            return Ok(());
        };

        let MinerState::ActiveMiner { ref tenure_id, .. } = state_machine.current_miner else {
            // no inactivity if there's no active miner
            return Ok(());
        };

        let version = SortitionStateVersion::from_protocol_version(
            state_machine.active_signer_protocol_version,
        );
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

**File:** stacks-signer/src/chainstate/mod.rs (L390-419)
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
```

**File:** stacks-signer/src/chainstate/mod.rs (L422-448)
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
        }
```
