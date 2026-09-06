### Title
Miner-controlled activity-timer reset lets a stalled tenure block signer fallback indefinitely - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`SortitionData::check_latest_block_in_tenure` treats any block proposal that fails to advance the tenure's chain length (a "reorg attempt" against the tenure's own last signed/pre-committed block) as valid "miner activity" and calls `signer_db.update_last_activity_time(...)`. This timestamp is exactly the clock that `SortitionState::is_timed_out` (in both `chainstate/v1.rs` and `chainstate/v2.rs`) uses to decide whether the current miner has gone inactive and whether signers should fall back to the prior tenure's miner. Because the current tenure's own miner is the entity issuing these proposals, a single (one-slot) miner can keep resetting its own inactivity clock forever by repeatedly sending a stale/non-advancing block proposal, without ever producing a valid block that advances the chain. This is structurally the same griefing pattern as the MochiVault report: a cheap, repeatable, self-serviced action resets a timer that is supposed to gate a corrective action (there: withdraw; here: fallback-to-prior-miner).

### Finding Description
`check_latest_block_in_tenure` (`stacks-signer/src/chainstate/mod.rs`, lines 376-478) is invoked for every incoming block proposal for the tenure being evaluated (`confirms_latest_block_in_same_tenure`, called from the `check_proposal` validation path in `chainstate/v1.rs`/`v2.rs`) as well as for tenure-change confirmations (`check_tenure_change_confirms_parent`). [1](#0-0) 

When the proposed block does not exceed the chain length of the tenure's currently known best block, the code does not just reject it — it also unconditionally records the event as "miner activity" and refreshes `last_activity_time` for that tenure's consensus hash, provided the previous signed/pre-committed block is still "fresh" (within `reorg_attempts_activity_timeout`):

```
if info.signed_group.is_none_or(|signed_time| {
    signed_time + reorg_attempts_activity_timeout.as_secs() > get_epoch_time_secs()
}) {
    ...
    signer_db.update_last_activity_time(&block.header.consensus_hash, get_epoch_time_secs())
}
```

The same pattern repeats for the pre-commit branch a few lines below (lines 422-447): a proposal that merely conflicts with a fresh pre-commit (but is rejected) still resets the activity timer. [2](#0-1) 

This `last_activity_time` is precisely what both sortition-state versions consult to decide inactivity: [3](#0-2) [4](#0-3) 

And `check_miner_inactivity` in `v0/signer_state.rs` only attempts to fall back to the prior tenure's miner once `is_timed_out` returns true: [5](#0-4) 

Because the tenure's own miner is a single actor (one sortition winner) and is the one issuing block proposals for its own tenure, it can indefinitely defer being timed out by periodically re-proposing (or replaying) a block that does not advance the chain length (e.g., re-sending the same block, or a lower/duplicate one). Each such rejected proposal still refreshes `last_activity_time`, so `elapsed > block_proposal_timeout` in `is_timed_out` never becomes true, and `check_miner_inactivity` never triggers the fallback to the prior tenure's (working) miner. This wedges the equality that the timeout mechanism is meant to enforce: "if the current miner is inactive for `block_proposal_timeout`, signers must recognize it as inactive and fall back." No majority of signers, no other signer's key, and no StackerDB/transport-level exploitation is required — a lone miner triggers it purely through its own gossip messages (block proposals).

### Impact Explanation
This is a liveness wedge: a miner (the current tenure's sole legitimate proposer) can hold the network hostage by never producing a valid advancing block while periodically sending stale/non-advancing proposals just to keep resetting its own inactivity timer. Signers will never conclude the miner is inactive, and therefore will never revert to the previous (potentially still-live and cooperative) miner's tenure, halting chain progress until a new burn block/sortition naturally occurs. This matches the High-impact category: "a signer wedged into never signing valid blocks" / acting on a stale view indefinitely because the safety valve (inactivity fallback) can be suppressed at will by the very party it is meant to constrain.

### Likelihood Explanation
High feasibility: the attacking miner needs only to control block-proposal gossip for its own won tenure (which it inherently does) and periodically issue a proposal that does not advance the chain length (e.g., resubmit an old/stale block, or a deliberately low chain-length block). No signer collusion, no majority, and no cryptographic bypass is required — it is a pure protocol-logic griefing vector directly analogous to the referenced MochiVault "deposit resets withdraw timer" bug.

### Recommendation
Do not let a *rejected*, non-advancing, or duplicate proposal from the tenure's own miner refresh `last_activity_time` indefinitely. Options:
- Only count a proposal as "activity" once per unique/distinct proposal (e.g., dedupe by proposal hash) rather than on every resend.
- Bound how many times/how often a non-advancing proposal from the same tenure can reset the inactivity clock (e.g., a monotonically increasing counter with a cap, or only accept the "activity" reset the first time a given non-advancing block is seen).
- Separate "miner is alive" from "miner is making progress" — require actual chain-length advancement (a *new*, higher block) to reset the long-horizon `block_proposal_timeout`, and use a distinct/looser signal only for the short-horizon reorg-attempt-activity window.

### Proof of Concept
1. Miner M wins tenure T and proposes/gets block B0 signed (locally accepted or pre-committed).
2. M stalls and never proposes a higher block, but every `reorg_attempts_activity_timeout` interval (default well under `block_proposal_timeout`), M re-broadcasts B0 (or another proposal with `chain_length <= B0.chain_length`) as a new `BlockProposal`/`BlockPushed` message for tenure T.
3. Each such rejected proposal reaches `check_latest_block_in_tenure`, hits the `chain_length <=` branch, and calls `signer_db.update_last_activity_time(&consensus_hash, now)` (`chainstate/mod.rs` lines 403-416).
4. `SortitionState::is_timed_out` computes `elapsed = now - last_activity` each time signers evaluate inactivity (`v1.rs`/`v2.rs`), and `elapsed` never exceeds `block_proposal_timeout` because M keeps refreshing it just under the threshold.
5. `check_miner_inactivity` in `v0/signer_state.rs` (lines 284-316) therefore never falls back to the prior tenure's miner, and the chain stalls in tenure T indefinitely, even though M never produces a single new valid block.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L390-418)
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

**File:** stacks-signer/src/chainstate/v2.rs (L46-89)
```rust
    /// Check if the sortition identified by the ConsensusHash is timed out based on
    /// the blocks within the signer db and the block proposal timeout
    pub fn is_timed_out(
        sortition: &ConsensusHash,
        signer_db: &SignerDb,
        eval: &GlobalStateEvaluator,
        local_address: &StacksAddress,
        timeout: Duration,
    ) -> Result<bool, SignerChainstateError> {
        // If we've already signed a block in this tenure, the miner can't have timed out: we have
        // committed a signature to this tenure and must not help abandon it.
        //
        // Importantly, a block we have only pre-committed to does not count! A pre-commit carries
        // no signature, and if it never reaches the pre-commit threshold the tenure can stall
        // indefinitely. Treating it as signed here would suppress the inactivity timeout for
        // exactly the signers that pre-committed, so they could never fall back to the prior
        // miner and the tenure could never recover.
        let has_block = signer_db.has_signed_block_in_tenure(sortition)?;
        if has_block {
            return Ok(false);
        }
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
