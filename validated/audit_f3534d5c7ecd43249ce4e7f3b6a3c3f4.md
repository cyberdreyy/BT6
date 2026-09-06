## Circumvention of the Miner-Inactivity Timeout via Self-Refreshing "Last Activity" State - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
The signer's per-tenure inactivity/failover guard (`is_timed_out`) is driven by a single shared timestamp, `tenure_activity.last_activity_time`, keyed only by `consensus_hash`. This timestamp is refreshed not only by legitimate block progress, but also by **any** rejected/duplicate proposal that merely collides in height with a block the signer has *only pre-committed to* (never actually signed). Because "activity" is defined this way, a single miner — the one-slot actor who won the current tenure — can keep resubmitting cheap, never-completed proposals to perpetually refresh this shared timestamp and prevent `is_timed_out`/`check_miner_inactivity` from ever firing, indefinitely blocking the fallback to a legitimate prior miner even though the current miner has never actually produced a signed block.

### Finding Description
`SortitionData::check_latest_block_in_tenure` in [1](#0-0)  updates `last_activity_time` for a tenure whenever an incoming proposal collides with a block that this signer has only *pre-committed* to (state `PreCommitted`, no signature). This is distinct from the "fresh signed tip" branch above it, which is guarded by an actual signature (`get_last_signed_block`).

This shared, tenure-keyed timestamp is written via `SignerDb::update_last_activity_time` and read via `get_last_activity_time`, both keyed only by `consensus_hash` — a single value overwritten by whichever proposal happens to trigger the check most recently, with no distinction between "the miner is genuinely trying to make progress" and "the miner is stalling": [2](#0-1) .

The timer is consumed by `SortitionState::is_timed_out` (v1: [3](#0-2) , v2 equivalent in `chainstate/v2.rs`), which first checks `has_signed_block_in_tenure` and, if no block has ever been signed for this tenure, falls back to comparing `now - last_activity_time` against `block_proposal_timeout`. `check_miner_inactivity` in [4](#0-3)  is the sole mechanism that reverts the state machine to the prior (canonical) miner when the current miner has gone dark.

Because the "fresh pre-commit" branch of `check_latest_block_in_tenure` counts *any* height-colliding proposal as activity — even one this signer only locally pre-committed, never signed, and that will never reach the group threshold — a single miner can:
1. Propose block `P1` (gets locally pre-committed by signers, per-signer, without needing group consensus).
2. Repeatedly propose further blocks `P2, P3, ...` at height ≤ `P1`. Each is rejected on the height check, but each rejection still calls `update_last_activity_time` because `P1` is a *fresh pre-commit*, not a signed block.
3. Never let any proposal accumulate the 70% pre-commit/signature weight needed to actually finish a block.

Every signer independently runs this same logic on the same broadcast proposals, so all of them refresh their own `last_activity_time` in lock-step — no cross-signer coordination or majority is needed on the attacker's side. `has_signed_block_in_tenure` never becomes true (no block was ever actually signed), yet `is_timed_out` never fires either, because `last_activity_time` is perpetually fresh. `check_miner_inactivity`'s revert-to-prior-miner path (lines 317-354, same file) is consequently never reached.

### Impact Explanation
This wedges the signer set's own designed recovery mechanism: `check_miner_inactivity` exists specifically to fall back to a valid prior miner when the current miner stalls without producing a signed block — the changelog entry [5](#0-4)  shows the developers already fixed one variant of this exact class of bug (treating a pre-commit as a signed block suppressed the timeout). The pre-commit-collision "activity" branch reintroduces the same class of stall: a malicious current-tenure miner can indefinitely prevent signers from reverting to the prior miner, halting chain progress until a fresh Bitcoin burn block arrives (a new sortition), which is a liveness wedge — signers are stuck honoring a miner that never signs a valid block.

### Likelihood Explanation
Triggerable by the single miner who won the current tenure's sortition slot (in-scope: "a one-slot miner ... can trigger"). It requires no majority of signers, no other signer's key, and no node/auth access — only the ability to broadcast a stream of block proposals over `.miners` StackerDB, which is exactly the channel a winning miner already uses.

### Recommendation
Do not let a proposal that merely collides in height with a locally *pre-committed but never signed* block count as "activity" toward the inactivity timeout, or bound the number/rate of such refreshes per tenure so a miner cannot indefinitely postpone `is_timed_out`. Consider requiring that "activity" for the purpose of suppressing the inactivity fallback be tied to progress that a threshold of the signer set has observed (e.g., pre-commit weight crossing some minimum), rather than a purely local, per-signer, per-proposal timestamp update.

### Proof of Concept
1. Miner wins tenure `T`, proposes block `P1` at height `h`; some signers locally validate/pre-commit `P1` (state `PreCommitted`, `approved_time` set) but `P1` never reaches the 70% pre-commit or signature threshold.
2. Miner proposes `P2` at height `h` (or lower) in tenure `T`. For every signer that pre-committed `P1`, `check_latest_block_in_tenure` ( [1](#0-0) ) sees `is_fresh_pre_commit == true` and `block.header.chain_length <= info.block.header.chain_length`, and calls `update_last_activity_time(&T, now)`.
3. Repeat step 2 with `P3, P4, ...` before `tenure_last_block_proposal_timeout`/`block_proposal_timeout` elapses each time.
4. `has_signed_block_in_tenure(T)` remains `false` throughout (no block was ever actually signed), but `get_last_activity_time(T)` is always recent, so `SortitionState::is_timed_out` ( [6](#0-5) ) never returns `true`, and `check_miner_inactivity` never reverts to the prior miner — the tenure stalls indefinitely under the attacker's control.

### Citations

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

**File:** stacks-signer/src/v0/signer_state.rs (L284-374)
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

        // Only revert to the prior miner if its tenure is the canonical Stacks tip's
        // tenure. A miner only continues (extends) a tenure it won, so if the canonical
        // tip is in some other tenure due to a Bitcoin reorg orphaning the prior
        // sortition's tenure, the prior miner's node has already stopped mining and
        // will never propose again.
        let stacks_tip_ch = client.get_peer_info()?.stacks_tip_consensus_hash;
        if sortition_data.consensus_hash != stacks_tip_ch {
            warn!(
                "Signer State: Current miner timed out due to inactivity, but the canonical stacks tip is not in the prior miner's tenure, so the prior miner cannot continue it. Allowing current miner to continue";
                "stacks_tip_consensus_hash" => %stacks_tip_ch,
                "prior_sortition_consensus_hash" => %sortition_data.consensus_hash,
            );
            return Ok(());
        }

        if !last_sortition.is_tenure_valid(db, client, proposal_config, eval)? {
            warn!("Signer State: Current miner timed out due to inactivity, but prior miner is not valid. Allowing current miner to continue");
            return Ok(());
        }
        let new_active_tenure_ch = &sortition_data.consensus_hash;
        let inactive_tenure_ch = tenure_id.clone();
        state_machine.current_miner = Self::make_miner_state(
            sortition_data.clone(),
            client,
            db,
            proposal_config.tenure_last_block_proposal_timeout,
        )?;
        info!(
            "Signer State: Current tenure timed out, setting the active miner to the prior tenure";
            "inactive_tenure_ch" => %inactive_tenure_ch,
            "new_active_tenure_ch" => %new_active_tenure_ch
        );

        crate::monitoring::actions::increment_signer_agreement_state_change_reason(
            crate::monitoring::SignerAgreementStateChangeReason::InactiveMiner,
        );

        Ok(())
    }
```

**File:** stacks-signer/changelog.d/precommit-suppresses-miner-timeout.fixed (L1-1)
```text
Do not let a block that was only pre-committed suppress the miner inactivity timeout. A pre-commit was treated as a signed block, so signers that pre-committed to a tenure-start block which never reached the pre-commit threshold could never time the miner out and fall back to the prior tenure, stalling the chain until the next burn block.
```
