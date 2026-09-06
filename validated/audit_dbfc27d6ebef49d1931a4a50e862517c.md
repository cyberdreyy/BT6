No vulnerability found for this question.

`get_parsed_reward_set` in `stacks-signer/src/runloop.rs` is a stateless helper that takes a `reward_cycle` parameter and directly queries the stacks node via `self.stacks_client.get_reward_set_signers(reward_cycle)` on every call — it holds no burn-height cursor, no cached tip, and no state that could go "stale" between calls. [1](#0-0) 

It is only used to build the list of registered signers for a reward cycle when constructing a `SignerConfig` (via `get_signer_config`), not to validate `BlockProposal` messages, burn views, or reorg claims against a canonical tip. [2](#0-1) 

The actual burn-tip/burn-view bookkeeping lives in `RunLoop::refresh_runloop` and the `RewardCycleInfo` struct (`current_reward_cycle_info`), which is refreshed based on `max(node's live peer info burn height, event burn height)` each time a `NewBurnBlock` event arrives — not inside `get_parsed_reward_set`. [3](#0-2) 

There is no "burn-height cursor" parameter or field read by `get_parsed_reward_set`, so the premise of the question — that this function itself reads a stale cursor and thereby breaks the "burn view validated == canonical tip" invariant — does not match the code. Actual BlockProposal validation against burn views happens elsewhere (in the `v0`/`v1` signer state machine and `SortitionsView`, e.g. `crate::chainstate::v1::SortitionsView` imported at the top of this file), not in this function. [4](#0-3) 

Since the targeted function has no cursor, no caching, and no role in per-proposal burn-view validation, the described attack path is not reachable through it.

### Citations

**File:** stacks-signer/src/runloop.rs (L25-25)
```rust
use crate::chainstate::v1::SortitionsView;
```

**File:** stacks-signer/src/runloop.rs (L218-233)
```rust
    pub fn get_parsed_reward_set(
        &self,
        reward_cycle: u64,
    ) -> Result<Option<SignerEntries>, ClientError> {
        debug!("Getting registered signers for reward cycle {reward_cycle}...");
        let Some(signers) = self.stacks_client.get_reward_set_signers(reward_cycle)? else {
            warn!("No reward set signers found for reward cycle {reward_cycle}.");
            return Ok(None);
        };
        if signers.is_empty() {
            warn!("No registered signers found for reward cycle {reward_cycle}.");
            return Ok(None);
        }
        let entries = SignerEntries::parse(self.config.network.is_mainnet(), &signers).unwrap();
        Ok(Some(entries))
    }
```

**File:** stacks-signer/src/runloop.rs (L236-249)
```rust
    fn get_signer_config(
        &mut self,
        reward_cycle: u64,
    ) -> Result<Option<SignerConfig>, ConfigurationError> {
        // We can only register for a reward cycle if a reward set exists.
        let signer_entries = match self.get_parsed_reward_set(reward_cycle) {
            Ok(Some(x)) => x,
            Ok(None) => return Ok(None),
            Err(e) => {
                warn!("Error while fetching reward set {reward_cycle}: {e:?}");
                return Err(e.into());
            }
        };

```

**File:** stacks-signer/src/runloop.rs (L387-447)
```rust
    fn refresh_runloop(&mut self, ev_burn_block_height: u64) -> Result<(), ClientError> {
        let current_burn_block_height = std::cmp::max(
            self.stacks_client.get_peer_info()?.burn_block_height,
            ev_burn_block_height,
        );
        let reward_cycle_info = self
            .current_reward_cycle_info
            .as_mut()
            .expect("FATAL: cannot be an initialized signer with no reward cycle info.");
        let current_reward_cycle = reward_cycle_info.reward_cycle;
        let block_reward_cycle = reward_cycle_info.get_reward_cycle(current_burn_block_height);

        // First ensure we refresh our view of the current reward cycle information
        if block_reward_cycle != current_reward_cycle {
            let new_reward_cycle_info = RewardCycleInfo {
                reward_cycle: block_reward_cycle,
                reward_cycle_length: reward_cycle_info.reward_cycle_length,
                prepare_phase_block_length: reward_cycle_info.prepare_phase_block_length,
                first_burnchain_block_height: reward_cycle_info.first_burnchain_block_height,
                last_burnchain_block_height: current_burn_block_height,
            };
            *reward_cycle_info = new_reward_cycle_info;
        }
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
```
