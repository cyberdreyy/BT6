### Title
Shared `SortitionsView` cache leaks miner-invalidation/config state across the current and next reward-cycle signer instances - (File: `stacks-signer/src/runloop.rs`)

### Summary
`RunLoop` keeps exactly one cached sortition view (`sortition_state: Option<SortitionsView>`) but can host two live, independently-configured `Signer` instances at once (the current reward cycle and, during the prepare phase, the next one). Both instances are handed the same `&mut self.sortition_state` in every pass, so whichever signer populates or mutates the cache first "wins" for the other, even though the view encodes per-signer configuration and per-evaluation miner-invalidation state that must not cross reward-cycle contexts.

### Finding Description
`RunLoop<Signer, T>` declares a single, un-scoped cache: [1](#0-0) 

Every pass, this one field is threaded into *every* registered signer, regardless of which reward cycle it belongs to: [2](#0-1) 

`self.stacks_signers` is a `HashMap<u64, ConfiguredSigner<Signer, T>>` keyed by `reward_cycle % 2`, and during a prepare phase it legitimately holds two distinct, concurrently-active `Signer`s — one for the current cycle and one for the next — each carrying its *own* `proposal_config: ProposalEvalConfig` and its own `signer_db`: [3](#0-2) [4](#0-3) 

Inside `check_block_against_local_state`, the shared cache is only (re)fetched when it is `None`; otherwise the existing value — populated by whichever signer ran first — is reused as-is: [5](#0-4) 

`SortitionsView` bundles both the sortition data *and* a `ProposalEvalConfig` that is copied in at fetch time from whichever signer triggered the fetch: [6](#0-5) [7](#0-6) 

`check_proposal` also *mutates* `cur_sortition.miner_status` in place, based on the caller's own timeout config and its own `SignerDb`'s canonical tip: [8](#0-7) [9](#0-8) 

Because the cache is shared, a decision made in the context of one reward cycle's signer (its `proposal_config.block_proposal_timeout`, its `signer_db.get_canonical_tip()`) sets `miner_status = InvalidatedBeforeFirstBlock` on the single shared `SortitionsView`. The very next call — for the *other* signer's reward cycle, in the same `run_one_pass` — inherits that mutated status and that borrowed `config`, without ever re-deriving them from its own context, because the guard is only `if sortition_state.is_none()`.

This mirrors the referenced report's root cause exactly: a piece of state that is supposed to be independently scoped per logical instance (there, per-proxy; here, per reward-cycle signer) is instead computed once and shared, so a "delegatecall" (here, the second signer's own `check_proposal` invocation) observes state that was actually set up for a different instance.

### Impact Explanation
This is a liveness wedge, not a majority-controlled or externally-triggered exploit: it fires purely from the runloop's own scheduling of its two live signer instances during the reward-cycle prepare-phase overlap window. If the current-cycle signer invalidates the miner (e.g. due to a real timeout or reorg-not-allowed condition scoped to cycle N), the next-cycle signer transparently inherits `SortitionMinerStatus::InvalidatedBeforeFirstBlock` and rejects legitimate cycle N+1 proposals with `RejectReason::InvalidMiner` / `ReorgNotAllowed`, even though nothing about cycle N+1's own miner or tenure ever triggered that state. Because the cache is only refreshed when it becomes `None` again (e.g., after a client fetch error), this cross-contamination can persist across passes, matching the High-severity bucket: "a signer wedged into never signing valid blocks... or acting on a stale reward set/threshold."

### Likelihood Explanation
The prepare-phase overlap where two signers are simultaneously registered is a routine, expected condition of every reward-cycle transition (`refresh_signer_config` explicitly configures the next cycle's signer while the current one is still active), so the two signer instances sharing `&mut self.sortition_state` in the same `for` loop happens on essentially every cycle boundary. No majority collusion, external attacker, or unusual timing is required — any legitimate miner-timeout or reorg-disallowed event on one cycle's signer is sufficient to poison the other's view for the remainder of the shared cache's lifetime.

### Recommendation
Scope the sortition-view cache per reward cycle (e.g. `HashMap<u64, SortitionsView>` keyed the same way as `stacks_signers`, or store it inside each `Signer`/`ConfiguredSigner` instance) instead of as a single `RunLoop`-wide field, so that `config` and `miner_status` mutations made while evaluating one reward cycle's proposals can never be read or reused by the other reward cycle's signer.

### Proof of Concept
1. Enter a reward-cycle prepare phase so `self.stacks_signers` holds two `ConfiguredSigner::RegisteredSigner` entries: one for reward cycle `N` (parity slot) and one for reward cycle `N+1` (other parity slot), per `refresh_runloop`/`refresh_signer_config`: [3](#0-2) 
2. In a single `run_one_pass`, the `for configured_signer in self.stacks_signers.values_mut()` loop processes the cycle-`N` signer first (arbitrary `HashMap` iteration order), which calls `check_block_against_local_state` → `SortitionsView::fetch_view` (since `sortition_state` starts `None`) using cycle-`N`'s `proposal_config`, then `check_proposal` determines the current miner has timed out per cycle-`N`'s `block_proposal_timeout`/`signer_db` view and sets `cur_sortition.miner_status = InvalidatedBeforeFirstBlock`: [8](#0-7) 
3. In the same pass, the loop then processes the cycle-`N+1` signer. `sortition_state` is now `Some(..)`, so `check_block_against_local_state`'s `if sortition_state.is_none()` guard skips a fresh fetch: [5](#0-4) 
4. The cycle-`N+1` signer's fresh, legitimate block proposal is checked against the inherited `cur_sortition` whose `miner_status` is `InvalidatedBeforeFirstBlock` and whose `config` is cycle-`N`'s `ProposalEvalConfig`, causing the proposal to be wrongly rejected (`RejectReason::InvalidMiner`) despite cycle `N+1`'s own state having no such invalidation condition.

### Citations

**File:** stacks-signer/src/runloop.rs (L198-200)
```rust
    pub current_reward_cycle_info: Option<RewardCycleInfo>,
    /// Cache sortitin data from `stacks-node`
    pub sortition_state: Option<SortitionsView>,
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

**File:** stacks-signer/src/runloop.rs (L596-609)
```rust
        for configured_signer in self.stacks_signers.values_mut() {
            let ConfiguredSigner::RegisteredSigner(ref mut signer) = configured_signer else {
                debug!("{configured_signer}: Not configured for cycle, ignoring events for cycle");
                continue;
            };

            signer.process_event(
                &self.stacks_client,
                &mut self.sortition_state,
                event.as_ref(),
                res,
                current_reward_cycle,
            );
        }
```

**File:** stacks-signer/src/v0/signer.rs (L299-311)
```rust
        Self {
            private_key: signer_config.stacks_private_key,
            stacks_address,
            stackerdb,
            mainnet: signer_config.mainnet,
            mode,
            signer_addresses: signer_config.signer_entries.signer_addresses.clone(),
            signer_weights: signer_config.signer_entries.signer_addr_to_weight.clone(),
            signer_slot_ids: signer_config.signer_slot_ids.clone(),
            reward_cycle: signer_config.reward_cycle,
            signer_db,
            proposal_config,
            submitted_block_proposal: None,
```

**File:** stacks-signer/src/v0/signer.rs (L882-895)
```rust
        let block_id = block.block_id();
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

**File:** stacks-signer/src/chainstate/v1.rs (L122-132)
```rust
/// The signer's current view of the stacks chain's sortition
///  state
#[derive(Debug)]
pub struct SortitionsView {
    /// the prior successful sortition (this corresponds to the "prior" miner slot)
    pub last_sortition: Option<SortitionState>,
    /// the current successful sortition (this corresponds to the "current" miner slot)
    pub cur_sortition: SortitionState,
    /// configuration settings for evaluating proposals
    pub config: ProposalEvalConfig,
}
```

**File:** stacks-signer/src/chainstate/v1.rs (L144-163)
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
```

**File:** stacks-signer/src/chainstate/v1.rs (L164-201)
```rust
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
```
