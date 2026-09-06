### Title
Global signer state agreement threshold uses floor division while block-approval threshold uses ceiling, allowing "agreement" to be reached below the true supermajority — ([File: libsigner/src/v0/signer_state.rs])

### Summary
`GlobalStateEvaluator::reached_agreement` computes the 70% supermajority threshold with plain floor division (`total_weight * 7 / 10`), while the node/chainstate-side block-approval threshold `NakamotoBlockHeader::compute_voting_weight_threshold` computes the same nominal 70% with an explicit ceiling adjustment. When `total_weight` is not a multiple of 10, these two "70%" thresholds diverge, and the signer-side global-state agreement check requires strictly less weight than the canonical block-approval threshold to consider consensus reached.

### Finding Description
`reached_agreement` in [1](#0-0)  is:
```rust
pub fn reached_agreement(&self, vote_weight: u32) -> bool {
    u64::from(vote_weight)
        >= u64::from(self.total_weight).strict_mul(NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
            / 10
}
```
This is a pure floor division of `total_weight * 7 / 10`, with no rounding-up.

By contrast, the canonical, node-enforced block signature threshold `NakamotoBlockHeader::compute_voting_weight_threshold` explicitly rounds up: [2](#0-1) 
```rust
pub fn compute_voting_weight_threshold(total_weight: u32) -> Result<u32, ChainstateError> {
    let threshold = NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD;
    let total_weight = u64::from(total_weight);
    let ceil = if (total_weight * threshold) % 10 == 0 { 0 } else { 1 };
    u32::try_from((total_weight * threshold) / 10 + ceil)...
}
```

For any `total_weight` where `total_weight * 7` is not divisible by 10 (e.g. `total_weight = 11`), `compute_voting_weight_threshold` returns `8` (72.7%), but `reached_agreement` treats `7` (63.6%) as sufficient. This is exactly analogous to the reported bug class: two logically-equivalent "70% of total" computations use different rounding conventions, and the less-strict one is used to gate a security-relevant decision.

`reached_agreement` gates `GlobalStateEvaluator::determine_global_state`, `determine_global_burn_view`, and `determine_latest_supported_signer_protocol_version` — all in [3](#0-2) . These functions decide the *canonical current miner*, *canonical burn view*, and *tx replay set* that feed directly into `LocalStateMachine::capitulate_viewpoint` / `capitulate_miner_view` in [4](#0-3) , which can flip a signer's local view of the active miner (`sortition_state.cur_sortition.miner_status`) and cause it to invalidate or accept a competing tenure.

### Impact Explanation
Because the global-state "agreement" threshold is looser than the canonical 70% block-approval threshold, a minority coalition of signers (holding as little as ~63.6% instead of the intended ≥70% in the boundary case) could cause `determine_global_state`/`determine_global_burn_view` to converge on a *current miner* / *burn view* that a stricter (ceiling-based) accounting would not yet ratify. Signers that capitulate their local state machine to this prematurely-"agreed" global view (`capitulate_viewpoint`) may switch to treating a different miner as canonical and mark the previous miner's sortition as `InvalidatedBeforeFirstBlock`, diverging from what the true supermajority (per node consensus rules) would have selected. This is a state-machine correctness/equivocation-guard weakening (maps to the "High" impact category: a signer acting on a stale/incorrectly-thresholded view of the reward set consensus), rather than a fully unilateral invalid-signature forgery, since `verify_signer_signatures` on the node still uses the correct ceiling formula for actual block signature counting.

### Likelihood Explanation
This requires no majority of signers and no key compromise — it is triggered purely by the existing signer set's weight distribution whenever `total_weight * 7` is not a multiple of 10 (a very common case, since weights are apportioned in integer reward slots) combined with a coalition reaching the lower (floor) bound rather than the higher (ceiling) bound. This is a deterministic, reachable divergence in every reward cycle whose total weight isn't a multiple of 10.

### Recommendation
Make `reached_agreement` (and its counterpart `reached_disagreement`) use the same ceiling-rounding formula as `NakamotoBlockHeader::compute_voting_weight_threshold`, ideally by calling that shared function (or a shared helper) instead of independently re-deriving the threshold with different rounding behavior. This guarantees the signer-side "global agreement" concept is always consistent with the node's actual block-approval threshold.

### Proof of Concept
For `total_weight = 11` (e.g., 11 equal-weight signers):
- `compute_voting_weight_threshold(11)` → `(11*7)/10 = 7`, remainder `77 % 10 = 7 ≠ 0` → `ceil = 1` → threshold = `8` (need ≥8/11 ≈ 72.7%).
- `reached_agreement` with `vote_weight = 7`: `7 >= (11*7)/10 = 77/10 = 7` (integer floor) → `true`.

So 7 of 11 signers (63.6%) are treated by `GlobalStateEvaluator` as having reached global agreement on a miner/burn view/protocol version, even though the node's own supermajority threshold formula would require 8 of 11 (72.7%) to consider the equivalent quantity approved. This divergence is directly analogous to the reported `_feeDenominatorAdjusted` rounding bug: two computations meant to represent the same fractional threshold diverge due to inconsistent rounding, and the more permissive one governs a security-relevant decision path.

### Citations

**File:** libsigner/src/v0/signer_state.rs (L56-158)
```rust
    /// Determine what the maximum signer protocol version that a majority of signers can support
    pub fn determine_latest_supported_signer_protocol_version(&self) -> Option<u64> {
        let mut protocol_versions = HashMap::new();
        for (address, update) in &self.address_updates {
            let Some(weight) = self.address_weights.get(address) else {
                continue;
            };
            let entry = protocol_versions
                .entry(update.local_supported_signer_protocol_version)
                .or_insert_with(|| 0);
            *entry += weight;
        }
        // find the highest version number supported by a threshold number of signers
        let mut protocol_versions: Vec<_> = protocol_versions.into_iter().collect();
        protocol_versions.sort_by_key(|(version, _)| *version);
        let mut total_weight_support: u32 = 0;
        for (version, weight_support) in protocol_versions.into_iter().rev() {
            total_weight_support += weight_support;
            if self.reached_agreement(total_weight_support) {
                return Some(version);
            }
        }
        None
    }

    /// Determine what the global burn view is if there is one
    pub fn determine_global_burn_view(&self) -> Option<(&ConsensusHash, u64)> {
        let mut burn_blocks = HashMap::new();
        for (address, update) in &self.address_updates {
            let Some(weight) = self.address_weights.get(address) else {
                continue;
            };
            let (burn_block, burn_block_height) = update.content.burn_block_view();

            let entry = burn_blocks
                .entry((burn_block, burn_block_height))
                .or_insert_with(|| 0);
            *entry += weight;
            if self.reached_agreement(*entry) {
                return Some((burn_block, burn_block_height));
            }
        }
        None
    }

    /// Check if there is an agreed upon global state
    pub fn determine_global_state(&self) -> Option<SignerStateMachine> {
        let active_signer_protocol_version =
            self.determine_latest_supported_signer_protocol_version()?;
        let mut state_views = HashMap::new();
        let mut tx_replay_sets = HashMap::new();
        let mut found_state_view = None;
        let mut found_replay_set = None;
        for (address, update) in &self.address_updates {
            let Some(weight) = self.address_weights.get(address) else {
                continue;
            };
            let (burn_block, burn_block_height) = update.content.burn_block_view();
            let current_miner = update.content.current_miner();
            let tx_replay_set = update.content.tx_replay_set();

            let state_machine = SignerStateMachine {
                burn_block: burn_block.clone(),
                burn_block_height,
                current_miner: current_miner.clone().into(),
                active_signer_protocol_version,
                // We need to calculate the threshold for the tx_replay_set separately
                tx_replay_set: ReplayTransactionSet::none(),
            };
            let key = SignerStateMachineKey(state_machine.clone());
            let entry = state_views.entry(key).or_insert_with(|| 0);
            *entry += weight;

            if self.reached_agreement(*entry) {
                found_state_view = Some(state_machine);
            }

            let replay_entry = tx_replay_sets
                .entry(tx_replay_set.clone())
                .or_insert_with(|| 0);
            *replay_entry += weight;

            if self.reached_agreement(*replay_entry) {
                found_replay_set = Some(tx_replay_set);
            }
            if found_replay_set.is_some() && found_state_view.is_some() {
                break;
            }
        }
        // Try to find agreed replay set, or find longest common prefix if no exact agreement
        let final_replay_set = if let Some(tx_replay_set) = found_replay_set {
            tx_replay_set
        } else {
            // No exact agreement found, try finding longest common prefix with majority support
            self.find_majority_prefix_replay_set(&tx_replay_sets)
                .unwrap_or_else(ReplayTransactionSet::none)
        };

        if let Some(state_view) = found_state_view.as_mut() {
            state_view.tx_replay_set = final_replay_set;
        }
        found_state_view
    }
```

**File:** libsigner/src/v0/signer_state.rs (L169-175)
```rust
    /// Check if the supplied vote weight crosses the global agreement threshold.
    /// Returns true if it has, false otherwise.
    pub fn reached_agreement(&self, vote_weight: u32) -> bool {
        u64::from(vote_weight)
            >= u64::from(self.total_weight).strict_mul(NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
                / 10
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1192-1207)
```rust
    /// Compute the threshold for the minimum number of signers (by weight) required
    /// to approve a Nakamoto block.
    pub fn compute_voting_weight_threshold(total_weight: u32) -> Result<u32, ChainstateError> {
        let threshold = NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD;
        let total_weight = u64::from(total_weight);
        let ceil = if (total_weight * threshold) % 10 == 0 {
            0
        } else {
            1
        };
        u32::try_from((total_weight * threshold) / 10 + ceil).map_err(|_| {
            ChainstateError::InvalidStacksBlock(
                "Overflow when computing nakamoto block approval threshold".to_string(),
            )
        })
    }
```

**File:** stacks-signer/src/v0/signer_state.rs (L888-979)
```rust
    /// Updates the local state machine's viewpoint as necessary based on the global state
    #[allow(clippy::too_many_arguments)]
    pub fn capitulate_viewpoint(
        &mut self,
        stacks_client: &StacksClient,
        signerdb: &mut SignerDb,
        eval: &mut GlobalStateEvaluator,
        local_supported_signer_protocol_version: u64,
        sortition_state: &mut Option<SortitionsView>,
        capitulate_miner_view_timeout: Duration,
        tenure_last_block_proposal_timeout: Duration,
        last_capitulate_miner_view: &mut SystemTime,
    ) {
        // We should do this without waiting for capitulation checks, as protocol version updates are orthogonal to capitulation
        self.update_protocol_version(stacks_client, eval, local_supported_signer_protocol_version);

        if !self.is_capitulation_check_ready(
            signerdb,
            local_supported_signer_protocol_version,
            capitulate_miner_view_timeout,
            last_capitulate_miner_view,
        ) {
            return;
        }
        *last_capitulate_miner_view = SystemTime::now();
        // First, update our parent tenure last block if needed. We may have timed out our view of it.
        // This is a bit of an expensive call (due to call for node tip) so we don't want to do it if
        // the node is advancing with our participation.
        self.update_parent_tenure_last_block(
            stacks_client,
            signerdb,
            local_supported_signer_protocol_version,
            tenure_last_block_proposal_timeout,
        );
        let Ok(local_update) =
            self.try_into_update_message_with_version(local_supported_signer_protocol_version)
        else {
            return;
        };

        // Is there a miner view to which we should capitulate?
        let Some(new_miner) = self.capitulate_miner_view(
            stacks_client,
            eval,
            signerdb,
            &local_update,
            tenure_last_block_proposal_timeout,
        ) else {
            return;
        };

        let (burn_block, burn_block_height) = local_update.content.burn_block_view();
        let current_miner = local_update.content.current_miner();
        let tx_replay_set = local_update.content.tx_replay_set();

        if current_miner != &new_miner {
            info!("Signer State: Capitulating local state machine's current miner viewpoint";
                "current_miner" => ?current_miner,
                "new_miner" => ?new_miner,
                "burn_block" => %burn_block,
                "burn_block_height" => burn_block_height,
                "tx_replay_set" => ?tx_replay_set,
            );
            crate::monitoring::actions::increment_signer_agreement_state_change_reason(
                crate::monitoring::SignerAgreementStateChangeReason::MinerViewUpdate,
            );
            Self::monitor_miner_parent_tenure_update(current_miner, &new_miner);

            *self = Self::Initialized(SignerStateMachine {
                burn_block: burn_block.clone(),
                burn_block_height,
                current_miner: new_miner.clone().into(),
                active_signer_protocol_version: local_update.active_signer_protocol_version,
                tx_replay_set,
            });

            match new_miner {
                StateMachineUpdateMinerState::ActiveMiner {
                    current_miner_pkh, ..
                } => {
                    if let Some(sortition_state) = sortition_state {
                        // if there is a mismatch between the new_miner ad the current sortition view, mark the current miner as invalid
                        if current_miner_pkh != sortition_state.cur_sortition.data.miner_pkh {
                            sortition_state.cur_sortition.miner_status =
                                SortitionMinerStatus::InvalidatedBeforeFirstBlock
                        }
                    }
                }
                StateMachineUpdateMinerState::NoValidMiner => (),
            }
        }
    }
```
