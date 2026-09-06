### Title
Miner-triggered reset of `last_activity_time` lets a single tenure's miner wedge the signer's inactivity failover — ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
The signer's miner-inactivity mechanism (`SortitionState::is_timed_out`) fails over to the prior tenure's miner only after `block_proposal_timeout` has elapsed since the current miner's "last activity." That activity timestamp, however, is refreshed by `update_last_activity_time` whenever the *current* sortition winner submits a block proposal that conflicts with (is no higher than) a block the signer has already accepted/pre-committed — even though that proposal is itself rejected. Because only the single elected miner for the tenure (a "one-slot" actor) is needed to trigger this refresh, and no majority of signers or additional privileges are required, that miner can keep re-signing and resubmitting cheap, invalid/conflicting proposals to indefinitely reset the clock. This is structurally identical to the `StakedCap.notify()` bug: a cheap, permissionless, repeatable action resets a timer (`$.lastNotify` / `last_activity_time`) that gates a downstream guarantee (`lockedProfit`/`totalAssets` vs. `is_timed_out`/failover), producing an indefinite denial of the intended fallback/liveness property.

### Finding Description
`SortitionState::is_timed_out` computes elapsed time from `last_activity_time` (falling back to the burn-block receive time) and compares it to `block_proposal_timeout`, only allowing failover to the prior miner once the tenure has been quiet for that long: [1](#0-0) 

`check_miner_inactivity` in the signer state machine drives failover strictly off this predicate: if `is_timed_out` is false, the current miner view is preserved with no other check: [2](#0-1) 

The activity timer is refreshed by `check_latest_block_in_tenure` in two places whenever the current miner's proposal is *not* higher than a block the signer already knows about — i.e. exactly the situation of a stalled/malicious miner resubmitting conflicting/invalid proposals instead of extending the tenure. First, for a block equal-or-lower than the last signed block, when the last signature is still fresh (or nonexistent): [3](#0-2) 

Second, and unconditionally on the underlying miner's honesty, whenever the incoming block conflicts with an existing pre-commit (a state that itself carries no signature) at or below the pre-committed height: [4](#0-3) 

Both call sites invoke `signer_db.update_last_activity_time(&block.header.consensus_hash, get_epoch_time_secs())`, which is the same timestamp consulted by `is_timed_out`. Since only the currently-elected miner (the "one-slot" actor for that tenure) needs to author these proposals — no cooperation from other signers or miners is required — that miner can keep the elapsed time under `block_proposal_timeout` forever by periodically re-signing and resubmitting a non-advancing block. This is the direct analog of `StakedCap.notify()`: a cheap, permissionless, repeatable call (`notify()` / proposing a conflicting block) resets a stored timestamp (`$.lastNotify` / `last_activity_time`) that a separate function (`lockedProfit()` / `is_timed_out()`) uses to gate a downstream invariant (assets become withdrawable / failover occurs), and the attacker can indefinitely suppress that invariant from ever being satisfied.

### Impact Explanation
This breaks the liveness guarantee that a wedged/malicious tenure will eventually be abandoned in favor of the prior valid miner. If the current miner never produces a valid, higher block but keeps emitting cheap conflicting/no-progress proposals just under the `block_proposal_timeout` cadence, `check_miner_inactivity` never triggers, the signer's local state machine never reverts `current_miner` to the prior tenure, and the signer never accepts extend/failover behavior for that tenure. Because this only requires the single sortition winner (no majority, no other signer key, no privileged access), it matches the specified High-impact category: "a signer wedged into never signing valid blocks."

### Likelihood Explanation
Likelihood is high for a malicious or compromised miner: the actions needed (signing and rebroadcasting a proposal that is no higher than an already-known block) are cheap, require only the miner's own signing key, and are entirely within the reach of the current tenure's single elected miner — an actor already assumed to be untrusted in the threat model (this is exactly the actor `block_proposal_timeout`/failover exists to defend against).

### Recommendation
- Short term: do not treat rejected/non-advancing/conflicting proposals as valid "activity" for purposes of resetting the inactivity timer used by `is_timed_out`. Only signed (`signed_self`/`signed_group`) progress, or another independent, unforgeable signal, should refresh `last_activity_time`.
- Long term: add invariant/liveness tests asserting that a miner who only ever resubmits non-advancing or conflicting proposals is timed out and replaced within `block_proposal_timeout`, regardless of how frequently it resubmits.

### Proof of Concept
1. Miner M wins the sortition for tenure T and becomes `ActiveMiner` in the local state machine.
2. The signer locally accepts (or pre-commits to) block B at height h in T.
3. M repeatedly signs and rebroadcasts a new proposal B' at height ≤ h (trivial: just re-sign the same or a cosmetically altered block), spaced less than `block_proposal_timeout` apart.
4. Each rejected proposal hits `check_latest_block_in_tenure`'s early-rejection or pre-commit-conflict branch and calls `signer_db.update_last_activity_time(...)` (mod.rs lines 411-416 / 442-446), resetting the clock used by `SortitionState::is_timed_out`.
5. `check_miner_inactivity` (`v0/signer_state.rs` lines 304-315) observes `is_timed_out == false` on every check and never reverts to the prior tenure's miner.
6. Tenure T never produces a valid canonical block advance, and the signer set is wedged waiting on M indefinitely — a liveness DoS triggerable by the single current-tenure miner.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L52-94)
```rust
impl SortitionState {
    /// Check if the given sortition identified by its ConsensusHash has timed out based on current signed blocks
    /// and the time at which the burn block for it was first recorded in the provided signerdb
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
