### Title
Ignored `BlockInfo` state-transition failures let a signer broadcast a block or a rejection that contradicts its own already-finalized verdict — (File: `stacks-signer/src/v0/signer.rs`)

### Summary
Several signer handlers call `BlockInfo::mark_locally_accepted`/`mark_locally_rejected` (which wrap `move_to()`), check the `Result` only to decide whether to *log* a warning, and then unconditionally continue to persist the block and broadcast a signature/rejection — regardless of whether the state transition actually succeeded. This mirrors the LiFi finding: a call whose success/failure return value is available is not used to gate the security-relevant downstream action (there, sending swapped tokens; here, broadcasting a signature or a rejection).

### Finding Description
`BlockInfo::mark_locally_accepted` and `BlockInfo::mark_locally_rejected` mutate `valid`/`signed_group` unconditionally and *then* attempt `move_to()`, which can fail (return `Err`) if the requested transition is not legal from the current state (e.g. `LocallyAccepted`/`LocallyRejected` cannot be reached from `GloballyAccepted`/`GloballyRejected`): [1](#0-0) 

`check_state`/`move_to` do not mutate `self.state` on failure, so on error the persisted `state` stays whatever it was (e.g. `GloballyRejected`), while `valid`/`signed_group` have already been overwritten: [2](#0-1) 

In `store_and_process_block_signature`, once the signature-weight threshold is reached, `mark_locally_accepted(true)` is called and its `Err` is only used to decide whether to log — the function then falls through unconditionally to `insert_block` and `broadcast_signed_block`: [3](#0-2) 

The same pattern (check the `Result`, warn conditionally, but never `return`) appears in `handle_block_validate_reject`, where a failed `mark_locally_rejected()` still leads to storing/broadcasting a `BlockRejection`: [4](#0-3) 

and in the rejection-override branch of `handle_block_validate_ok`: [5](#0-4) 

By contrast, the pre-commit branch of `handle_block_validate_ok` does gate correctly by returning on failure: [6](#0-5) 

showing the "check-but-don't-enforce" pattern above is an inconsistency/oversight rather than an intended design.

### Impact Explanation
The equality that is supposed to hold is: *the signer only broadcasts a signature/rejection that is consistent with the block state it has actually recorded* (a `GloballyAccepted`/`GloballyRejected` verdict is terminal and must not be silently overridden by a stale straggler message). Because the state-transition failure is swallowed, a signer can:
- Push `broadcast_signed_block` (via `handle_post_block`) for a block that its own bookkeeping considers `GloballyRejected`, effectively re-submitting a superseded/rejected block to the node as if it carries a valid quorum of signatures, or
- Broadcast a `BlockRejection` for a block that has already reached `GloballyAccepted` in its own DB, which other signers count toward `total_weight_rejected` (see `store_and_process_block_rejection` / `stackerdb_listener.rs`), risking a signer set that disagrees on whether a block is canonical.

This falls into the Critical bucket described in the rules: a signer producing a signed/rejected message that conflicts with its own already-finalized (canonical) verdict, and/or a stale rejection being recounted against an already-accepted block.

### Likelihood Explanation
This requires no majority collusion and no key compromise — it is triggered purely by message reordering/latency that any single signer can experience on its own: receiving a late straggler acceptance or pre-commit/signature message for a block it has already resolved (accepted or rejected) at the reward-cycle boundary, or when a competing block at the same height has already been finalized. The `has_reached_consensus()` guard is present in the code (showing the authors anticipated this race) but its result is used only to select the log level, not to gate execution, so the race is only "logged" rather than prevented.

### Recommendation
In `store_and_process_block_signature`, `handle_block_validate_reject`, and the rejection branch of `handle_block_validate_ok`, `return` immediately when `mark_locally_accepted`/`mark_locally_rejected` returns `Err` (mirroring the already-correct early-return in the pre-commit branch of `handle_block_validate_ok`), instead of only conditionally warning and falling through to `insert_block`/broadcast. This ensures that `valid`/`signed_group` mutations and any subsequent network broadcast only happen when the state transition itself is legal, exactly analogous to adding the missing "tokens actually received" `require()` in the LiFi fix.

### Proof of Concept
1. Signer S validates block B, and (due to network delay) later receives enough rejection weight for B such that `mark_globally_rejected` sets `state = GloballyRejected` in its `BlockInfo`.
2. A late `BlockAccepted`/signature message for B (from a peer whose message was delayed) arrives afterward and, combined with S's earlier bookkeeping, `store_and_process_block_signature` computes `total_signature_weight >= min_weight`.
3. `block_info.mark_locally_accepted(true)` is called: `signed_group` is set, but `move_to(LocallyAccepted)` fails because current state is `GloballyRejected` (illegal transition per `check_state`).
4. The `Err` branch only logs (`has_reached_consensus()` is `true`, so not even a warning), then execution falls through to:
`self.signer_db.insert_block(block_info)` (persisting `signed_group` set while `state` is still `GloballyRejected`) and `self.broadcast_signed_block(stacks_client, block_info.block.clone(), &addrs_to_sigs)` — pushing the rejected block to the node as signed, in contradiction with S's own finalized verdict. [3](#0-2)

### Citations

**File:** stacks-signer/src/signerdb.rs (L279-306)
```rust
    /// Mark this block as valid and the appropriate timestamps if they aren't already set, and attempt to mark it as locally accepted.
    pub fn mark_locally_accepted(&mut self, group_signed: bool) -> Result<(), String> {
        if group_signed {
            self.signed_group.get_or_insert(get_epoch_time_secs());
        } else {
            self.valid = Some(true);
            self.approved_time.get_or_insert(get_epoch_time_secs());
            self.signed_self.get_or_insert(get_epoch_time_secs());
        }
        self.move_to(BlockState::LocallyAccepted)
    }

    /// Mark this block's signed group time if not already set and attempt to mark it as globally accepted.
    pub fn mark_globally_accepted(&mut self) -> Result<(), String> {
        self.signed_group.get_or_insert(get_epoch_time_secs());
        self.move_to(BlockState::GloballyAccepted)
    }

    /// Mark this block as invalid and attempt to mark it as locally rejected
    pub fn mark_locally_rejected(&mut self) -> Result<(), String> {
        self.valid = Some(false);
        self.move_to(BlockState::LocallyRejected)
    }

    /// Attempt to mark the block as globally rejected
    pub fn mark_globally_rejected(&mut self) -> Result<(), String> {
        self.move_to(BlockState::GloballyRejected)
    }
```

**File:** stacks-signer/src/signerdb.rs (L313-341)
```rust
    /// Check if the block state transition is valid
    fn check_state(&self, state: BlockState) -> bool {
        let prev_state = &self.state;
        if *prev_state == state {
            return true;
        }
        match state {
            BlockState::Unprocessed => false,
            BlockState::LocallyAccepted | BlockState::LocallyRejected => !matches!(
                prev_state,
                BlockState::GloballyRejected | BlockState::GloballyAccepted
            ),
            BlockState::GloballyAccepted => !matches!(prev_state, BlockState::GloballyRejected),
            BlockState::GloballyRejected => !matches!(prev_state, BlockState::GloballyAccepted),
            BlockState::PreCommitted => matches!(prev_state, BlockState::Unprocessed),
        }
    }

    /// Attempt to transition the block state
    pub fn move_to(&mut self, state: BlockState) -> Result<(), String> {
        if !self.check_state(state) {
            return Err(format!(
                "Invalid state transition from {} to {state}",
                self.state
            ));
        }
        self.state = state;
        Ok(())
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1946-1959)
```rust
        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
        {
            // The signer db state has changed. We no longer view this block as valid. Override the validation response.
            if let Err(e) = block_info.mark_locally_rejected() {
                if !block_info.has_reached_consensus() {
                    warn!("{self}: Failed to mark block as locally rejected: {e:?}");
                }
            };
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.handle_block_rejection(&block_rejection, sortition_state);
            self.send_block_response(&block_info.block, block_rejection.into());
```

**File:** stacks-signer/src/v0/signer.rs (L1960-1970)
```rust
        } else {
            if let Err(e) = block_info.mark_pre_committed() {
                // The block may have reached enough signatures before we validated the block so should fail to mark pre-committed
                // but still call to make sure the timestamps and validity are updated correctly.
                if !block_info.has_reached_consensus()
                    && block_info.state != BlockState::LocallyAccepted
                {
                    warn!("{self}: Failed to mark block as approved: {e:?}",);
                    return;
                }
            }
```

**File:** stacks-signer/src/v0/signer.rs (L2016-2051)
```rust
        if !block_info.check_static_valid_block() {
            debug!("{self}: Block is syntatically invalid; will not store");
            return;
        }
        if let Err(e) = block_info.mark_locally_rejected() {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally rejected: {e:?}");
            }
        }
        let block_rejection = BlockRejection::from_validate_rejection(
            block_validate_reject.clone(),
            &self.private_key,
            self.mainnet,
            self.signer_db.calculate_full_extend_timestamp(
                self.proposal_config
                    .tenure_idle_timeout
                    .saturating_add(self.proposal_config.tenure_idle_timeout_buffer),
                &block_info.block,
                false,
            ),
            self.signer_db.calculate_read_count_extend_timestamp(
                self.proposal_config
                    .read_count_idle_timeout
                    .saturating_add(self.proposal_config.tenure_idle_timeout_buffer),
                &block_info.block,
                false,
            ),
        );

        block_info.reject_reason = Some(block_rejection.response_data.reject_reason.clone());
        self.signer_db
            .insert_block(&block_info)
            .unwrap_or_else(|e| self.handle_insert_block_error(e));
        self.handle_block_rejection(&block_rejection, sortition_state);
        self.send_block_response(&block_info.block, block_rejection.into());
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2525-2538)
```rust
        // have enough signatures to broadcast!
        // move block to LOCALLY accepted state.
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
        if let Err(e) = block_info.mark_locally_accepted(true) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}");
            }
        }
        let _ = self.signer_db.insert_block(block_info).map_err(|e| {
            warn!("Failed to set group threshold signature timestamp for {block_hash}: {e:?}");
            panic!("{self} Failed to write block to signerdb: {e}");
        });
        self.broadcast_signed_block(stacks_client, block_info.block.clone(), &addrs_to_sigs);
    }
```
