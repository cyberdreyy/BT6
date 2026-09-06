### Title
Premature signer-slot eviction in `RunLoop::refresh_signer_config()` drops an outgoing-cycle signer that still has unprocessed blocks - ([File: stacks-signer/src/runloop.rs])

### Summary
The runloop's two-slot signer cache (keyed by `reward_cycle % 2`) is refreshed unconditionally when a new reward cycle needs to be configured, without checking whether the slot's current occupant still has unfinished signing work. This is directly analogous to the `PirexGmx#migrateReward()` bug: the "old producer" (here, the outgoing-cycle `ConfiguredSigner`) is torn down/replaced before its pending obligations are drained, because the code path that performs the swap does not consult the same "is it still active" check (`has_unprocessed_blocks()`) that the dedicated cleanup routine uses.

### Finding Description
`RunLoop` stores at most two live `ConfiguredSigner` instances in `stacks_signers: HashMap<u64, ConfiguredSigner<Signer, T>>`, indexed by `reward_cycle % 2` [1](#0-0) .

`refresh_signer_config()` computes `reward_index = reward_cycle % 2` and then unconditionally does `self.stacks_signers.insert(reward_index, new_signer_config)`, overwriting whatever was previously stored at that index, with no check on the outgoing entry's state [2](#0-1) .

By contrast, `cleanup_stale_signers()` is the routine explicitly designed to retire a stale-cycle signer, and it only removes a `RegisteredSigner` once `!signer.has_unprocessed_blocks()` is true; otherwise it deliberately keeps the entry alive so the signer can finish its pending tenure work [3](#0-2) .

In `refresh_runloop()`, the sequence of operations is:
1. Roll the reward-cycle info forward if the burn height crossed into a new cycle.
2. If not already configured for `current_reward_cycle`, call `refresh_signer_config(current_reward_cycle)`.
3. If in the next-cycle prepare phase and not already configured for `next_reward_cycle`, call `refresh_signer_config(next_reward_cycle)`.
4. Only afterward call `cleanup_stale_signers(current_reward_cycle)`. [4](#0-3) 

Because `reward_cycle % 2` only has two possible values, cycle `C-1` and cycle `C+1` always map to the *same* slot (they differ by 2, so parity matches). Concretely:
- Right after a reward-cycle rollover to `C`, the cache holds: slot `C%2` → the just-promoted `C`-signer, and slot `(C-1)%2` → the now-stale `C-1`-signer (previous "current"), which is retained until `cleanup_stale_signers` decides it is safe to delete.
- Later, while still in cycle `C`, once the runloop enters the prepare phase for cycle `C+1`, `refresh_signer_config(C+1)` is invoked. Its slot is `(C+1)%2`, which is numerically identical to `(C-1)%2`.
- This `insert()` call happens in step 3, *before* `cleanup_stale_signers` (step 4) ever runs, and it does not check `has_unprocessed_blocks()` on the entry it is replacing.

If the stale `C-1` signer still `has_unprocessed_blocks() == true` at that moment (e.g., it is still finishing signature/validation work for the tail of its own tenure, such as a late block proposal near the reward-cycle boundary), it is silently dropped and replaced by the brand-new `C+1` `ConfiguredSigner`. The old signer's in-memory work queue and any block(s) it was actively obligated to sign are discarded — not because it was deregistered or its tenure was actually finished, but purely due to the index collision in the 2-slot cache.

This mirrors the reported bug class precisely: in PirexGmx, the "old" producer pointer (`PirexRewards.producer`) is left pointing at the retiring contract, and code that should have checked for migration completion instead let the stale entity's logic run/be invoked, causing lost rewards. Here, the equality that should hold — "a signer instance is retired only when its outstanding signing obligations are actually complete" — is broken by an unconditional slot overwrite that bypasses the completion check.

### Impact Explanation
This is a liveness wedge on a signer: for the outgoing reward cycle, the signer's in-process obligation to sign/validate remaining blocks for its final tenure can be aborted without any completion check, purely as a side effect of unrelated bookkeeping (advancing into the next cycle's prepare phase). A signer that should still be signing valid blocks for cycle `C-1` may simply stop doing so. This falls under the specified High-impact category: "a signer wedged into never signing valid blocks." It does not, on its own, cause the signer to sign an invalid block or double-sign, but it can cause it to silently fail to complete its per-tenure signing duties, contributing to network-wide signature-collection delays/wedges if multiple signers are affected concurrently (e.g., correlated by shared burn-height triggers across the signer set).

### Likelihood Explanation
The trigger condition — a stale-cycle signer that still has unprocessed blocks exactly at the moment the runloop begins configuring the next-plus-one cycle at the colliding slot — requires a specific but not implausible timing: block proposals/validations trailing near a reward-cycle boundary combined with the runloop needing to configure two cycles ahead in slot terms. It requires no majority collusion, no auth token, and no other signer's key; it is purely a function of local state-machine bookkeeping timing driven by ordinary burn-block events, which any observer (including a single miner controlling block timing near cycle boundaries) can influence to widen or narrow the window. This makes it a plausible, low-cost-to-trigger condition, though it depends on the specific race window rather than being deterministic on every cycle boundary.

### Recommendation
In `refresh_signer_config()`, before overwriting `self.stacks_signers` at `reward_index`, check whether an existing occupant at that slot is stale relative to the cycle being configured and, if so, whether it still `has_unprocessed_blocks()`. If it does, defer the swap (or run `cleanup_stale_signers` first and only proceed once the slot is confirmed free), rather than unconditionally inserting and discarding it. Concretely:
- Call `cleanup_stale_signers(current_reward_cycle)` before calling `refresh_signer_config` for the next cycle, not after, so that any signer with completed work is removed first.
- If the target slot is still occupied by a `RegisteredSigner` with `has_unprocessed_blocks() == true`, skip/delay the refresh for the next cycle rather than clobbering it, and retry on a subsequent pass.

### Proof of Concept
Conceptual trace (from `stacks-signer/src/runloop.rs`):
1. Cycle rolls over to `C`. State: slot `C%2` = `C`-signer; slot `(C-1)%2` = stale `C-1`-signer, still `has_unprocessed_blocks() == true` (e.g. it's still waiting to sign a trailing block for its tenure).
2. Burn block event arrives placing the node in the prepare phase for cycle `C+1`. `refresh_runloop` runs:
   - `is_configured_for_cycle(current=C)` → true, no refresh needed for slot `C%2`.
   - `is_in_next_prepare_phase` → true, `is_configured_for_cycle(next=C+1)` → false (slot `(C+1)%2` holds the stale `C-1` config, whose `reward_cycle() == C-1 != C+1`) → calls `refresh_signer_config(C+1)`.
   - `refresh_signer_config(C+1)` computes `reward_index = (C+1)%2 == (C-1)%2` and does `self.stacks_signers.insert((C-1)%2, new_C+1_config)`, unconditionally replacing the still-active `C-1` signer — even though it `has_unprocessed_blocks()`.
   - Only afterward is `cleanup_stale_signers(C)` invoked, but by then the `C-1` signer object is already gone; its unprocessed blocks are never signed.

This demonstrates that `refresh_signer_config` and `cleanup_stale_signers` are not sequenced/guarded against each other for the shared-parity slot case, allowing a signer with pending work to be evicted before completion. [5](#0-4) [6](#0-5) [3](#0-2)

### Citations

**File:** stacks-signer/src/runloop.rs (L192-200)
```rust
    /// The internal signer for an odd or even reward cycle
    /// Keyed by reward cycle % 2
    pub stacks_signers: HashMap<u64, ConfiguredSigner<Signer, T>>,
    /// The state of the runloop
    pub state: State,
    /// The current reward cycle info. Only None if the runloop is uninitialized
    pub current_reward_cycle_info: Option<RewardCycleInfo>,
    /// Cache sortitin data from `stacks-node`
    pub sortition_state: Option<SortitionsView>,
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

**File:** stacks-signer/src/runloop.rs (L410-448)
```rust
        let reward_cycle_before_refresh = current_reward_cycle;
        let current_reward_cycle = reward_cycle_info.reward_cycle;
        let is_in_next_prepare_phase =
            reward_cycle_info.is_in_next_prepare_phase(current_burn_block_height);
        let next_reward_cycle = current_reward_cycle.saturating_add(1);

        info!(
            "Refreshing runloop with new burn block event";
            "latest_node_burn_ht" => current_burn_block_height,
            "event_ht" =>  ev_burn_block_height,
            "reward_cycle_before_refresh" => reward_cycle_before_refresh,
            "current_reward_cycle" => current_reward_cycle,
            "configured_for_current" => Self::is_configured_for_cycle(&self.stacks_signers, current_reward_cycle),
            "registered_for_current" => Self::is_registered_for_cycle(&self.stacks_signers, current_reward_cycle),
            "configured_for_next" => Self::is_configured_for_cycle(&self.stacks_signers, next_reward_cycle),
            "registered_for_next" => Self::is_registered_for_cycle(&self.stacks_signers, next_reward_cycle),
            "is_in_next_prepare_phase" => is_in_next_prepare_phase,
        );

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

        self.cleanup_stale_signers(current_reward_cycle);
        if self.stacks_signers.is_empty() {
            self.state = State::NoRegisteredSigners;
        } else {
            self.state = State::RegisteredSigners;
        }
        Ok(())
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
