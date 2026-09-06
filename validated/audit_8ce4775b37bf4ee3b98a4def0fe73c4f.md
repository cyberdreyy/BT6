### Title
Griefing signer liveness fallback via repeated cheap reorg-attempt proposals resetting `last_activity_time` - ([File: stacks-signer/src/chainstate/mod.rs], [File: stacks-signer/src/chainstate/v1.rs], [File: stacks-signer/src/chainstate/v2.rs])

### Summary
The signer's inactivity/timeout mechanism (`SortitionState::is_timed_out`), which is the safety valve that lets signers fall back to a prior valid miner when the current tenure's miner goes silent, is driven by `last_activity_time` for that tenure. The documented flow shows that a rejected reorg-attempt block proposal — cheap, permissionless, and requiring only a valid sortition-winning miner key (a "one-slot" actor) — still calls `update_last_activity_time` on the target tenure even though the proposal itself is rejected. This mirrors the Uniswap report's pattern: a permissionless, cheap action (`sync()`) resets state (`blockTimestampLast`) that a security-relevant equality check (`block.timestamp == blockTimestampLast`) depends on, defeating the check whenever the attacker wants. Here, a reorg-doomed proposal resets `last_activity_time`, defeating the `elapsed > block_proposal_timeout` check that `is_timed_out` depends on.

