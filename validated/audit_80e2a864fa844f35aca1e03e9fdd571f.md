### Title
Miner can indefinitely suppress the inactivity timeout by resubmitting non-progressing block proposals - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
`SortitionData::check_latest_block_in_tenure` refreshes a tenure's `last_activity_time` whenever a *rejected*, non-progressing (same-or-lower `chain_length`) block proposal arrives, as long as the tenure's last signed block has not yet reached global acceptance. Because `SortitionState::is_timed_out` (the only signal used to detect an inactive/malicious miner and hand control back to the prior tenure) measures elapsed time from this same `last_activity_time`, the current tenure's miner can keep resubmitting cheap, rejected proposals to reset the clock forever — exactly the TraderJoe `FeeHelper` pattern where a "no-op" operation nonetheless bumps the timestamp that a decay/timeout check depends on.

### Finding Description
`check_latest_block_in_tenure` looks up the highest known signed block for the tenure (`get_tenure_last_block_info`) and, if the newly proposed block does not extend past it (`block.header.chain_length <= info.block.header.chain_length`), rejects the proposal early — but first conditionally bumps the activity clock: [1](#0-0) 

The gating condition is `info.signed_group.is_none_or(|signed_time| signed_time + reorg_attempts_activity_timeout.as_secs() > get_epoch_time_secs())`. `Option::is_none_or` returns `true` when the option is `None`. That means whenever the last known signed block in the tenure has only been **locally/self-signed** (not yet globally accepted, so `signed_group` is `None`), *every* subsequent conflicting/duplicate/lower-height proposal from that same miner refreshes `last_activity_time` — with no bound at all, not even `reorg_attempts_activity_timeout`. The refreshed timestamp is written unconditionally via `SignerDb::update_last_activity_time`: [2](#0-1) 

That same value is the sole input to the inactivity check used in both chainstate versions: [3](#0-2) [4](#0-3) 

`is_timed_out` is what `check_miner_inactivity` relies on to decide whether to revert control to the prior tenure's miner: [5](#0-4) 

`check_latest_block_in_tenure` is reached from `SortitionsView::check_proposal` on every non-tenure-change proposal via `confirms_latest_block_in_same_tenure`, whose failure path returns `RejectReason::InvalidParentBlock` — i.e., the proposal is explicitly rejected, no signature is ever produced for it: [6](#0-5) 

This is structurally identical to the TraderJoe `FeeHelper.updateVariableFeeParameters` bug: a call that does *not* meaningfully progress the protected state (there, the volatility reference; here, the tenure/chain length) still updates the timestamp (`_fp.time` / `last_activity_time`) that governs a time-based reset/decay/timeout (`is_timed_out`). An entity that can trigger the no-op path cheaply and repeatedly can keep the timer from ever expiring.

### Impact Explanation
The miner that won the current tenure's sortition is a single actor (the "one-slot miner" the rules describe). By locally getting even one proposal locally/self-signed (a routine, expected event in normal tenure operation) and then repeatedly broadcasting additional non-progressing proposals (e.g., re-proposing at the same or a stale `chain_length`) before `block_proposal_timeout` elapses, this miner keeps `is_timed_out` returning `false` indefinitely. Consequently:
- `check_miner_inactivity` never falls back to the prior tenure's miner (`v0/signer_state.rs:313-373`).
- The signer set is wedged waiting on a miner that never produces a valid, chain-extending block.
- No majority of other signers, no other signer's key, and no `auth_token`/local access are required — only the tenure-winning miner's own mining key and normal StackerDB gossip.

This matches the "High" impact bucket: a signer wedged into never signing valid blocks / never falling back from a stalled miner, purely due to a broken equality between "activity that indicates real progress" and "activity that merely resets a timer."

### Likelihood Explanation
Likelihood is high for any miner that wants to stall the network (e.g., to prevent handoff to a competing miner, or to grief the chain) since the trigger conditions are ordinary: get one locally-signed (not yet globally-accepted) block in the tenure, then keep sending trivially rejected re-proposals at an interval shorter than `block_proposal_timeout`. No cooperation from other signers or the honest majority is needed, and the cost is just gossiping already-rejected block headers.

### Recommendation
- Only bump `last_activity_time` when the proposal represents genuine forward progress (a higher `chain_length`, a tenure-change, or an accepted/signed outcome), not merely because a conflicting/rejected proposal arrived.
- If activity credit must be given for legitimate reorg attempts, bound it uniformly by `reorg_attempts_activity_timeout` regardless of whether `signed_group` is `Some` or `None`, rather than treating the `None` case as unconditionally "fresh."
- Consider deriving `is_timed_out` from a monotonic count of *meaningfully distinct* proposals/signed states rather than a freely-resettable wall-clock timestamp that any rejected message can refresh.

### Proof of Concept
1. Miner M wins the sortition for tenure T.
2. M proposes block B1 extending the tenure; some signers self-sign B1 (`signed_self` set) but it has not yet reached global acceptance, so `signed_group` is `None` for the tenure's last block info as returned by `get_tenure_last_block_info` (`chainstate/mod.rs:330-364`).
3. M repeatedly re-broadcasts a proposal B2 with `chain_length <= B1.chain_length` (e.g., resubmits B1 itself, or any stale/duplicate header) at intervals shorter than `block_proposal_timeout`.
4. Each time, `check_latest_block_in_tenure` takes the `info.signed_group.is_none_or(...)` branch (true, since `signed_group == None`) and calls `signer_db.update_last_activity_time(&block.header.consensus_hash, get_epoch_time_secs())` (`chainstate/mod.rs:403-417`), even though the function ultimately returns `Ok(false)` and the proposal is rejected (`RejectReason::InvalidParentBlock`, `chainstate/v1.rs:336-338`).
5. `SortitionState::is_timed_out` recomputes `elapsed` from this repeatedly-refreshed `last_activity_time` (`chainstate/v1.rs:76-93`) and never exceeds `block_proposal_timeout`.
6. `check_miner_inactivity` therefore never executes the fallback to the prior tenure's miner (`v0/signer_state.rs:313-326`), and the signer set remains wedged on miner M indefinitely while M never produces a chain-extending, signable block.

### Citations

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

**File:** stacks-signer/src/signerdb.rs (L2248-2257)
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

**File:** stacks-signer/src/chainstate/v1.rs (L327-339)
```rust
        } else {
            // check if the new block confirms the last block in the current tenure
            let confirms_latest_in_tenure = SortitionData::confirms_latest_block_in_same_tenure(
                block,
                signer_db,
                client,
                &self.config,
            )
            .map_err(SignerChainstateError::from)?;
            if !confirms_latest_in_tenure {
                return Err(RejectReason::InvalidParentBlock);
            }
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

**File:** stacks-signer/src/v0/signer_state.rs (L284-326)
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
```
