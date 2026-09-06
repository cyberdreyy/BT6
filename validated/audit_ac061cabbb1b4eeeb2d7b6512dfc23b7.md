### Title
`SortitionsView::check_proposal`'s consensus-hash-mismatch reset silently clears a sticky miner-invalidation flag, re-arming a previously invalidated miner - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
`SortitionsView::check_proposal` (v1 chainstate) marks a sortition's miner as `InvalidatedBeforeFirstBlock`/`InvalidatedAfterFirstBlock` when it detects misbehavior (e.g. a bad-timed reorg attempt), and that status is meant to be sticky across all subsequent proposals in the same tenure so the signer never signs for that miner again [1](#0-0) . However, when a proposal's `consensus_hash` matches neither the cached current nor last sortition, and `reset_view_if_wrong_consensus_hash` is `true` (the value used on every production call from `check_block_against_local_state`), the view is unconditionally reset via `reset_view()`, which re-fetches sortition data and re-initializes `miner_status` to `Valid` [2](#0-1) [3](#0-2) [4](#0-3) . This lets the current one-slot miner clear its own sticky invalidation by first sending any proposal with an unrelated `consensus_hash`, then re-sending the previously-blocked (invalid/reorging) proposal.

### Finding Description
`check_block_against_local_state` in `stacks-signer/src/v0/signer.rs` always calls `check_proposal` with `reset_view_if_wrong_consensus_hash = true` in production [5](#0-4) .

Inside `check_proposal`, the "sticky" invalidation checks at the top (timeout-based invalidation, and the tip/parent-tenure-choice invalidation) only run `if self.cur_sortition.miner_status == SortitionMinerStatus::Valid`, i.e. once a sortition's miner has been flagged `InvalidatedBeforeFirstBlock`/`InvalidatedAfterFirstBlock` it is meant to stay that way for the rest of the tenure regardless of what the live tip/timeout state says on a later call [6](#0-5) . This stickiness is the actual persistent memory of a detected reorg/timeout violation for that sortition.

Further down, if the incoming block's `consensus_hash` matches neither `cur_sortition` nor `last_sortition`, the code (when `reset_view_if_wrong_consensus_hash` is true) calls `self.reset_view(client)` and recurses with `false` [7](#0-6) . `reset_view` re-derives both `cur_sortition` and `last_sortition` from scratch via `SortitionState::try_from`, whose `TryFrom<SortitionInfo>` impl hard-codes `miner_status: SortitionMinerStatus::Valid` [8](#0-7) [3](#0-2) . Because the `SortitionsView` instance (`sortition_state: &mut Option<SortitionsView>`) is cached and reused across many block-proposal evaluations by the same signer [9](#0-8) , this mutation clobbers any prior invalidation of the *current* sortition's miner, wiping the signer's memory that the miner had already been caught misbehaving.

Any block header — even one with a garbage/foreign `consensus_hash`, since `consensus_hash` is checked *before* miner-pubkey verification — triggers this branch. The current elected miner can craft and broadcast such a "probe" block via the ordinary StackerDB block-proposal channel to force the reset, then follow up with the actual bad proposal (e.g. one attempting the reorg that was already flagged) which the signer will now evaluate against a freshly-`Valid` `miner_status`.

### Impact Explanation
This breaks the invalid/non-canonical-signature equality guard the signer relies on: a miner sortition explicitly marked invalid (for attempting a disallowed reorg, or for timing out) can be silently re-validated by the same actor without any majority of signers, StackerDB tampering, or key compromise, purely via ordinary proposal traffic. If the underlying misbehavior condition it was flagged for is not independently re-derived and re-blocked by the recursive retry, the signer can end up signing a block from a miner it had already determined should never be signed for again in that tenure — a High/Critical-class safety break per the stated impact categories (miner-status corruption undermining the reorg-prevention guard).

### Likelihood Explanation
Reachable by any single elected (one-slot) miner without cooperation from other signers: it only needs to send two block proposals over the normal StackerDB channel — one with an unrelated `consensus_hash` to force the reset, then the real bad proposal. `check_block_against_local_state` always passes `true` for `reset_view_if_wrong_consensus_hash` in production, so every signer's cached `SortitionsView` is susceptible on the very first mismatched proposal it processes. The condition on the effectiveness of the follow-up bad proposal depends on whether the specific invalidation reason (timeout vs. reorg-timing) would be freshly re-triggered by the recursive `check_proposal(..., false, ...)` call using live data; this nuance could not be fully confirmed with static analysis alone, so likelihood is assessed as plausible but not proven end-to-end without dynamic testing.

### Recommendation
Preserve a previously-set `InvalidatedBeforeFirstBlock`/`InvalidatedAfterFirstBlock` status across `reset_view()` for a sortition whose `consensus_hash` is unchanged, instead of unconditionally re-initializing `miner_status` to `Valid` in `SortitionState::try_from`/`reset_view`. At minimum, `reset_view` should carry forward the prior `miner_status` for any sortition whose identity (`consensus_hash`) is unchanged from before the reset, only re-deriving `Valid` for genuinely new sortitions.

### Proof of Concept
Could not be fully validated with static analysis alone (no test harness run); the trace above is the reasoning chain: (1) `check_block_against_local_state` → `check_proposal(..., true, ...)` [5](#0-4) ; (2) mismatch branch → `reset_view()` → `miner_status = Valid` [2](#0-1) ; (3) sticky-status guard bypassed on next real proposal [10](#0-9) . A background Devin session with the ability to run the existing `stacks-signer` chainstate v1 test suite (e.g. extending `check_proposal_refresh` / `check_proposal_invalid_status` in `stacks-signer/src/chainstate/tests/v1.rs`) would be needed to construct and confirm a concrete exploit sequence.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L32-41)
```rust
/// Captures this signer's current view of a sortition's miner.
#[derive(PartialEq, Eq, Debug)]
pub enum SortitionMinerStatus {
    /// The signer thinks this sortition's miner is invalid, and hasn't signed any blocks for them.
    InvalidatedBeforeFirstBlock,
    /// The signer thinks this sortition's miner is invalid, but already signed one or more blocks for them.
    InvalidatedAfterFirstBlock,
    /// The signer thinks this sortition's miner is valid
    Valid,
}
```

**File:** stacks-signer/src/chainstate/v1.rs (L97-106)
```rust
impl TryFrom<SortitionInfo> for SortitionState {
    type Error = ClientError;
    fn try_from(value: SortitionInfo) -> Result<Self, Self::Error> {
        let data = SortitionData::try_from(value)?;
        Ok(Self {
            data,
            miner_status: SortitionMinerStatus::Valid,
        })
    }
}
```

**File:** stacks-signer/src/chainstate/v1.rs (L144-203)
```rust
        if self.cur_sortition.miner_status == SortitionMinerStatus::Valid
            && SortitionState::is_timed_out(
                &self.cur_sortition.data.consensus_hash,
                signer_db,
                self.config.block_proposal_timeout,
            )?
        {
            info!(
                "Current miner timed out, marking as invalid.";
                "block_height" => block.header.chain_length,
                "block_proposal_timeout" => ?self.config.block_proposal_timeout,
                "current_sortition_consensus_hash" => ?self.cur_sortition.data.consensus_hash,
            );
            self.cur_sortition.miner_status = SortitionMinerStatus::InvalidatedBeforeFirstBlock;

            // If the current proposal is also for this current
            // sortition, then we can return early here.
            if self.cur_sortition.data.consensus_hash == block.header.consensus_hash {
                return Err(RejectReason::InvalidMiner);
            }
        } else if let Some(tip) = signer_db
            .get_canonical_tip()
            .map_err(SignerChainstateError::from)?
        {
            // Check if the current sortition is aligned with the expected tenure:
            // - If the tip is in the current tenure, we are in the process of mining this tenure.
            // - If the tip is not in the current tenure, then we’re starting a new tenure,
            //   and the current sortition's parent tenure must match the tenure of the tip.
            // - If the tip is not building off of the current sortition's parent tenure, then
            //   check to see if the tip's parent is within the first proposal burn block timeout,
            //   which allows for forks when a burn block arrives quickly.
            // - Else the miner of the current sortition has committed to an incorrect parent tenure.
            let consensus_hash_match =
                self.cur_sortition.data.consensus_hash == tip.block.header.consensus_hash;
            let parent_tenure_id_match =
                self.cur_sortition.data.parent_tenure_id == tip.block.header.consensus_hash;
            if !consensus_hash_match && !parent_tenure_id_match {
                // More expensive check, so do it only if we need to.
                let is_valid_parent_tenure = self.cur_sortition.data.check_parent_tenure_choice(
                    signer_db,
                    client,
                    &self.config.first_proposal_burn_block_timing,
                )?;
                if !is_valid_parent_tenure {
                    warn!(
                        "Current sortition does not build off of canonical tip tenure, marking as invalid";
                        "current_sortition_parent" => ?self.cur_sortition.data.parent_tenure_id,
                        "tip_consensus_hash" => ?tip.block.header.consensus_hash,
                    );
                    self.cur_sortition.miner_status =
                        SortitionMinerStatus::InvalidatedBeforeFirstBlock;

                    // If the current proposal is also for this current
                    // sortition, then we can return early here.
                    if self.cur_sortition.data.consensus_hash == block.header.consensus_hash {
                        return Err(RejectReason::ReorgNotAllowed);
                    }
                }
            }
        }
```

**File:** stacks-signer/src/chainstate/v1.rs (L238-265)
```rust
        let miner_pkh = Hash160::from_data(&miner_pk.to_bytes_compressed());
        let Some(proposed_by) =
            (if block.header.consensus_hash == self.cur_sortition.data.consensus_hash {
                Some(ProposedBy::CurrentSortition(&self.cur_sortition))
            } else {
                None
            })
            .or_else(|| {
                self.last_sortition.as_ref().and_then(|last_sortition| {
                    if block.header.consensus_hash == last_sortition.data.consensus_hash {
                        Some(ProposedBy::LastSortition(last_sortition))
                    } else {
                        None
                    }
                })
            })
        else {
            if reset_view_if_wrong_consensus_hash {
                info!(
                    "Miner block proposal has consensus hash that is neither the current or last sortition. Resetting view.";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "current_sortition_consensus_hash" => ?self.cur_sortition.data.consensus_hash,
                    "last_sortition_consensus_hash" => ?self.last_sortition.as_ref().map(|x| &x.data.consensus_hash),
                );
                self.reset_view(client)
                    .map_err(SignerChainstateError::from)?;
                return self.check_proposal(client, signer_db, block, false, replay_set);
            }
```

**File:** stacks-signer/src/chainstate/v1.rs (L546-563)
```rust
    /// Reset the view to the current sortition and last sortition
    pub fn reset_view(&mut self, client: &StacksClient) -> Result<(), ClientError> {
        let CurrentAndLastSortition {
            current_sortition,
            last_sortition,
        } = client.get_current_and_last_sortition()?;

        let cur_sortition = SortitionState::try_from(current_sortition)?;
        let last_sortition = last_sortition
            .map(SortitionState::try_from)
            .transpose()
            .ok()
            .flatten();

        self.cur_sortition = cur_sortition;
        self.last_sortition = last_sortition;
        Ok(())
    }
```

**File:** stacks-signer/src/v0/signer.rs (L883-895)
```rust
        // Get sortition view if we don't have it
        if sortition_state.is_none() {
            *sortition_state =
                SortitionsView::fetch_view(self.proposal_config.clone(), stacks_client)
                    .inspect_err(|e| {
                        warn!(
                            "{self}: Failed to update sortition view: {e:?}";
                            "signer_signature_hash" => %signer_signature_hash,
                            "block_id" => %block_id,
                        )
                    })
                    .ok();
        }
```

**File:** stacks-signer/src/v0/signer.rs (L898-907)
```rust
        if let Some(sortition_state) = sortition_state {
            match sortition_state.check_proposal(
                stacks_client,
                &mut self.signer_db,
                block,
                true,
                self.global_state_evaluator
                    .get_global_tx_replay_set()
                    .unwrap_or_default(),
            ) {
```
