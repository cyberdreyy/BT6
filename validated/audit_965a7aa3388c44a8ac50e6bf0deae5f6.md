### Title
Griefing extension of miner inactivity timeout via repeated conflicting/reorg-looking proposals - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
`check_latest_block_in_tenure` (invoked from `check_block_against_signer_db_state` on every proposal) resets a signer's `last_activity_time` for the *current miner's own tenure* whenever a conflicting/lower-height block proposal arrives, as long as the tenure's highest known block is not yet stale by `reorg_attempts_activity_timeout`. Because this reset fires unconditionally on receipt of *any* signed proposal that fails the height check — before any group-level rejection tally happens — the sole miner holding the current tenure can keep resending cheap, invalid/stale proposals to indefinitely postpone `is_timed_out()` (in `chainstate/v1.rs`/`chainstate/v2.rs`), preventing the signer set from ever falling back to a valid alternate miner, stalling block production. This is structurally analogous to Velodrome's `notifyRewardAmount`, where any caller could restart an active reward period with a dust amount before it expired, indefinitely extending the payout window.

### Finding Description
`check_latest_block_in_tenure` in `stacks-signer/src/chainstate/mod.rs` compares an incoming block proposal's `chain_length` against the tenure's last known signed/pre-committed block: [1](#0-0) 

- If the proposal does not confirm enough blocks (`chain_length <= info.block.header.chain_length`), and the last known block is not stale past `reorg_attempts_activity_timeout` (or has no `signed_group` timestamp at all, i.e. only locally accepted), the code calls `signer_db.update_last_activity_time(&block.header.consensus_hash, get_epoch_time_secs())` and returns `Ok(false)` (fails the check / proposal rejected).
- The same reset also fires for a "fresh pre-commit" conflict via the second branch (lines 422-448 in the same function).

`update_last_activity_time`/`get_last_activity_time` simply upsert/read a per-tenure timestamp: [2](#0-1) 

That timestamp is the sole input to the liveness check in both chainstate versions: [3](#0-2) [4](#0-3) 

`check_miner_inactivity` in `stacks-signer/src/v0/signer_state.rs` only falls back to the prior/alternate miner once `SortitionState::is_timed_out` returns true: [5](#0-4) 

The one deployed mitigation against a miner abusing reorg-looking rejections — marking the miner invalid once ≥30% of signers reject with `ReorgNotAllowed` — is explicitly skipped for the current global-state signer protocol: [6](#0-5) 
and documented as such: [7](#0-6) 

Crucially, `update_last_activity_time` executes locally and unconditionally at proposal-check time, *before* any group tally of rejections occurs — so even where the 30% mitigation exists (older, non-global-state protocol paths), it cannot prevent the activity-timer reset itself; it can only, after the fact, flag the miner invalid once enough rejections accumulate. Under the current global-state protocol version, that after-the-fact safety net is entirely disabled, so nothing stops the reset.

### Impact Explanation
The active miner that legitimately won a tenure (a single, one-slot actor) can, after getting one block pre-committed/signed as required to seed `info` in `get_tenure_last_block_info`/`get_last_accepted_block`, stop producing further valid blocks and instead repeatedly gossip cheap, self-signed proposals with a stale/duplicate `chain_length`. Each such proposal is rejected by `check_latest_block_in_tenure`, but as a side effect resets `last_activity_time` for that tenure on every signer that evaluates it. As long as these are sent faster than `reorg_attempts_activity_timeout` (200s default) — or unconditionally while the tenure's highest block has no `signed_group` timestamp — `is_timed_out()` never returns true, so `check_miner_inactivity` never falls back to the prior miner. This wedges the whole signer set into never accepting a tenure extend from, or switching to, any alternate/valid miner, halting chain progress. This matches the "signer wedged into never signing valid blocks" High-severity liveness class, achieved by a single miner without needing a majority of signers, another signer's key, or auth-token access.

### Likelihood Explanation
The attack requires only: (1) winning a sortition (an ordinary, expected event for any miner), (2) getting a single block pre-committed/signed early in the tenure (normal behavior), and (3) thereafter broadcasting self-signed, intentionally stale-height `BlockProposal` messages over the `.miners` StackerDB slot at a modest cadence. No cryptographic breaks, no majority collusion, and no elevated privileges are needed — only the miner's own mining key, which an active miner already possesses. The gate that could have neutralized this (the 30% `ReorgNotAllowed` mark-invalid path) is explicitly disabled once the fleet runs the global-state signer protocol, which is the direction the codebase is moving, making the wedge fully live under that configuration.

### Recommendation
Bound how many times/how long a single tenure's `last_activity_time` can be pushed forward purely by conflicting/rejected proposals from the tenure's own miner without an actual newly signed or globally-accepted block. For example: only allow the "count as miner activity" reset once per proposal *content* (dedupe by block hash) or cap the cumulative extension per tenure independent of `reorg_attempts_activity_timeout`, and reinstate an equivalent to the ≥30%-`ReorgNotAllowed`-rejection invalidation path for global-state protocol versions rather than skipping it outright.

### Proof of Concept
1. Miner M wins sortition for tenure T, proposes block B0 (chain_length = k), which reaches pre-commit/signature threshold — `get_tenure_last_block_info`/`get_last_accepted_block` now return B0 for T.
2. M stops proposing genuinely new blocks. Instead, every `< reorg_attempts_activity_timeout` seconds, M signs and gossips a new `BlockProposal` for tenure T with `chain_length <= k` (a trivially crafted "reorg" of its own last block).
3. Each signer's `check_block_against_signer_db_state` → `check_latest_block_in_tenure` sees `chain_length <= info.block.header.chain_length`, hits the branch at `stacks-signer/src/chainstate/mod.rs:395-419`, and calls `update_last_activity_time(&block.header.consensus_hash, now)` before rejecting the proposal.
4. `SortitionState::is_timed_out` (`chainstate/v1.rs`/`v2.rs`) computes `elapsed = now - last_activity`, which never exceeds `block_proposal_timeout` because step 3 keeps refreshing `last_activity`.
5. `check_miner_inactivity` (`v0/signer_state.rs:284-315`) therefore never falls back to the prior sortition's miner, and no alternate miner's tenure extend is ever accepted, so the tenure is wedged and chain height stalls indefinitely as long as M keeps sending these cheap proposals.

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

**File:** stacks-signer/src/signerdb.rs (L2248-2271)
```rust
    /// Update the tenure (identified by consensus_hash) last activity timestamp
    pub fn update_last_activity_time(
        &mut self,
        tenure: &ConsensusHash,
        last_activity_time: u64,
    ) -> Result<(), DBError> {
        debug!("Updating last activity for tenure"; "consensus_hash" => %tenure, "last_activity_time" => last_activity_time);
        self.db.execute("INSERT OR REPLACE INTO tenure_activity (consensus_hash, last_activity_time) VALUES (?1, ?2)", params![tenure, u64_to_sql(last_activity_time)?])?;
        Ok(())
    }

    /// Get the last activity timestamp for a tenure (identified by consensus_hash)
    pub fn get_last_activity_time(&self, tenure: &ConsensusHash) -> Result<Option<u64>, DBError> {
        let query =
            "SELECT last_activity_time FROM tenure_activity WHERE consensus_hash = ? LIMIT 1";
        let Some(last_activity_time_i64) = query_row::<i64, _>(&self.db, query, &[tenure])? else {
            return Ok(None);
        };
        let last_activity_time = u64::try_from(last_activity_time_i64).map_err(|e| {
            error!("Failed to parse db last_activity_time as u64: {e}");
            DBError::Corruption
        })?;
        Ok(Some(last_activity_time))
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

**File:** stacks-signer/src/v0/signer.rs (L2342-2353)
```rust
        // NOTE: This is only used by active signer protocol versions < Global state activation
        // If 30% of the signers have rejected the block due to an invalid
        // reorg, mark the miner as invalid.
        // If we cannot determine the active signer protocol version it means we are
        // running a global state machine version that couldn't reach consensus, so we can skip this check
        if self
            .determine_active_signer_protocol_version()
            .map(|version| version.uses_global_state())
            .unwrap_or(true)
        {
            return;
        };
```

**File:** docs/signer-flows.md (L377-383)
```markdown
The outdated-peer fallback keeps mixed-version fleets live: an acceptance from a
peer that never sent a pre-commit is routed into the pre-commit path instead, so
that peer's weight still counts toward the threshold that produces _our_
signature. Note that reaching 70% signatures still only marks the block
_locally_ accepted with the group timestamp; global acceptance waits for the node
to adopt it. Marking the miner invalid on a 30% `ReorgNotAllowed` rejection is
skipped once the active protocol version uses global signer state.
```
