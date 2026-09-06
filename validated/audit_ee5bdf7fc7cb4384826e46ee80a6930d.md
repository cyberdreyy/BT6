### Title
Inconsistent quorum-threshold rounding between `GlobalStateEvaluator::reached_agreement` and `NakamotoBlockHeader::compute_voting_weight_threshold` lets the signer state machine "agree" on a miner/burn-view below the real block-approval threshold - (File: `libsigner/src/v0/signer_state.rs`)

### Summary
Two independent implementations compute the same "70% of signer weight" quorum but round differently: one rounds up (ceiling), the other truncates (floor). This mirrors the EIP-150 report's root cause — a safety check computed with one rounding rule while the enforced/authoritative behavior uses a stricter rule — creating a window where a check "passes" without the real invariant holding.

### Finding Description
The authoritative block-approval/rejection threshold used to actually sign, reject, and broadcast a block is computed with ceiling (round-up) division: [1](#0-0) 

```
let ceil = if (total_weight * threshold) % 10 == 0 { 0 } else { 1 };
u32::try_from((total_weight * threshold) / 10 + ceil)
```

This `compute_voting_weight_threshold` is what `stacks-signer/src/v0/signer.rs` uses everywhere a real consensus decision is made: block acceptance (`store_and_process_block_signature`), block rejection (`handle_block_rejection`), and pre-commit tallying (`handle_block_pre_commit`). [2](#0-1) [3](#0-2) [4](#0-3) 

In contrast, `GlobalStateEvaluator::reached_agreement`, which drives the *global signer state machine* (agreed burn view, agreed current miner, agreed protocol version, agreed tx-replay set), truncates (floors) instead of ceiling: [5](#0-4) 

```
pub fn reached_agreement(&self, vote_weight: u32) -> bool {
    u64::from(vote_weight) >= u64::from(self.total_weight).strict_mul(NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD) / 10
}
```

Whenever `total_weight * 7` is not evenly divisible by 10, `compute_voting_weight_threshold` requires exactly one more weight-unit than `reached_agreement`'s floor value. This means `GlobalStateEvaluator` will conclude the network has "reached agreement" on a state view (miner, burn view, protocol version, replay set) with strictly less weight than the amount required by `compute_voting_weight_threshold` to actually sign/reject a block.

`reached_agreement` is load-bearing for consensus-critical decisions:
- `determine_global_state` / `determine_global_burn_view` / `determine_latest_supported_signer_protocol_version` (the canonical "what does the network agree on" logic). [6](#0-5) 
- `capitulate_miner_view`, which flips a signer's local view of the *current active miner* and can mark the previously-active miner invalid, feeding directly into which blocks the signer will validate/sign next. [7](#0-6) 

So the equality broken is: **"agreed" (state-machine consensus threshold) vs. "verified-accepted" (block-approval consensus threshold)** — these two quorum computations should be identical but are not, due to the rounding mismatch.

### Impact Explanation
This falls under the High-impact category of "acting on a stale/miscomputed threshold." Because `reached_agreement`'s bar is systematically lower (by exactly 1 weight unit whenever `total_weight * 7 % 10 != 0`) than the bar actually required to approve/reject a block, the global state machine can capitulate to a new miner view, a new burn view, or a new protocol version at a support level that would not be sufficient to actually sign a block under `compute_voting_weight_threshold`. This can cause a signer to switch its notion of the canonical/active miner (and therefore what it will validate and sign) based on a quorum that is inconsistent with — and weaker than — the one enforced elsewhere in the same codebase for block acceptance/rejection. This is not a crash or DoS; it's a genuine protocol-invariant violation between two components that are supposed to agree on the same 70% threshold.

### Likelihood Explanation
This does not require a majority of signers or any privileged access — it only requires a distribution of signer weights (which can occur naturally, or be engineered by any subset of participants who control enough weight to land support exactly at `floor(0.7 * total_weight)`, one unit short of `ceil(0.7 * total_weight)`) and does not require the majority-collusion threshold explicitly excluded by the rules. Any reward cycle where `total_weight * 7 mod 10 != 0` (the common case, since `total_weight` is rarely a multiple of 10) exhibits this discrepancy whenever weight support sits exactly at the floor boundary.

### Recommendation
Make `GlobalStateEvaluator::reached_agreement` (and `reached_disagreement`) use the same ceiling-based threshold computation as `NakamotoBlockHeader::compute_voting_weight_threshold`, ideally by having both call into a single shared function, so that "state-machine agreement" and "block-approval threshold" can never diverge due to differing rounding rules.

### Proof of Concept
Given `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD = 7` (out of 10) and a signer set with `total_weight = 11`:
- `compute_voting_weight_threshold(11)`: `11*7=77`, `77/10=7` remainder `7`, so `ceil=1` → threshold = `8`.
- `reached_agreement` with `vote_weight = 7`: `7 >= 77/10 = 7` (integer floor) → `true`.

So a coalition holding weight `7` out of `11` (63.6%, below the intended 70% threshold and below the `8` required by `compute_voting_weight_threshold`) is treated by `GlobalStateEvaluator` as having "reached agreement" on a miner/burn-view/protocol-version state, while the exact same weight would *not* be sufficient to approve or reject a block via `compute_voting_weight_threshold` used in `stacks-signer/src/v0/signer.rs`. This directly demonstrates the two "70%" thresholds diverging by exactly the rounding gap, analogous to the EIP-150 63/64 rounding gap that let a check pass without the real underlying condition holding.

### Citations

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

**File:** stacks-signer/src/v0/signer.rs (L1298-1301)
```rust
        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
```

**File:** stacks-signer/src/v0/signer.rs (L2309-2313)
```rust
        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
        if total_reject_weight.saturating_add(min_weight) <= total_weight {
```

**File:** stacks-signer/src/v0/signer.rs (L2498-2503)
```rust
        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });

        if min_weight > total_signature_weight {
```

**File:** libsigner/src/v0/signer_state.rs (L56-99)
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

**File:** stacks-signer/src/v0/signer_state.rs (L928-978)
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
```
