This directly disproves the hypothesized vulnerability. `handle_block_proposal` at `stacks-signer/src/v0/signer.rs:1582-1589` explicitly checks `block_proposal.reward_cycle != self.reward_cycle` and returns early ("We are not signing for this reward cycle. Ignore the block.") before any weight-based validation occurs. Each `BlockProposal` carries its own `reward_cycle` field set by the block's own sortition/tenure — it isn't inferred from `current_reward_cycle_info` at delivery time. So a stale-cycle `Signer` instance (self.reward_cycle == N) can never process a proposal tagged with cycle N+1: the exact equality the question worries about is enforced per-message, per-signer, independent of the runloop's `current_reward_cycle` variable.

The dedicated regression tests `outgoing_signers_ignore_block_proposals` and `incoming_signers_ignore_block_proposals` at `stacks-node/src/tests/signer/v0/mod.rs:6077` and `:6249` exist specifically to assert this boundary behavior: outgoing-cycle signers must not sign blocks proposed for the incoming cycle and vice versa. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

The `current_reward_cycle` parameter passed into `process_event` only gates whether a *future*-cycle signer (`self.reward_cycle > current_reward_cycle`) processes non-status/non-burn-block events; it never causes a *past*-cycle signer to accept proposals tagged for a newer cycle, because `handle_block_proposal`'s own guard operates on the proposal's embedded `reward_cycle` field compared against `self.reward_cycle`, not against the runloop's possibly-lagging `current_reward_cycle`. Since `self.reward_cycle` is fixed at construction to match exactly the reward set/weights (`signer_weights`) that were fetched for that cycle, there is no window where a proposal for cycle N+1 is matched against cycle N's weights — the proposal is simply dropped by the stale-cycle signer, and the correctly-configured signer for cycle N+1 (already instantiated during the prepare phase via `refresh_signer_config` in `initialize_runloop`/`refresh_runloop`) is the one that will actually evaluate it. [6](#0-5) [7](#0-6) 

#No vulnerability found for this question.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L333-404)
```rust
    fn process_event(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        event: Option<&SignerEvent<SignerMessage>>,
        _res: &Sender<SignerResult>,
        current_reward_cycle: u64,
    ) {
        self.check_submitted_block_proposal();
        self.check_pending_block_validations(stacks_client);

        let mut prior_state = self.local_state_machine.clone();
        let local_signer_protocol_version = self.get_signer_protocol_version();
        if self.reward_cycle <= current_reward_cycle {
            self.local_state_machine.handle_pending_update(&mut self.signer_db, stacks_client,
                &self.proposal_config,
                &mut self.tx_replay_scope, &self.global_state_evaluator, local_signer_protocol_version)
                .unwrap_or_else(|e| error!("{self}: failed to update local state machine for pending update"; "err" => ?e));
        }
        // See if we should capitulate our viewpoint...
        self.local_state_machine.capitulate_viewpoint(
            stacks_client,
            &mut self.signer_db,
            &mut self.global_state_evaluator,
            local_signer_protocol_version,
            sortition_state,
            self.capitulate_miner_view_timeout,
            self.proposal_config.tenure_last_block_proposal_timeout,
            &mut self.last_capitulate_miner_view,
        );

        if prior_state != self.local_state_machine {
            let version = self.get_signer_protocol_version();
            self.local_state_machine
                .send_signer_update_message(&mut self.stackerdb, version);
            prior_state = self.local_state_machine.clone();
        }

        let event_parity = match event {
            // Block proposal events do have reward cycles, but each proposal has its own cycle,
            //  and the vec could be heterogeneous, so, don't differentiate.
            Some(SignerEvent::BlockValidationResponse(_))
            | Some(SignerEvent::MinerMessages(..))
            | Some(SignerEvent::NewBurnBlock { .. })
            | Some(SignerEvent::NewBlock { .. })
            | Some(SignerEvent::StatusCheck)
            | None => None,
            Some(SignerEvent::SignerMessages { signer_set, .. }) => {
                Some(u64::from(*signer_set) % 2)
            }
        };
        let other_signer_parity = (self.reward_cycle + 1) % 2;
        if event_parity == Some(other_signer_parity) {
            return;
        }
        debug!("{self}: Processing event: {event:?}");
        let Some(event) = event else {
            // No event. Do nothing.
            debug!("{self}: No event received");
            return;
        };
        if self.reward_cycle > current_reward_cycle
            && !matches!(
                event,
                SignerEvent::StatusCheck | SignerEvent::NewBurnBlock { .. }
            )
        {
            // The reward cycle has not yet started for this signer instance
            // Do not process any events other than status checks or new burn blocks
            debug!("{self}: Signer reward cycle has not yet started. Ignoring event.");
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1575-1589)
```rust
    fn handle_block_proposal(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        block_proposal: &BlockProposal,
    ) {
        debug!("{self}: Received a block proposal: {block_proposal:?}");
        if block_proposal.reward_cycle != self.reward_cycle {
            // We are not signing for this reward cycle. Ignore the block.
            debug!(
                "{self}: Received a block proposal for a different reward cycle. Ignore it.";
                "requested_reward_cycle" => block_proposal.reward_cycle
            );
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2666-2684)
```rust
    /// Helper for getting the block info from the db while accommodating for reward cycle
    pub fn block_lookup_by_reward_cycle(
        &self,
        block_hash: &Sha512Trunc256Sum,
    ) -> Option<BlockInfo> {
        let block_info = self
            .signer_db
            .block_lookup(block_hash)
            .inspect_err(|e| {
                error!("{self}: Failed to lookup block hash {block_hash} in signer db: {e:?}");
            })
            .ok()
            .flatten()?;
        if block_info.reward_cycle == self.reward_cycle {
            Some(block_info)
        } else {
            None
        }
    }
```

**File:** stacks-node/src/tests/signer/v0/mod.rs (L6077-6096)
```rust
#[test]
#[ignore]
/// Test that signers for an incoming reward cycle, do not sign blocks for the previous reward cycle.
///
/// Test Setup:
/// The test spins up five stacks signers that are stacked for multiple cycles, one miner Nakamoto node, and a corresponding bitcoind.
/// The stacks node is then advanced to Epoch 3.0 boundary to allow block signing.
///
/// Test Execution:
/// The node mines to the middle of the prepare phase of reward cycle N+1.
/// Sends a status request to the signers to ensure both the current and next reward cycle signers are active.
/// A valid Nakamoto block is proposed.
/// Two invalid Nakamoto blocks are proposed.
///
/// Test Assertion:
/// All signers for cycle N sign the valid block.
/// No signers for cycle N+1 emit any messages.
/// All signers for cycle N reject the invalid blocks.
/// No signers for cycle N+1 emit any messages for the invalid blocks.
/// The chain advances to block N.
```

**File:** stacks-node/src/tests/signer/v0/mod.rs (L6249-6268)
```rust
#[test]
#[ignore]
/// Test that signers for an outgoing reward cycle, do not sign blocks for the incoming reward cycle.
///
/// Test Setup:
/// The test spins up five stacks signers that are stacked for multiple cycles, one miner Nakamoto node, and a corresponding bitcoind.
/// The stacks node is then advanced to Epoch 3.0 boundary to allow block signing.
///
/// Test Execution:
/// The node mines to the next reward cycle.
/// Sends a status request to the signers to ensure both the current and previoustimeout_heur reward cycle signers are active.
/// A valid Nakamoto block is proposed.
/// Two invalid Nakamoto blocks are proposed.
///
/// Test Assertion:
/// All signers for cycle N+1 sign the valid block.
/// No signers for cycle N emit any messages.
/// All signers for cycle N+1 reject the invalid blocks.
/// No signers for cycle N emit any messages for the invalid blocks.
/// The chain advances to block N.
```

**File:** stacks-signer/src/runloop.rs (L341-362)
```rust
    /// Refresh signer configuration for a specific reward cycle
    fn refresh_signer_config(&mut self, reward_cycle: u64) {
        let reward_index = reward_cycle % 2;
        let new_signer_config = match self.get_signer_config(reward_cycle) {
            Ok(Some(new_signer_config)) => {
                let signer_mode = new_signer_config.signer_mode.clone();
                let new_signer = Signer::new(&self.stacks_client, new_signer_config);
                info!("{new_signer} Signer is registered for reward cycle {reward_cycle} as {signer_mode}. Initialized signer state.");
                ConfiguredSigner::RegisteredSigner(new_signer)
            }
            Ok(None) => {
                warn!("Signer is not registered for reward cycle {reward_cycle}");
                ConfiguredSigner::not_registered(reward_cycle)
            }
            Err(e) => {
                warn!("Failed to get the reward set info: {e}. Will try again later.");
                return;
            }
        };

        self.stacks_signers.insert(reward_index, new_signer_config);
    }
```

**File:** stacks-signer/src/runloop.rs (L364-385)
```rust
    fn initialize_runloop(&mut self) -> Result<(), ClientError> {
        debug!("Initializing signer runloop...");
        let reward_cycle_info = retry_with_exponential_backoff(|| {
            self.stacks_client
                .get_current_reward_cycle_info()
                .map_err(backoff::Error::transient)
        })?;
        let current_reward_cycle = reward_cycle_info.reward_cycle;
        self.refresh_signer_config(current_reward_cycle);
        // We should only attempt to initialize the next reward cycle signer if we are in the prepare phase of the next reward cycle
        if reward_cycle_info.is_in_next_prepare_phase(reward_cycle_info.last_burnchain_block_height)
        {
            self.refresh_signer_config(current_reward_cycle.saturating_add(1));
        }
        self.current_reward_cycle_info = Some(reward_cycle_info);
        if self.stacks_signers.is_empty() {
            self.state = State::NoRegisteredSigners;
        } else {
            self.state = State::RegisteredSigners;
        }
        Ok(())
    }
```
