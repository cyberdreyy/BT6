This confirms the premise fails. Key facts:

1. `check_and_handle_prepare_phase_start` (stackslib/src/chainstate/nakamoto/signer_set.rs, lines 944-969) computes and commits the signer set for `cycle_of_prepare_phase`, which is the **next** reward cycle (N+1) — but this happens during a burn height that is still part of the *current* reward cycle N's prepare phase. The commit does not change which reward cycle blocks mined at that burn height belong to. The signer set for cycle N+1 only takes effect starting at the mod-0/mod-1 boundary block of cycle N+1, per `nakamoto_first_block_of_cycle`/`is_naka_signing_cycle_start` (see `stackslib/src/burnchains/burnchain.rs` lines 582-609).

2. `block_height_to_reward_cycle` (used both node-side in `SignerCoordinator::propose_block`, stacks-node/src/nakamoto_node/signer_coordinator.rs lines 294-301, and signer-side via `RewardCycleInfo::get_reward_cycle`, stacks-signer/src/runloop.rs lines 106-118) is a pure deterministic function of burn height. It does not shift at prepare-phase start — it only shifts at the actual reward-cycle boundary. So a proposal made during cycle N's prepare phase is tagged `reward_cycle: N` by the node (via `election_sortition.block_height`), consistently with what the signer expects.

3. `handle_block_proposal` (stacks-signer/src/v0/signer.rs lines 1574-1589) enforces `block_proposal.reward_cycle != self.reward_cycle` → ignore. Since both node and signer derive the reward cycle from the same deterministic burn-height formula, and the .signers contract commit for N+1 during N's prepare phase does not alter that formula's output for burn heights still in cycle N, there is no window where a stale cycle-N `SignerConfig` gets used to sign a block that the node itself would tag as cycle N+1.

4. The transition where the signer must actually pick up the new cycle's `SignerConfig` is handled by `refresh_runloop` (stacks-signer/src/runloop.rs lines 387-448), which is driven by `is_in_next_prepare_phase` for *preparing* (registering) the next-cycle signer ahead of time, while `stacks_signers` keeps the current-cycle `RegisteredSigner` active via `cleanup_stale_signers(current_reward_cycle)` — the current-cycle signer is not torn down until the reward cycle itself rolls over per `get_reward_cycle`.

The scenario described conflates "the .signers contract is updated for the next cycle" with "the current cycle number changes for blocks still being mined." Those are different events separated by the remainder of the prepare phase (typically many blocks), and the node's own `reward_cycle` tag on `BlockProposal` (which the signer strictly checks against) is derived from the same burn-height arithmetic the signer uses — so they can't diverge in the way the question assumes at the exact prepare-phase-start boundary.

<cite repo="EzraCole/stacks-core--013" path="stackslib/src/chainstate/nakamoto/signer_set.rs" start="939="943" /> [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

#No vulnerability found for this question.

### Citations

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L957-969)
```rust
        // now, determine if we are in a prepare phase, and we are the first
        //  block in this prepare phase in our fork
        if !pox_constants.is_in_prepare_phase(first_block_height, burn_tip_height.into()) {
            // if we're not in a prepare phase, don't need to do anything
            return Ok(None);
        }

        let Some(cycle_of_prepare_phase) =
            pox_constants.reward_cycle_of_prepare_phase(first_block_height, burn_tip_height.into())
        else {
            // if we're not in a prepare phase, don't need to do anything
            return Ok(None);
        };
```

**File:** stackslib/src/burnchains/burnchain.rs (L590-609)
```rust
    /// Is this the first block to be signed by the signer set in cycle N?
    /// This is the mod 0 block.
    pub fn is_naka_signing_cycle_start(&self, burn_height: u64) -> bool {
        self.pox_constants
            .is_naka_signing_cycle_start(self.first_block_height, burn_height)
    }

    /// return the first burn block which receives reward in `reward_cycle`.
    /// this is the modulo 1 block
    pub fn reward_cycle_to_block_height(&self, reward_cycle: u64) -> u64 {
        self.pox_constants
            .reward_cycle_to_block_height(self.first_block_height, reward_cycle)
    }

    /// the first burn block that must be *signed* by the signer set of `reward_cycle`.
    /// this is the modulo 0 block
    pub fn nakamoto_first_block_of_cycle(&self, reward_cycle: u64) -> u64 {
        self.pox_constants
            .nakamoto_first_block_of_cycle(self.first_block_height, reward_cycle)
    }
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L293-303)
```rust

        let reward_cycle_id = burnchain
            .block_height_to_reward_cycle(election_sortition.block_height)
            .expect("FATAL: tried to initialize coordinator before first burn block height");

        let block_proposal = BlockProposal {
            block: block.clone(),
            burn_height: election_sortition.block_height,
            reward_cycle: reward_cycle_id,
            block_proposal_data: BlockProposalData::from_current_version(miner_diagnostic_data),
        };
```

**File:** stacks-signer/src/runloop.rs (L114-118)
```rust
    /// Get the reward cycle for a specific burnchain block height
    pub const fn get_reward_cycle(&self, burnchain_block_height: u64) -> u64 {
        let blocks_mined = burnchain_block_height.saturating_sub(self.first_burnchain_block_height);
        blocks_mined / self.reward_cycle_length
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

**File:** stacks-signer/src/v0/signer.rs (L1582-1589)
```rust
        if block_proposal.reward_cycle != self.reward_cycle {
            // We are not signing for this reward cycle. Ignore the block.
            debug!(
                "{self}: Received a block proposal for a different reward cycle. Ignore it.";
                "requested_reward_cycle" => block_proposal.reward_cycle
            );
            return;
        }
```
