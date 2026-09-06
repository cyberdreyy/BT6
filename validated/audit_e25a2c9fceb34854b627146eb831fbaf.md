## Finding

### Title
`GlobalStateEvaluator::reached_agreement`/`reached_disagreement` floor the 70% supermajority threshold instead of rounding up, letting the signer's global-state consensus (miner view / burn view / protocol version / tx-replay-set) be reached with less than the canonical block-approval threshold — ([File: libsigner/src/v0/signer_state.rs])

### Summary
The Tapioca report flags a division that always rounds down when it should round up, silently under-delivering the intended amount. The same rounding-direction defect exists in the signer's own supermajority check: `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` use floor (truncating) integer division for the 70%/30% thresholds, while the canonical, consensus-enforced threshold used to actually accept a block's signatures (`NakamotoBlockHeader::compute_voting_weight_threshold`) explicitly rounds up (ceiling). The two "same conceptual 70%" checks in the codebase disagree by exactly one weight unit whenever `total_weight * 7` is not a multiple of 10.

### Finding Description
`GlobalStateEvaluator::reached_agreement` and `reached_disagreement` compute: [1](#0-0) 

```rust
pub fn reached_agreement(&self, vote_weight: u32) -> bool {
    u64::from(vote_weight)
        >= u64::from(self.total_weight).strict_mul(NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
            / 10
}

pub fn reached_disagreement(&self, vote_weight: u32) -> bool {
    u64::from(vote_weight)
        > u64::from(self.total_weight).strict_mul(10 - NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
            / 10
}
```

This is plain integer division, i.e. `floor(total_weight * 7 / 10)`. Compare this to the threshold that actually gates whether a Nakamoto block's aggregated signer signatures are accepted as consensus-valid, `NakamotoBlockHeader::compute_voting_weight_threshold`, which deliberately rounds *up*: [2](#0-1) 

```rust
pub fn compute_voting_weight_threshold(total_weight: u32) -> Result<u32, ChainstateError> {
    let threshold = NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD;
    let total_weight = u64::from(total_weight);
    let ceil = if (total_weight * threshold) % 10 == 0 { 0 } else { 1 };
    u32::try_from((total_weight * threshold) / 10 + ceil).map_err(...)
}
```

`compute_voting_weight_threshold` is used both by chainstate signature verification (`verify_signer_signatures`, lines 1180-1190 of the same file) and by the signer's own block-acceptance path (`stacks-signer/src/v0/signer.rs`, `store_and_process_block_signature` / `handle_block_pre_commit` / `handle_block_rejection`), all of which call `min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)`. That is the *canonical* 70% bar.

`GlobalStateEvaluator::reached_agreement`, on the other hand, is the bar used to decide the signer's *global state machine* view — the current miner, the burn view, the active protocol version, and the transaction replay set: [3](#0-2) 

That result feeds directly into `SignerStateMachine::capitulate_viewpoint` / `capitulate_miner_view`, which switches the signer's local view of "who is the current miner" and "what tenure it's building on": [4](#0-3) 

For `total_weight = 101` (not a multiple of 10, so the two rounding modes diverge): `101 * 7 = 707`, `707 / 10 = 70` (floor) vs. `70 + 1 = 71` (ceil). So:
- `GlobalStateEvaluator::reached_agreement(70)` returns `true` (70/101 ≈ 69.3% is treated as "agreement reached").
- `NakamotoBlockHeader::compute_voting_weight_threshold(101)` requires `71` (≥70.3%) to actually accept a block's signatures.

This is confirmed by the existing regression tests, which document that this constant assumes threshold `== 7` and separately validate `compute_voting_weight_threshold`'s ceiling behavior: [5](#0-4) [6](#0-5) 

### Impact Explanation
A minority coalition holding exactly `floor(total_weight*7/10)` weight (one unit short of the true supermajority) can force the `GlobalStateEvaluator` to declare "global agreement" on a `current_miner`, burn view, protocol version, or tx replay set that does not actually have the canonical 70% backing required elsewhere in the protocol. Because this drives `capitulate_miner_view`/`capitulate_viewpoint` (which switches which miner/tenure the signer treats as legitimate), a signer can be steered into adopting a view of the network's state that is inconsistent with the actual majority — a discrepancy between the signer's internally "aggregated weight" decision and the "verified" 70% threshold enforced by chainstate for block acceptance. This is the same equality-break class the rules call out ("aggregated-weight vs verified-accepts"): the signer's local consensus bookkeeping can settle on a view backed by strictly less than the weight required to actually get a block accepted, creating a mismatch between what the signer believes is the agreed current miner/tenure and what the network can actually approve, which can manifest as signers stuck disagreeing with the true canonical tip (liveness wedge) or, when capitulation swings the local view, prematurely trusting a miner view without true supermajority support.

### Likelihood Explanation
The rounding gap is deterministic and total-weight-dependent, not attacker-controlled in size (it is always exactly 0 or 1 weight unit), and requires no majority — any coalition able to reach `floor(total_weight*7/10)` (which is strictly less than the true 70%) triggers the divergence whenever `total_weight * 7 mod 10 != 0`, which is the common case for arbitrary reward-set weight totals (most values of `total_weight` are not multiples of 10 in the `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD == 7` case, i.e., `total_weight` not divisible by 10).

### Recommendation
Make `reached_agreement`/`reached_disagreement` use the same ceiling-rounding logic as `NakamotoBlockHeader::compute_voting_weight_threshold` (or call that function directly) so that the signer's internal notion of "70% supermajority reached" is never weaker than the canonical, chainstate-enforced block-approval threshold.

### Proof of Concept
With `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD = 7` and `total_weight = 101`:
1. `NakamotoBlockHeader::compute_voting_weight_threshold(101)` returns `71` — a block only gets accepted with ≥71 weight of signatures.
2. `GlobalStateEvaluator { total_weight: 101, .. }.reached_agreement(70)` returns `true` — the signer's global-state evaluator declares agreement with only `70` weight.
3. A set of signers whose combined weight is exactly `70` (i.e., short of the `71` actually required for block/consensus acceptance) can drive `determine_global_state`/`determine_global_burn_view`/`determine_latest_supported_signer_protocol_version` to "lock in" a view (current miner, burn view, protocol version, or tx replay set) that lacks the canonical supermajority, feeding into `capitulate_viewpoint` in `stacks-signer/src/v0/signer_state.rs`.

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

**File:** stacks-signer/src/v0/signer_state.rs (L928-962)
```rust
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
```

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L4118-4123)
```rust
        // Round-up check
        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(511_u32).unwrap(),
            358_u32,
        );
    }
```

**File:** libsigner/src/tests/signer_state.rs (L712-718)
```rust
/// wrap value (170_503_271 for `reached_disagreement_no_u32_overflow`) that are
/// only correct when the supermajority constant is 7. If this assert ever
/// fires, the test values must be recomputed deliberately, not just bumped.
const _: () = assert!(
    NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD == 7,
    "threshold tests in this file assume NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD == 7"
);
```
