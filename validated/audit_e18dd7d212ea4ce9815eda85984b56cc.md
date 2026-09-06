### Title
Miner can indefinitely refresh its own inactivity timer with rejected/reorging proposals, freezing signer fallback to a live prior miner - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
The external report's bug class is: a permissionless actor can repeatedly invoke a function that resets a gating timestamp (`lastProfitTime`), and that timestamp reset itself blocks a time-based state transition (profit withdrawal) that should otherwise become available. The analogous mechanism in this repo is `SortitionData::check_latest_block_in_tenure`, which resets the miner's `last_activity_time` any time it receives a *stale/reorging* block proposal from the current tenure's miner, and that same `last_activity_time` is the sole input to `SortitionState::is_timed_out` (v1/v2), which gates whether the signer set will ever fall back to a live prior miner via `check_miner_inactivity`.

### Finding Description
`check_latest_block_in_tenure` (`stacks-signer/src/chainstate/mod.rs:376-419`) is invoked on every block proposal a signer receives (via `check_block_against_state`/`check_block_against_signer_db_state` in `stacks-signer/src/v0/signer.rs`), which is fully attacker (miner)-controlled and permissionless from the signer's perspective — a signer cannot refuse to *look at* a proposal from the current sortition winner.

When the proposal does not advance the chain (`block.header.chain_length <= info.block.header.chain_length` — i.e. it's a reorg/resend attempt), instead of only rejecting it, the code does: [1](#0-0) 

```
if block.header.chain_length <= info.block.header.chain_length {
    ...
    if info.signed_group.is_none_or(|signed_time| {
        signed_time + reorg_attempts_activity_timeout.as_secs() > get_epoch_time_secs()
    }) {
        // ... count this invalid block proposal towards valid miner activity.
        signer_db.update_last_activity_time(&block.header.consensus_hash, get_epoch_time_secs())
    }
    return Ok(false);
}
```

This `update_last_activity_time` call is exactly the "reset the freeze clock" primitive from the vault bug: it is triggered by a message the attacker fully controls (a stale/invalid/conflicting block proposal), and it stamps the current wall-clock time into the very field that `is_timed_out` reads to decide whether the miner has gone inactive: [2](#0-1) 

`is_timed_out` computes `elapsed` as `now - last_activity` (falling back to burn-block receive time only if `last_activity_time` was never set), and only flips to inactive once `elapsed > block_proposal_timeout`. The v2 path (`stacks-signer/src/chainstate/v2.rs:48-89`) is structurally identical.

`check_miner_inactivity` in `stacks-signer/src/v0/signer_state.rs:284-374` is the *only* mechanism that lets the signer set abandon a stuck/unresponsive current-tenure miner and fall back to a still-live prior miner (`make_miner_state(prior sortition)`), which in turn is what allows tenure progress to continue when the current winner stalls. Because `is_timed_out` short-circuits to `Ok(false)` as long as `elapsed <= block_proposal_timeout`, a miner that keeps `last_activity_time` fresh never becomes eligible for this fallback, no matter how long it withholds a genuinely valid, chain-advancing block.

The equivalence to the report:
- Vault: `harvest()` (permissionless) → `depositProfitTokenForUsers()` → sets `lastProfitTime = block.timestamp` → blocks `withdrawProfit()`'s `block.timestamp <= lastProfitTime` check.
- Signer: proposal from the sortition-winning miner (attacker-controlled, no majority needed) → `check_latest_block_in_tenure` → `update_last_activity_time(now)` → blocks `is_timed_out`'s `elapsed > block_proposal_timeout` check, wedging `check_miner_inactivity`'s fallback path.

### Impact Explanation
This is a liveness wedge on the *tenure fallback* mechanism: a single sortition-winning miner can indefinitely suppress the signer set's ability to demote it and adopt a live prior miner, by periodically re-sending an old/rejected/reorging block proposal (something it can do at will, with no cost and no cooperation from any other party) purely to keep pinging `update_last_activity_time`. While the miner never gets a signature on an invalid block (so this is not a Critical safety break), it denies block production/tenure progress for as long as it keeps refreshing the timer, which matches the High-impact category: "a signer wedged into never signing valid blocks" during that tenure, since the fallback path that would otherwise let the signer set continue producing blocks through a different miner is neutralized.

Note there is a partial code-level mitigation already present: the refresh only happens when `info.signed_group` is `None` (i.e., only-locally-accepted) or fresh within `reorg_attempts_activity_timeout` (default 3s per `mainnet-signer-conf.toml`). This narrows — but does not eliminate — the window: the miner must resend proposals frequently enough (faster than `reorg_attempts_activity_timeout`, and definitely faster than `block_proposal_timeout`) to keep refreshing, which is entirely within a malicious miner's control since it is the one crafting and timing the resends.

### Likelihood Explanation
Reachable by a single actor: the current sortition-winning miner, using only gossip-style block-proposal resubmission it already controls, with no need for another signer's key, a majority of signers, or node/auth access. The trigger condition (a proposal whose chain_length does not exceed the last known block in the tenure) is trivial to produce — it's just re-sending the previous or an older block, which is normal miner behavior mistaken for legitimate resend/retry traffic, making this both cheap and hard to distinguish from benign resubmission.

### Recommendation
Do not let a chain-non-advancing/rejected proposal refresh the same `last_activity_time` clock that gates the inactivity/fallback decision indefinitely. Options: cap the number of times or total duration a single "no-progress" proposal signature can extend `last_activity_time` within a tenure, or track "genuine progress" activity (e.g., only proposals with unique/higher chain_length, or a bounded number of resend credits) separately from the timeout clock used by `is_timed_out`, so a miner cannot use resends of *the same or an already-rejected* block to indefinitely stave off the fallback check.

### Proof of Concept
Conceptual sequence (mirrors the harvest()-freezing PoC structure):
1. Sortition awards tenure to miner M. M proposes/gets one block accepted, then stalls (withholds further valid blocks).
2. Before `block_proposal_timeout` elapses, M crafts and (re)broadcasts a stale/old block proposal at the same or lower `chain_length` as the tenure's last known block.
3. Each such proposal triggers `check_latest_block_in_tenure` → `update_last_activity_time(now)` (`stacks-signer/src/chainstate/mod.rs:411-416`), resetting the clock read by `SortitionState::is_timed_out`.
4. M repeats step 2 at an interval shorter than `block_proposal_timeout` (and within `reorg_attempts_activity_timeout` of the last signed_group time) indefinitely.
5. `check_miner_inactivity` (`stacks-signer/src/v0/signer_state.rs:290-315`) never observes `is_timed_out == true`, so the signer set never falls back to the prior, live miner — tenure progress is wedged as long as M keeps resending. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L395-418)
```rust
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

**File:** stacks-signer/src/chainstate/v1.rs (L55-93)
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
```

**File:** stacks-signer/src/v0/signer_state.rs (L284-315)
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
