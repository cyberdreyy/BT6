### Title
Stale-cycle signer overwritten in `stacks_signers` map before its unprocessed proposals/equivocation state are drained - ([File: stacks-signer/src/runloop.rs])

### Summary
`RunLoop::stacks_signers` holds at most two live `Signer` instances, keyed by `reward_cycle % 2` [1](#0-0) . `cleanup_stale_signers` only removes a stale-cycle entry when it has no unprocessed blocks left [2](#0-1) , but `refresh_signer_config` unconditionally overwrites whatever `Signer` currently occupies that hash-map slot with a brand-new `Signer::new(...)` instance, with no check that the old signer's pending work has actually drained [3](#0-2) . This is the same setter-without-drain-check pattern as the reported CurveLP `set_pool()` bug: a "slot" is repointed to a new logical owner before the old owner's outstanding obligations are cleared.

### Finding Description
Reward cycles `N` and `N-2` share the same parity (`% 2`), so they map to the same key in `stacks_signers`. The intended lifecycle is:
1. During the prepare phase of cycle `N-1`, `refresh_runloop` calls `refresh_signer_config(N)`, inserting the new cycle's `Signer` at slot `N % 2` — the *other* slot from the current cycle `N-1`, so no collision occurs yet [4](#0-3) .
2. Once the burn view rolls into cycle `N`, `cleanup_stale_signers(N)` tries to evict the entry for cycle `N-1` (slot `(N-1)%2`), but only if `!signer.has_unprocessed_blocks()`; if the outgoing signer still has an unprocessed/pending block (e.g. a tail-of-tenure proposal still awaiting node validation or global acceptance), it is deliberately left in place [5](#0-4) .
3. Later, during the prepare phase of cycle `N`, `refresh_runloop` calls `refresh_signer_config(N+1)`. `N+1` has the same parity as `N-1`, so this write targets slot `(N+1)%2 == (N-1)%2` — exactly the slot still occupied by the *not-yet-drained* `N-1` signer. `refresh_signer_config` performs `self.stacks_signers.insert(reward_index, new_signer_config)` unconditionally, with no re-check of `has_unprocessed_blocks()` and no attempt to let the old `Signer` finish its pending work [3](#0-2) .

The old `Signer` struct is simply dropped. Its in-process state is lost, including:
- `submitted_block_proposal` / the pending block-validation tracking used by `check_submitted_block_proposal` / `check_pending_block_validations` [6](#0-5) .
- `recently_processed` (`RecentlyProcessedBlocks`), which is explicitly documented as "the guard [that] exists to stop us endorsing two blocks that could both end up in the chain" (equivocation guard) [7](#0-6) .

Because the new `Signer::new()` only reconstructs state from the persisted `SignerDb` and from `StateMachineUpdate` chunks pulled fresh from StackerDB [8](#0-7) , any purely in-memory guard/tracking that had not yet been flushed to `SignerDb` is gone the moment the map entry is replaced.

### Impact Explanation
This falls into the "High" bucket: a signer instance losing its equivocation guard / getting wedged mid-tenure. The outgoing cycle's `Signer` disappears while it still had an outstanding block to validate/sign or a locally-accepted block awaiting global acceptance; any timers, dedupe state, or in-flight validation bookkeeping tied to that instance vanish with it. If a genuinely in-flight validation response for the old cycle's tail block arrives after the swap, there is no live `Signer` object at that slot to correlate/process it (the slot now belongs to `N+1`), so the response is effectively orphaned — a liveness wedge on finishing that tenure's last block. It is not "Critical" because the persisted `SignerDb` (`blocks` table state machine, `BlockInfo::check_state`) still enforces the terminal/one-shot state transitions for anything that was durably written before the drop, so it does not by itself let the signer double-sign a persisted decision; the risk is confined to whatever state existed only in the dropped `Signer` struct's memory at the moment of the overwrite.

### Likelihood Explanation
This requires no privileged access, no majority collusion, and no key compromise — it is triggered purely by ordinary reward-cycle boundary timing combined with a slow/delayed final block of an outgoing tenure (e.g. validation still pending against the node, or global acceptance not yet observed) at the moment the next-but-one reward cycle's prepare phase begins. Given `first_proposal_burn_block_timing`/`block_proposal_validation_timeout` and `tenure_last_block_proposal_timeout` windows can be large, and reward-cycle prepare phases are short relative to those windows, the race between "old cycle still not drained" and "new same-parity cycle's config being installed" is plausible under normal network delay, not just adversarial action.

### Recommendation
In `refresh_signer_config`, before inserting a new `ConfiguredSigner` at `reward_index`, check whether the existing entry at that slot is still active (`has_unprocessed_blocks()` for a `RegisteredSigner`) and, if so, defer the swap (retry on a later pass) rather than unconditionally overwriting it — mirroring the same guard already used in `cleanup_stale_signers`. Alternatively, decouple slot reuse from parity so a not-yet-drained signer is never forced to share a slot with the next same-parity cycle's signer, e.g. by keying `stacks_signers` on the exact `reward_cycle` rather than `reward_cycle % 2`, or by draining/flushing any equivocation-relevant guard state to `SignerDb` synchronously before allowing eviction/overwrite.

### Proof of Concept
Not independently reproduced in this session (no terminal/build access in ask-only mode). The path is derived purely from static review of `stacks-signer/src/runloop.rs` (`refresh_signer_config`, `refresh_runloop`, `cleanup_stale_signers`) and `stacks-signer/src/v0/signer.rs` (`Signer::new`, `recently_processed` guard comment). A concrete PoC would: (1) run a signer through cycle `N-1` with an artificially delayed block-validation response so `has_unprocessed_blocks()` stays true past the cycle boundary into `N`; (2) advance the burnchain into the prepare phase of `N+1`, triggering `refresh_signer_config(N+1)` to overwrite slot `(N+1)%2 == (N-1)%2`; (3) observe that the delayed validation response for the `N-1` tail block is no longer tracked/answered by any live `Signer`, and that `recently_processed`/`submitted_block_proposal` state for that block is gone.

### Citations

**File:** stacks-signer/src/runloop.rs (L192-194)
```rust
    /// The internal signer for an odd or even reward cycle
    /// Keyed by reward cycle % 2
    pub stacks_signers: HashMap<u64, ConfiguredSigner<Signer, T>>,
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

**File:** stacks-signer/src/runloop.rs (L429-439)
```rust
        // Check if we need to refresh the signers:
        //   need to refresh the current signer if we are not configured for the current reward cycle
        //   need to refresh the next signer if we're not configured for the next reward cycle, and we're in the prepare phase
        if !Self::is_configured_for_cycle(&self.stacks_signers, current_reward_cycle) {
            self.refresh_signer_config(current_reward_cycle);
        }
        if is_in_next_prepare_phase
            && !Self::is_configured_for_cycle(&self.stacks_signers, next_reward_cycle)
        {
            self.refresh_signer_config(next_reward_cycle);
        }
```

**File:** stacks-signer/src/runloop.rs (L471-500)
```rust
    fn cleanup_stale_signers(&mut self, current_reward_cycle: u64) {
        #[cfg(any(test, feature = "testing"))]
        if TEST_SKIP_SIGNER_CLEANUP.get() {
            warn!("Skipping signer cleanup due to testing directive.");
            return;
        }
        let mut to_delete = Vec::new();
        for (idx, signer) in &mut self.stacks_signers {
            let reward_cycle = signer.reward_cycle();
            if reward_cycle >= current_reward_cycle {
                // We are either the current or a future reward cycle, so we are not stale.
                continue;
            }
            match signer {
                ConfiguredSigner::RegisteredSigner(signer) => {
                    if !signer.has_unprocessed_blocks() {
                        debug!("{signer}: Signer's tenure has completed.");
                        to_delete.push(*idx);
                    }
                }
                ConfiguredSigner::NotRegistered { .. } => {
                    debug!("{signer}: Unregistered signer's tenure has completed.");
                    to_delete.push(*idx);
                }
            }
        }
        for idx in to_delete {
            self.stacks_signers.remove(&idx);
        }
    }
```

**File:** docs/signer-flows.md (L125-128)
```markdown
> Anchors: `process_event`, `handle_event_match`,
> `check_submitted_block_proposal`, `check_pending_block_validations`,
> `handle_post_block`, `mock_sign` (signer.rs); `handle_pending_update`,
> `check_miner_inactivity`, `capitulate_viewpoint` (signer_state.rs)
```

**File:** stacks-signer/src/v0/signer.rs (L219-299)
```rust
        let mut signer_db =
            SignerDb::new(&signer_config.db_path).expect("Failed to connect to signer Db");
        let proposal_config = ProposalEvalConfig::from(&signer_config);

        let stacks_address = StacksAddress::p2pkh(
            signer_config.mainnet,
            &StacksPublicKey::from_private(&signer_config.stacks_private_key),
        );

        let session = stackerdb
            .get_session_mut(&MessageSlotID::StateMachineUpdate)
            .expect("Invalid stackerdb session");
        let signer_slot_ids: Vec<_> = signer_config
            .signer_entries
            .signer_id_to_addr
            .keys()
            .copied()
            .collect();
        for (chunk_opt, slot_id) in session
            .get_latest_chunks(&signer_slot_ids)
            .inspect_err(|e| {
                warn!("Error retrieving state machine updates from stacker DB: {e}");
            })
            .unwrap_or_default()
            .into_iter()
            .zip(signer_slot_ids.iter())
        {
            let Some(chunk) = chunk_opt else {
                continue;
            };

            let Ok(SignerMessage::StateMachineUpdate(update)) =
                read_next::<SignerMessage, _>(&mut &chunk[..])
            else {
                continue;
            };

            let Some(signer_addr) = signer_config.signer_entries.signer_id_to_addr.get(slot_id)
            else {
                continue;
            };

            // This might update the received time/cause a discrepency between when we receive it at our event queue, but it
            // allows signers to potentially evaluate blocks immediately regardless of its nodes event queue state on startup
            if let Err(e) = signer_db.insert_state_machine_update(
                signer_config.reward_cycle,
                signer_addr,
                &update,
                &SystemTime::now(),
            ) {
                warn!("Error submitting state machine update to signer DB: {e}");
            };
        }

        let updates = signer_db
            .get_signer_state_machine_updates(signer_config.reward_cycle)
            .inspect_err(|e| {
                warn!("An error occurred retrieving state machine updates from the db: {e}")
            })
            .unwrap_or_default();

        let global_state_evaluator = GlobalStateEvaluator::new(
            updates,
            signer_config.signer_entries.signer_addr_to_weight.clone(),
        );
        #[cfg(any(test, feature = "testing"))]
        let version = signer_config.supported_signer_protocol_version;
        #[cfg(not(any(test, feature = "testing")))]
        let version = SUPPORTED_SIGNER_PROTOCOL_VERSION;
        let signer_state = LocalStateMachine::new(
            &mut signer_db,
            stacks_client,
            &proposal_config,
            &global_state_evaluator,
            version,
        )
        .unwrap_or_else(|e| {
            warn!("Failed to initialize local state machine for signer: {e:?}");
            LocalStateMachine::Uninitialized
        });
        Self {
```

**File:** stacks-signer/src/v0/signer.rs (L1108-1111)
```rust
    /// Whether a block we signed still conflicts at `proposed_height`.
    ///
    /// The guard exists to stop us endorsing two blocks that could both end up in the chain. It
    /// must not, however, outlive the block it protects: a Bitcoin reorg can kill a block we
```
