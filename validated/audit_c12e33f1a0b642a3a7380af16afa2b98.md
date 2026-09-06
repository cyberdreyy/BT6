### Title
`refresh_signer_config` unconditionally overwrites a same-parity `ConfiguredSigner` entry with in-flight state, dropping cycle-N proposal/threshold context - ([File: stacks-signer/src/runloop.rs])

### Summary
`RunLoop::stacks_signers` is keyed only by `reward_cycle % 2`, and `refresh_signer_config` (called from `refresh_runloop`) does a bare `HashMap::insert` at that parity key with no check on the entry it replaces. [1](#0-0)  The only place that checks `has_unprocessed_blocks()` before removing a stale entry is `cleanup_stale_signers`, but that function runs *after* `refresh_signer_config` in `refresh_runloop`, so it cannot protect against the overwrite that already happened. [2](#0-1) 

### Finding Description
The claimed equality is: the `SignerConfig`/`ConfiguredSigner` object actually resident at `self.stacks_signers.get(&(reward_cycle % 2))` must correspond to the reward cycle of any `BlockProposal` still being processed for that parity slot.

Tracing `refresh_runloop`:
1. It recomputes `current_reward_cycle` from burn height and calls `Self::is_configured_for_cycle(&self.stacks_signers, current_reward_cycle)`, which only returns true if the map entry at `current_reward_cycle % 2` reports `signer.reward_cycle() == current_reward_cycle`. [3](#0-2) 
2. If false, it calls `self.refresh_signer_config(current_reward_cycle)`, which builds a brand-new `Signer` (fresh internal state, no knowledge of any pending proposal) and does `self.stacks_signers.insert(reward_index, new_signer_config)` — unconditionally replacing whatever `ConfiguredSigner` was previously stored at that index, dropping it (and any unresolved `BlockProposal`/aggregation state it held) on the floor. [4](#0-3) 
3. Only *after* this overwrite does `cleanup_stale_signers(current_reward_cycle)` run, which is the sole place that checks `has_unprocessed_blocks()` before deleting a stale cycle's signer — but by then it's checking the *new* map, and the potentially-stale N-cycle signer with pending proposals is already gone. [5](#0-4) 

Because reward cycles increment by 1 each cycle boundary (cycle N → N+1 → N+2), and parity repeats every 2 cycles, the overwrite specifically hits the *same-parity* two-cycles-back entry: when the runloop transitions from cycle N+1 to cycle N+2, `is_configured_for_cycle(N+2)` looks up key `(N+2)%2 == N%2`, finds the still-resident cycle-N `ConfiguredSigner` (which was never cleaned up because `cleanup_stale_signers` at the N→N+1 transition found `has_unprocessed_blocks()==true` and skipped it), and `refresh_signer_config(N+2)` blindly replaces it — regardless of `has_unprocessed_blocks()`.

This is a genuine break: the very mechanism (`has_unprocessed_blocks()` check in `cleanup_stale_signers`) that was designed to preserve a signer with pending work is bypassed by `refresh_signer_config`'s unconditional insert, which contains no equivalent guard.

### Impact Explanation
If cycle N's signer is dropped while it still had `has_unprocessed_blocks() == true`, any pending/in-flight `BlockProposal` for cycle N loses its accumulated state (whatever tracking `has_unprocessed_blocks` reflects — pending validation, unresolved proposal, etc.) with no successor signer object to resume it. The new cycle-N+2 `Signer` at the same map slot has a completely fresh `LocalStateMachine`, its own `signer_entries`/weights, and no memory of the old proposal. This matches the "High" impact category: a signer effectively wedged/losing its in-flight context and never completing the correct handling of that cycle's proposal (liveness loss / acting on stale-vs-vanished reward-set context), rather than a wrong-cycle signature being produced (since incoming messages for cycle N are dispatched by `process_event` primarily to whichever `ConfiguredSigner` exists, and after the overwrite there is no cycle-N signer object left at all to route to — the state is lost rather than misapplied to a live wrong-cycle signer, which I could not fully confirm from the message-routing code without further inspection of `process_event`/`Signer` dispatch in `stacks-signer/src/v0/signer.rs`, which I was unable to complete before running out of tool calls).

### Likelihood Explanation
This requires cycle N to still have `has_unprocessed_blocks() == true` at the moment cycle N+2's configuration first becomes fetchable (i.e., pending work surviving across an entire reward cycle boundary, on the order of ~2100 Bitcoin blocks by default). This is a long-lived condition dependent on the node/chain's operational state (e.g. a stalled tenure, a long-unresolved proposal, or an unusual restart/catch-up scenario) rather than something a single unprivileged attacker with one miner slot can directly force to occur within a specific short window, since they do not control burn-block cadence or reward-cycle transitions. I was not able to verify from available code whether an attacker crafting `BlockProposal`/StackerDB gossip alone can keep `has_unprocessed_blocks()` true for an entire extra reward cycle purely through repeated proposal spam (this would need inspection of what sets/clears that flag in `stacks-signer/src/v0/signer.rs`, which I did not have iterations left to fully trace).

### Recommendation
In `refresh_signer_config` (stacks-signer/src/runloop.rs), before calling `self.stacks_signers.insert(reward_index, new_signer_config)`, check whether an existing entry at `reward_index` belongs to an older reward cycle and, if it is a `RegisteredSigner` with `has_unprocessed_blocks() == true`, defer the overwrite (retry on a later pass) instead of silently replacing it — mirroring the same guard already used in `cleanup_stale_signers`.

### Proof of Concept
I could not fully construct a concrete, isolated Rust unit test asserting the exact "before/after" equality without further inspection of `Signer::has_unprocessed_blocks` and `process_event`'s per-cycle message routing in `stacks-signer/src/v0/signer.rs`, which I was unable to complete within the available tool budget. A reproduction would need to: (1) construct a `RunLoop` with a `ConfiguredSigner::RegisteredSigner` at cycle N with a pending proposal such that `has_unprocessed_blocks()` returns true, (2) call `refresh_runloop` to advance through cycle N+1 (verifying `cleanup_stale_signers` skips removal due to the pending-blocks guard), (3) call `refresh_runloop` again to advance to cycle N+2 and assert that `stacks_signers.get(&(N%2))` is silently replaced with the N+2 config despite `has_unprocessed_blocks()` still being true beforehand — confirming the overwrite happens without honoring the same guard `cleanup_stale_signers` enforces.

### Citations

**File:** stacks-signer/src/runloop.rs (L342-362)
```rust
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

**File:** stacks-signer/src/runloop.rs (L429-441)
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

        self.cleanup_stale_signers(current_reward_cycle);
```

**File:** stacks-signer/src/runloop.rs (L450-458)
```rust
    fn is_configured_for_cycle(
        stacks_signers: &HashMap<u64, ConfiguredSigner<Signer, T>>,
        reward_cycle: u64,
    ) -> bool {
        let Some(signer) = stacks_signers.get(&(reward_cycle % 2)) else {
            return false;
        };
        signer.reward_cycle() == reward_cycle
    }
```
