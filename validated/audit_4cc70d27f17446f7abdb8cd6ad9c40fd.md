### Title
Floor-division in `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` diverges from the block-approval ceiling threshold, letting the global-state-machine (miner-view/burn-view/protocol-version) consensus be declared with less than the true 70% supermajority - (File: `libsigner/src/v0/signer_state.rs`)

### Summary
`GlobalStateEvaluator::reached_agreement` and `reached_disagreement` compute the 70%/30% supermajority thresholds using plain floor (integer) division, whereas the actual Nakamoto block-signature approval threshold used by the node/chainstate, `NakamotoBlockHeader::compute_voting_weight_threshold`, uses ceiling division. This is structurally the same rounding-down defect as the referenced `getPrice` finding: a threshold comparison that should require "at least X%" instead accepts "at least floor(X%)", so for certain `total_weight` values the signer-side global-agreement check fires with strictly less accumulated weight than the protocol's actual approval threshold.

### Finding Description
The node-side (consensus-enforced) threshold is defined with an explicit ceiling correction: [1](#0-0) 

```
pub fn compute_voting_weight_threshold(total_weight: u32) -> Result<u32, ChainstateError> {
    let threshold = NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD;
    let total_weight = u64::from(total_weight);
    let ceil = if (total_weight * threshold) % 10 == 0 { 0 } else { 1 };
    u32::try_from((total_weight * threshold) / 10 + ceil)...
}
```

This guarantees the required weight is `ceil(total_weight * 7 / 10)` — e.g. for `total_weight = 13`, `13*7=91`, `91/10=9` remainder `1` → ceil adds 1 → threshold = `10`.

The signer-side `GlobalStateEvaluator`, used to determine consensus on the *global signer state machine* (burn view, active miner, protocol version, tx-replay set) implements the comparison with plain floor division and no ceiling correction: [2](#0-1) 

```
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

For `total_weight = 13`, `13*7/10 = 9` (floor, no ceiling) → `reached_agreement` fires at vote_weight `9`, i.e. `9/13 ≈ 69.2%`, one weight-unit *below* the node's actual `10/13 ≈ 76.9%` threshold used for `compute_voting_weight_threshold`. This is exactly analogous to the report's rounding-down `if` comparison: the boundary condition is evaluated with truncated integer division instead of the ceiling-correct division that the equivalent consensus-critical code path uses elsewhere in the same codebase.

`reached_agreement`/`reached_disagreement` gate every "global agreement" decision that a one-slot signer's own vote (crafted via a `StateMachineUpdate` gossip message) can tip over the boundary, including:
- `determine_latest_supported_signer_protocol_version` — [3](#0-2) 
- `determine_global_burn_view` — [4](#0-3) 
- `determine_global_state` (miner/tx-replay consensus) — [5](#0-4) 
- `capitulate_miner_view`, which uses `reached_disagreement`/`reached_agreement` to decide whether a signer should switch its local view of the "current miner" to a different (potentially non-canonical) miner — [6](#0-5) 

Because a miner-view capitulation directly changes which miner's proposals a signer will subsequently treat as canonical/valid and be willing to sign for, an attacker who controls fractional weight near the rounding boundary (e.g. one slot whose weight sits exactly at the truncated fraction) can cause the `GlobalStateEvaluator` to declare "agreement" on a competing/attacker-favored miner view, burn view, or tx-replay set one weight-unit earlier than the protocol's actual supermajority requires. This does not require a majority — it only requires the vote tally to land in the gap between `floor(total*7/10)` and `ceil(total*7/10)`, which one or a few colluding-weight signers plus the natural distribution of honest votes can produce given specific `total_weight` values (any `total_weight` not evenly divisible by 10 when multiplied by 7 exhibits this gap).

### Impact Explanation
This breaks an equality that the rest of the system (the actual block-header verification in `verify_signer_signatures`/`compute_voting_weight_threshold`) enforces strictly via ceiling division: the "70% agreement" invariant. When the signer-side gossip-driven global-state evaluator uses a laxer (floor) threshold than the consensus-side, signers can be steered into capitulating to a `current_miner` view, burn view, or protocol version that has not actually reached the true supermajority. This can cause a signer to subsequently sign a block proposal from a miner that does not have legitimate majority backing (or that is not canonical per the stricter node-side rule), i.e., a signer signing on behalf of an under-supported/potentially non-canonical view. This maps to the "signer signing an invalid/non-canonical block" and "liveness wedge" (mis-agreement causing signers to diverge or stall on capitulation) categories in the analog rules.

### Likelihood Explanation
No majority of signers or key compromise is required — the discrepancy is purely arithmetic and exists deterministically for any `total_weight` where `total_weight * 7` is not a multiple of 10 (i.e., most possible reward-set weight totals). It is reachable purely by ordinary state-machine-update gossip messages (`StateMachineUpdate`, `v0::messages`) that any registered signer can broadcast, requiring no majority and no protocol violation beyond crafting/timing normal votes to land on the rounding boundary.

### Recommendation
Make `reached_agreement`/`reached_disagreement` in `libsigner/src/v0/signer_state.rs` use the same ceiling-division formula as `NakamotoBlockHeader::compute_voting_weight_threshold` (i.e., compute `ceil(total_weight * threshold / 10)` for agreement, and the correct complementary ceiling/floor pairing for disagreement) so the signer-side global-state consensus threshold is provably identical to the node-side block-approval threshold.

### Proof of Concept
Given `total_weight = 13` and `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD = 7`:
- Node-side `compute_voting_weight_threshold(13)`: `13*7=91`, `91 % 10 = 1` → `ceil=1` → `91/10 + 1 = 10`. Node requires **10/13** weight to approve a block signature set.
- Signer-side `reached_agreement(9)`: `13*7/10 = 63/10... ` recompute: `13*7=91`, `91/10=9` (floor) → check `9 >= 9` → **true**. The `GlobalStateEvaluator` declares global agreement at **9/13** weight (≈69.2%), one unit below the node's actual 10/13 (≈76.9%) requirement.

This gap means a `current_miner`/burn-view/protocol-version consensus can be "reached" by the signer set's gossip layer at 9/13 weight, while the node's own consensus-critical `verify_signer_signatures` would demand 10/13 for an actual block signature quorum — an exploitable, deterministic inconsistency in the equality the protocol otherwise enforces strictly elsewhere.

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

**File:** libsigner/src/v0/signer_state.rs (L81-99)
```rust
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

**File:** libsigner/src/v0/signer_state.rs (L101-158)
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

**File:** stacks-signer/src/v0/signer_state.rs (L1019-1054)
```rust
        let mut miners = HashMap::new();
        let mut potential_matches = HashSet::new();

        for (address, update) in &eval.address_updates {
            let Some(weight) = eval.address_weights.get(address) else {
                continue;
            };
            let burn_block = update.content.burn_block_view().0;
            if burn_block != global_burn_block {
                continue;
            }
            let miner_state = update.content.current_miner();
            let StateMachineUpdateMinerState::ActiveMiner {
                tenure_id,
                parent_tenure_last_block_height,
                parent_tenure_id,
                ..
            } = miner_state
            else {
                // Only consider potential active miners
                continue;
            };

            let entry = miners.entry(miner_state).or_insert(0);
            *entry += weight;
            if !eval.reached_disagreement(*entry) {
                // We don't even see a blocking minority threshold. Ignore.
                continue;
            }

            let nmb_blocks = signerdb
                .get_globally_accepted_block_count_in_tenure(tenure_id)
                .unwrap_or(0);
            if nmb_blocks == 0 && !eval.reached_agreement(*entry) {
                continue;
            }
```