### Finding Description
`is_timed_out` (present in both v1 and v2 chainstate) computes liveness purely from elapsed time since `last_activity_time`: [1](#0-0) [2](#0-1) 

`check_miner_inactivity` (v0 signer state) calls this for the *current* `ActiveMiner`'s `tenure_id`, and only falls back to the prior sortition's miner if `is_timed_out` returns `true`: [3](#0-2) 

Per the repo's own documented data-flow (`docs/signer-flows.md` § 7), when a proposal fails `check_latest_block_in_tenure` because a fresh *signed* tip already exists in that tenure and the new proposal is not higher, the rejection path is explicitly annotated:

"`LSB -- yes, and proposal not higher --> RA: fails the check (a reorg attempt within reorg_attempts_activity_timeout still counts as miner activity: update_last_activity_time)`" [4](#0-3) 

That is, a *rejected* reorg-attempt proposal still updates `last_activity_time` for the targeted tenure. This is the same class of bug as the referenced Uniswap issue: a cheap, gossip-only action from a single legitimate actor (any address holding a valid miner key that has won some sortition — i.e., a "one-slot miner") resets timing state that a downstream equality/threshold check (`elapsed > block_proposal_timeout`) relies on, without that action itself needing to succeed or be accepted.

The `reorg_attempts_activity_timeout` window is meant to tolerate legitimately late-arriving proposals so a genuinely active but slow miner is not penalized, per the config documentation: [5](#0-4) 

But because the update fires on *any* reorg-attempt proposal within the window — not only ones from a plausibly-live, honest miner — a malicious or colluding miner-key holder can keep re-submitting a proposal that is guaranteed to be rejected (it only needs to be validly signed and reference the tenure being reorged) purely to keep resetting `last_activity_time` on a tenure whose actual miner has gone silent. Each such message is cheap (StackerDB gossip, no on-chain cost, no majority needed) and can be repeated indefinitely, similar to repeatedly front-running with `sync()`.

### Impact Explanation
This breaks the liveness guarantee the fallback mechanism is meant to provide: "High - a signer wedged into never signing valid blocks." If `last_activity_time` for the stalled tenure can be perpetually refreshed by a single miner-key holder's cheap, rejected messages, `is_timed_out` never returns `true`, `check_miner_inactivity` never falls back to the prior valid miner, and the signer set is wedged: it keeps waiting on a miner that will never produce a block, halting chain progress (a liveness wedge) rather than degrading gracefully to the documented fallback behavior.

### Likelihood Explanation
The action needed — submitting a validly-signed but doomed-to-be-rejected reorg/tenure-change proposal via StackerDB — requires only holding a miner key that has won at least one sortition (the "one-slot miner" precondition explicitly permitted by scope), no majority of signers, no other signer's key, and no elevated access. It can be repeated at negligible cost every `reorg_attempts_activity_timeout` window, making sustained griefing straightforward for a single such actor.

### Recommendation
Do not let a *rejected* proposal (particularly one rejected specifically because it is an impermissible reorg attempt) refresh `last_activity_time` for the tenure being defended. Reserve `update_last_activity_time` for proposals that are plausibly legitimate activity from the tenure's own miner (e.g., proposals that at least pass more basic identity/consensus-hash checks), or bound the number/frequency of activity-refresh events a given proposer can generate per tenure, so `is_timed_out` cannot be indefinitely suppressed by a single actor's cheap, failing messages.

### Proof of Concept
1. Miner A wins sortition and starts a tenure but stops producing blocks (goes silent or is malicious).
2. A separate entity that controls a valid miner key which has won some sortition (could be Miner A itself, or an accomplice miner from a later sortition) repeatedly crafts and gossips a validly-signed tenure-change/reorg block proposal targeting A's tenure.
3. Each such proposal is evaluated by `check_latest_block_in_tenure`; because a fresh signed tip already exists and the proposal is not higher, it is rejected — but per the documented flow, `update_last_activity_time` is still invoked for A's tenure.
4. `SortitionState::is_timed_out` for A's tenure keeps observing a recent `last_activity_time`, so `elapsed > block_proposal_timeout` never holds.
5. `check_miner_inactivity` never triggers the fallback to the prior sortition's miner, and the signer set remains wedged on A's stalled tenure indefinitely, as long as the griefing party resubmits before each `reorg_attempts_activity_timeout` window elapses.

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

**File:** stacks-signer/src/v0/signer_state.rs (L284-334)
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

        // the tenure timed out, try to see if we can use the prior tenure instead
        let CurrentAndLastSortition { last_sortition, .. } =
            client.get_current_and_last_sortition()?;
        let Some(last_sortition) = last_sortition
            .and_then(|val| SortitionData::try_from(val).ok())
            .map(|data| SortitionState::new(version, data))
        else {
            warn!("Signer State: Current miner timed out due to inactivity, but could not find a valid prior miner. Allowing current miner to continue");
            return Ok(());
        };

        let sortition_data = last_sortition.data();
        // If we already reverted to the last sortition miner, don't time it out as it means we have already timed out the current sorititon miner
        // as there is no other miner available.
        if &sortition_data.consensus_hash == tenure_id {
            warn!("Signer State: Last sortition miner has timed out, but no prior valid miner. Allowing last sortition miner to continue");
            return Ok(());
        }
```

**File:** docs/signer-flows.md (L406-411)
```markdown
    SAME --> CLB["check_latest_block_in_tenure(tenure_id)"]
    CLB --> LSB{"fresh SIGNED tip in that tenure?<br/>get_tenure_last_block_info =<br/>get_last_signed_block + freshness from<br/>the last signature time<br/>(tenure_last_block_proposal_timeout)"}
    LSB -- "yes, and proposal not higher" --> RA["fails the check<br/>(a reorg attempt within<br/>reorg_attempts_activity_timeout still<br/>counts as miner activity:<br/>update_last_activity_time)"]:::bad
    LSB -- "no signed tip, or proposal higher" --> CARVE{"fresh PRE-COMMITTED block<br/>at ≥ this height?<br/>get_last_accepted_block"}
    CARVE -- yes --> ACT["count miner activity only —<br/>a pre-commit never vetoes<br/>update_last_activity_time"]
    CARVE -- no --> NODE
```

**File:** sample/conf/signer/mainnet-signer-conf.toml (L137-142)
```text
# Time to wait for the last block of a tenure to be globally accepted
# or rejected before considering a new miner's block at the same height
# as potentially valid.
# Default: 30
# Units: seconds
# tenure_last_block_proposal_timeout_secs = 30
```
