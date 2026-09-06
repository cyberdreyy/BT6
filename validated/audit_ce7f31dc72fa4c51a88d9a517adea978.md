### Title
Signer-side global-state agreement threshold uses floor division while the node's block-approval threshold uses ceiling division, allowing a signer to act on "agreement" below the real consensus requirement - (File: `libsigner/src/v0/signer_state.rs`)

### Summary
`GlobalStateEvaluator::reached_agreement`/`reached_disagreement` compute the 70% agreement threshold with plain integer floor division, while the node/chainstate computation of the *same* threshold, `NakamotoBlockHeader::compute_voting_weight_threshold`, rounds up (ceiling). These two computations are supposed to represent the identical "70% of signer weight" quantity, but they diverge whenever `total_weight * 7` is not a multiple of 10 — the same class of precision-loss/rounding bug as the reference report (dividing before combining vs. dividing once, here manifesting as floor-vs-ceiling on the *same* logical threshold used in two different contexts).

### Finding Description
The node enforces block approval with a ceiling-rounded threshold: [1](#0-0) 

```rust
pub fn compute_voting_weight_threshold(total_weight: u32) -> Result<u32, ChainstateError> {
    let threshold = NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD;
    let total_weight = u64::from(total_weight);
    let ceil = if (total_weight * threshold) % 10 == 0 { 0 } else { 1 };
    u32::try_from((total_weight * threshold) / 10 + ceil)...
}
```

This is used both to validate signer signatures on a block (`verify_signer_signatures`, [2](#0-1) ) and by the signer itself when deciding whether it has gathered enough acceptance/rejection weight to broadcast a block (`stacks-signer/src/v0/signer.rs`, `store_and_process_block_signature` / `handle_block_rejection`, [3](#0-2)  and [4](#0-3) ).

However, the *global state machine* agreement check used by the signer's `GlobalStateEvaluator` — which determines the active signer protocol version, the agreed burn view, the agreed current-miner state-machine view, and critically the agreed **transaction replay set** — uses plain floor division with no ceiling adjustment: [5](#0-4) 

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

With `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD = 7` (70%), take `total_weight = 3`:
- Node-side `compute_voting_weight_threshold(3)` = `(3*7)/10 + ceil` = `2 + 1` = **3** (i.e., unanimity is required to hit 70% of 3 units).
- Signer-side `reached_agreement(vote_weight)`: threshold is `(3*7)/10` = **2** (floor, no ceiling).

So a signer set with total weight 3 needs all 3 units to satisfy the node's actual 70% block-approval threshold, but the signer's own `GlobalStateEvaluator::reached_agreement` will report "agreement reached" with only 2 out of 3 units of vote weight. This mismatch is directly analogous to the referenced `getCommunityVotingPower` bug: two computations meant to express the same percentage threshold diverge due to how/when the division is rounded, producing a materially different (looser) result on the signer side than what the node treats as canonical.

### Impact Explanation
`reached_agreement` gates several signer state-machine decisions in `determine_latest_supported_signer_protocol_version`, `determine_global_burn_view`, and `determine_global_state` (which derives `tx_replay_set`) — [6](#0-5) . Because the effective agreement threshold here is systematically laxer than the one the node enforces for block-signature acceptance, a signer can settle on a "global state" (including which transaction-replay set to enforce during block validation via `submit_block_for_validation`, [7](#0-6) ) with a weight coalition that would not actually satisfy the canonical 70% threshold used elsewhere in the protocol (block approval, rejection consensus). This is a threshold/equality mismatch between the signer's internal consensus view and the node's canonical consensus view — the class of bug the rules classify as High ("acting on a stale reward set/threshold").

### Likelihood Explanation
This requires no majority collusion and no privileged access: it is purely a function of `total_weight` and how weight happens to be distributed among signer entries for a given reward cycle (any `total_weight` where `total_weight * 7` is not a multiple of 10, e.g. any total weight not divisible by 10, triggers the discrepancy). This is deterministic and will occur naturally in ordinary reward-cycle configurations, not something an attacker needs to specially craft — it's a systemic rounding bug rather than a targeted exploit, but it is reliably reachable.

### Recommendation
Make `reached_agreement`/`reached_disagreement` use the same ceiling-rounding formula as `NakamotoBlockHeader::compute_voting_weight_threshold` (or better, have `GlobalStateEvaluator` call `compute_voting_weight_threshold` directly) so that the signer-side and node-side notions of "70% agreement" are always identical, eliminating the possibility of a coalition satisfying the signer's local threshold without satisfying the node's canonical threshold.

### Proof of Concept
1. Configure a reward cycle whose signer weights sum to `total_weight = 3` (e.g., three signers with weight 1 each), so `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD = 7`.
2. Node-side: `NakamotoBlockHeader::compute_voting_weight_threshold(3)` returns `3` (unanimity required) — [8](#0-7) .
3. Signer-side: have 2 of the 3 signers broadcast matching `StateMachineUpdate`s (e.g., same burn view / same tx replay set). `GlobalStateEvaluator::reached_agreement(2)` with `total_weight=3` evaluates `2 >= (3*7)/10 = 2` → `true` — [9](#0-8) .
4. The third signer's `determine_global_state()` therefore locks in this 2-of-3 view (including its `tx_replay_set`) as the "global state," even though this same 2-of-3 weight would fail the node's canonical `compute_voting_weight_threshold` of `3`, i.e., it would not be sufficient to actually get a block signed/approved on-chain.

This confirms the divergence between the signer's internally-used agreement threshold and the canonical, node-enforced block-approval threshold, exactly mirroring the reference report's "divide-then-sum vs sum-then-divide" precision mismatch pattern.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1180-1189)
```rust
        let threshold = Self::compute_voting_weight_threshold(total_weight)?;

        if total_weight_signed < threshold {
            return Err(ChainstateError::InvalidStacksBlock(format!(
                "Not enough signatures. Needed at least {} but got {} (out of {})",
                threshold, total_weight_signed, total_weight,
            )));
        }

        return Ok(total_weight_signed);
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

**File:** stacks-signer/src/v0/signer.rs (L2613-2622)
```rust
        match stacks_client.submit_block_for_validation(
            block.clone(),
            if self.validate_with_replay_tx {
                self.global_state_evaluator
                    .get_global_tx_replay_set()
                    .unwrap_or_default()
                    .clone_as_optional()
            } else {
                None
            },
```

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
