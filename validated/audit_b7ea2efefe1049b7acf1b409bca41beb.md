### Title
Rounding mismatch between global-state agreement threshold and the on-chain block-signing threshold lets a sub-70% minority dictate the signer's global view (current miner, burn view, protocol version, tx-replay set) - (File: `libsigner/src/v0/signer_state.rs`)

### Summary
This is the closest reachable analog to the "Golden God" bug: a naive boundary/rounding calculation used for a critical invariant produces a result that is inconsistent with the "real" threshold defined elsewhere in the same codebase, silently breaking an equality the protocol depends on ("aggregated-weight vs verified-accepts").

### Finding Description
`GlobalStateEvaluator::reached_agreement` and `reached_disagreement` compute the 70%/30% thresholds with plain floor integer division: [1](#0-0) 

```
vote_weight >= total_weight * 7 / 10          // floor, no ceiling
vote_weight >  total_weight * 3 / 10          // floor, no ceiling
```

Compare this to the threshold actually used to validate a block's signer signatures in chainstate, `NakamotoBlockHeader::compute_voting_weight_threshold`, which explicitly rounds **up** (ceiling) to guarantee a strict ≥70% majority: [2](#0-1) 

For any `total_weight` where `total_weight * 7` is not a multiple of 10 (i.e., most values), the two computations diverge by exactly 1 unit of weight. E.g. `total_weight = 3`: `compute_voting_weight_threshold` requires `ceil(2.1) = 3` (100%), but `reached_agreement` accepts `vote_weight = 2` (66.7%) as "agreement" (`2 >= floor(2.1) = 2`).

`reached_agreement` is not cosmetic — it is the sole gate for determining the signer's adopted **global state**: the active signer protocol version, the global burn-block view, the current-miner viewpoint, and the transaction replay set: [3](#0-2) [4](#0-3) 

That evaluated global state is what `LocalStateMachine::capitulate_viewpoint`/`capitulate_miner_view`/`update_protocol_version` use to overwrite this signer's own local view (current miner, tx replay set, protocol version), which in turn drives whether the signer follows/validates a miner's tenure and whether it participates in transaction replay: [5](#0-4) [6](#0-5) 

So a coalition holding strictly less than the real 70%-rounded-up threshold (e.g. exactly the floor value, one weight unit below what block-signature verification would demand) can force this signer to "agree" on and capitulate to a stale/incorrect current miner, burn view, tx-replay set, or protocol version — a value that a genuine ≥70%-weighted quorum (as chainstate defines it) never actually endorsed. This is the exact class of bug in the source report: two independently-computed boundary values (here, floor vs. ceiling division of the same percentage) are supposed to represent the same invariant but disagree at the margin, and the margin case is reachable by ordinary weight distributions, not requiring a signer majority.

### Impact Explanation
This breaks the intended equality between "the weight that block-signature verification treats as a supermajority" and "the weight the signer's own state machine treats as global agreement." A minority below the real quorum can steer a signer's local view of the current miner / tx-replay set / active protocol version. This maps to the High-severity analog explicitly allowed by the rules: "a signer wedged into ... acting on a stale reward set/threshold" (here, acting on a stale/incorrect global-state consensus that a true 70% quorum never reached). It does not require a majority of signers or another signer's key — only an off-by-one weight distribution that is common in practice (any `total_weight` not evenly divisible into tenths of the threshold, i.e. most signer sets).

### Likelihood Explanation
Likelihood is moderate-to-high: it triggers deterministically whenever `total_weight * 7 mod 10 != 0`, which is the common case for arbitrary signer weight distributions (unless total weight is a multiple of 10). No malicious majority is required — any minority holding the floor-threshold weight (one unit less than the true ceiling threshold) reaching consensus among themselves on a value is sufficient to flip this signer's global-state view.

### Recommendation
Make `reached_agreement`/`reached_disagreement` use the same ceiling-rounding convention as `NakamotoBlockHeader::compute_voting_weight_threshold`, ideally by having `libsigner`'s `GlobalStateEvaluator` call (or mirror byte-for-byte) that same threshold function, so the "global agreement" invariant and the on-chain block-signing invariant can never diverge.

### Proof of Concept
1. Configure a reward set with weights summing to `total_weight = 3` (e.g., three signers each weight 1, or weights 1/1/1).
2. `NakamotoBlockHeader::compute_voting_weight_threshold(3)` returns `3` (all three signers must sign for a block to be accepted on-chain) — [7](#0-6) .
3. Two of the three signers (`vote_weight = 2`) send matching `StateMachineUpdate`s for a given current-miner/burn-view/tx-replay-set value.
4. `GlobalStateEvaluator::reached_agreement(2)` with `total_weight = 3` evaluates `2 >= (3*7)/10 = 2` → `true`, so `determine_global_state`/`determine_global_burn_view`/`determine_latest_supported_signer_protocol_version` report consensus reached at only 66.7% weight — [4](#0-3) .
5. The third signer, upon seeing this "global state," calls `capitulate_viewpoint`/`capitulate_miner_view`, which uses `eval.determine_global_state()`/`determine_latest_supported_signer_protocol_version()` to overwrite its own local `current_miner`/`tx_replay_set`/`active_signer_protocol_version` — [8](#0-7)  — even though this 2-of-3 (66.7%) view never met the 70%-rounded-up bar (`3/3`) that block-signature verification would require for a corresponding block-level decision.

Note: I could not fully trace every downstream consumer of `capitulate_miner_view`'s output within the remaining tool budget (e.g., exact interplay with `SortitionsView`/`is_tenure_valid` re-checks that might independently veto the capitulated miner); this should be verified further in a live session to confirm whether any secondary check fully neutralizes the practical impact.

### Citations

**File:** libsigner/src/v0/signer_state.rs (L56-79)
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
```

**File:** libsigner/src/v0/signer_state.rs (L101-144)
```rust
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
```

**File:** libsigner/src/v0/signer_state.rs (L169-183)
```rust
    /// Check if the supplied vote weight crosses the global agreement threshold.
    /// Returns true if it has, false otherwise.
    pub fn reached_agreement(&self, vote_weight: u32) -> bool {
        u64::from(vote_weight)
            >= u64::from(self.total_weight).strict_mul(NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
                / 10
    }

    /// Check if the supplied vote weight crosses the blocking minority threshold.
    /// Returns true if it has, false otherwise.
    pub fn reached_disagreement(&self, vote_weight: u32) -> bool {
        u64::from(vote_weight)
            > u64::from(self.total_weight).strict_mul(10 - NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
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

**File:** stacks-signer/src/v0/signer_state.rs (L798-838)
```rust
    fn update_protocol_version(
        &mut self,
        stacks_client: &StacksClient,
        eval: &mut GlobalStateEvaluator,
        local_supported_signer_protocol_version: u64,
    ) {
        // Before we ever access eval...we should make sure to include our own local state machine update message in the evaluation
        let Ok(local_update) =
            self.try_into_update_message_with_version(local_supported_signer_protocol_version)
        else {
            return;
        };

        let old_protocol_version = local_update.active_signer_protocol_version;
        eval.insert_update(
            stacks_client.get_signer_address().clone(),
            local_update.clone(),
        );
        // Check if we should update our active protocol version
        let active_signer_protocol_version = eval
            .determine_latest_supported_signer_protocol_version()
            .unwrap_or(old_protocol_version);

        if active_signer_protocol_version != old_protocol_version {
            info!("Signer State: Updating active signer protocol version from {old_protocol_version} to {active_signer_protocol_version}");
            crate::monitoring::actions::increment_signer_agreement_state_change_reason(
                crate::monitoring::SignerAgreementStateChangeReason::ProtocolUpgrade,
            );
            let (burn_block, burn_block_height) = local_update.content.burn_block_view();
            let current_miner = local_update.content.current_miner();
            let tx_replay_set = local_update.content.tx_replay_set();

            *self = Self::Initialized(SignerStateMachine {
                burn_block: burn_block.clone(),
                burn_block_height,
                current_miner: current_miner.clone().into(),
                active_signer_protocol_version,
                tx_replay_set,
            });
        }
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
