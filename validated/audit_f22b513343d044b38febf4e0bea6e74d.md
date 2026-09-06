### Title
Inconsistent rounding direction in `GlobalStateEvaluator::reached_agreement` allows sub-70% weight to dictate signer global state - ([File: libsigner/src/v0/signer_state.rs])

### Summary
`GlobalStateEvaluator::reached_agreement` computes the 70% supermajority threshold by **flooring** (`total_weight * 7 / 10`), whereas the analogous and more security-critical `NakamotoBlockHeader::compute_voting_weight_threshold` **ceils** the same 70% calculation. This mirrors the ERC-4626 rounding-direction bug class: one path favors the protocol/majority (round up, more weight required) while the sibling path favors the caller (round down, less weight required) for what is supposed to be the same "70% supermajority" invariant.

### Finding Description
`compute_voting_weight_threshold` (block-signature threshold, used to gate whether enough signers approved/rejected a block) explicitly rounds up: [1](#0-0) 

This is verified by its own test, including an explicit "Round-up check" (`total_weight=511` → `358`, i.e. `ceil(511*7/10)`): [2](#0-1) 

By contrast, `GlobalStateEvaluator::reached_agreement`, which is used by every signer to decide whether a 70% supermajority of *signer state-machine updates* (active miner, burn view, protocol version, and — critically — the transaction replay set enforced during reorgs) has been reached, performs plain **integer floor division** and then a `>=` comparison: [3](#0-2) 

For `total_weight=511`, this floors to `357` instead of the ceiling value `358` that `compute_voting_weight_threshold` produces for the identical 70% calculation — a real, provable off-by-one in the same direction as the audited AutoRoller bug (rounding the wrong way for a check that is supposed to require "at least X%"). The existing overflow-regression tests only exercise `total_weight` values exactly divisible by 10/70 (`1_000_000_000`, `2_000_000_000`), so the floor-vs-ceiling discrepancy at non-clean `total_weight` values is untested and unnoticed: [4](#0-3) [5](#0-4) 

`reached_agreement` gates multiple safety-relevant decisions inside the signer's global-state machine: which miner is considered the "active miner" and canonical burn view, which signer protocol version is in force, and — most importantly — the transaction replay set that must be enforced across a reorg: [6](#0-5) [7](#0-6) [8](#0-7) 

### Impact Explanation
Because `reached_agreement` under-counts the true 70% bar by up to one weight unit, a coalition of signers (or a set of state-machine-update messages gossiped by them) holding slightly less than the intended 70% supermajority — but at or above the floored threshold — can force the local signer to "lock in" a global state view: an active-miner/burn-view determination, a signer protocol version, or, most consequentially, a transaction replay set (`find_majority_prefix_replay_set` / `determine_global_state`). Since `reached_agreement`'s result is used to decide the enforced replay set and active-miner view that downstream signing logic in `stacks-signer/src/v0/signer.rs` consults, an attacker who can influence just under the "true" 70% threshold (but over the flawed floored one) can cause the local signer's notion of consensus to diverge from the value a strictly-correct 70% ceiling check would have produced. This falls into the reportable class of "aggregated-weight vs verified-accepts" equality breaks: the code intends to gate a decision on `>= ceil(0.7 * total_weight)` but actually gates it on `>= floor(0.7 * total_weight)`, a concrete, provable safety-relevant miscount (not requiring a majority — the entire bug is that it requires *less* than the majority the code claims to enforce). Severity is bounded by the magnitude of the discrepancy (at most one weight unit, i.e. a fraction of a percent for realistic weight distributions), so real-world exploitability depends on weight granularity being coarse enough that "one unit" is a meaningful fraction of the signer set.

### Likelihood Explanation
This requires no majority takeover and no special permissions — only that the naturally-gossiped signer state-machine updates (`StateMachineUpdate` messages processed by any signer, one-slot-miner-adjacent surface) land at a `total_weight`/vote_weight combination where `total_weight * 7` is not a multiple of 10 (the common case for most non-trivial signer sets), and where the true supporting weight sits in the one-unit gap between `floor(0.7*W)` and `ceil(0.7*W)`. This is a narrow, deterministic-once-reached window rather than a broadly exploitable primitive, so likelihood is low-to-moderate and mostly a robustness/consistency defect rather than a readily weaponizable attack absent a very specific, tightly-balanced weight distribution.

### Recommendation
Make `reached_agreement` use the same ceiling semantics as `NakamotoBlockHeader::compute_voting_weight_threshold` (e.g. compute `threshold = (total_weight as u64 * NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD + 9) / 10` or reuse `compute_voting_weight_threshold` directly) so the two "70% supermajority" checks in the codebase are provably consistent, and add a regression test at a `total_weight` where `total_weight * 7 % 10 != 0` (e.g. `511`) asserting `reached_agreement` matches `compute_voting_weight_threshold`'s rounded-up value.

### Proof of Concept
1. Set `total_weight = 511` (or any weight where `total_weight * 7 % 10 != 0`).
2. `NakamotoBlockHeader::compute_voting_weight_threshold(511)` → `358` (ceiling, verified by existing test at `stackslib/src/chainstate/nakamoto/tests/mod.rs:4118-4122`).
3. `GlobalStateEvaluator::reached_agreement(357)` with `total_weight = 511` → returns `true` (`357 >= floor(511*7/10) = 357`), even though `357/511 ≈ 69.86% < 70%`.
4. This demonstrates the two "70% supermajority" gates in the same signer codebase disagree by exactly one weight unit at the boundary — the `GlobalStateEvaluator` path accepts sub-70% agreement while the block-signature path correctly requires ≥70% (rounded up).

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1194-1207)
```rust
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

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L4118-4122)
```rust
        // Round-up check
        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(511_u32).unwrap(),
            358_u32,
        );
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

**File:** libsigner/src/v0/signer_state.rs (L196-270)
```rust
    fn find_majority_prefix_replay_set(
        &self,
        tx_replay_sets: &HashMap<ReplayTransactionSet, u32>,
    ) -> Option<ReplayTransactionSet> {
        if tx_replay_sets.is_empty() {
            return None;
        }

        // First, try to find an exact match that reaches agreement
        for (replay_set, weight) in tx_replay_sets {
            if self.reached_agreement(*weight) {
                return Some(replay_set.clone());
            }
        }

        // No exact agreement found, find longest common prefix with majority support

        // Sort replay sets by weight (descending), then deterministically by length and content
        let mut sorted_sets: Vec<_> = tx_replay_sets.iter().collect();
        sorted_sets.sort_by(|(set_a, weight_a), (set_b, weight_b)| {
            // Primary: weight descending
            let weight_cmp = weight_b.cmp(weight_a);
            if weight_cmp != Ordering::Equal {
                return weight_cmp;
            }
            // Secondary: length descending (longer sequences first)
            let len_cmp = set_b.0.len().cmp(&set_a.0.len());
            if len_cmp != Ordering::Equal {
                return len_cmp;
            }
            // Tertiary: compare transaction IDs for determinism
            for (lhs, rhs) in set_a.0.iter().zip(&set_b.0) {
                let ord = lhs.txid().cmp(&rhs.txid());
                if ord != Ordering::Equal {
                    return ord;
                }
            }
            Ordering::Equal
        });

        // Start with the most supported replay set as initial candidate
        if let Some((initial_set, _)) = sorted_sets.first() {
            let mut candidate_prefix = initial_set.0.clone();
            let mut total_supporting_weight = 0u32;

            // Find all sets that support the current candidate prefix
            for (replay_set, weight) in tx_replay_sets {
                if replay_set.0.starts_with(&candidate_prefix) {
                    total_supporting_weight = total_supporting_weight.saturating_add(*weight);
                }
            }

            // If the initial candidate already has majority support, return it
            if self.reached_agreement(total_supporting_weight) {
                return Some(ReplayTransactionSet::new(candidate_prefix));
            }

            // Otherwise, iteratively truncate the prefix until we find majority support
            while !candidate_prefix.is_empty() {
                // Remove the last transaction from the prefix
                candidate_prefix.pop();

                // Recalculate supporting weight for the shorter prefix
                total_supporting_weight = 0u32;
                for (replay_set, weight) in tx_replay_sets {
                    if replay_set.0.starts_with(&candidate_prefix) {
                        total_supporting_weight = total_supporting_weight.saturating_add(*weight);
                    }
                }

                // If this prefix has majority support, return it
                if self.reached_agreement(total_supporting_weight) {
                    return Some(ReplayTransactionSet::new(candidate_prefix));
                }
            }
```

**File:** libsigner/src/tests/signer_state.rs (L731-756)
```rust
#[test]
/// Regression: u32 multiplication in `reached_agreement` wrapped silently in
/// release builds for `total_weight > u32::MAX / 7 ≈ 613_566_756`. With
/// `total_weight = 1_000_000_000` the buggy expression `total_weight * 7 / 10`
/// wrapped to ~270_503_270, allowing roughly 27% of total weight to satisfy
/// the 70% supermajority check. The fix widens to u64 first.
fn reached_agreement_no_u32_overflow() {
    let evaluator = evaluator_with_total_weight(1_000_000_000);

    // Pre-fix wrap landed at 270_503_270; assert that vote_weight at the wrapped
    // value is correctly rejected — i.e., 27% does not satisfy 70%.
    assert!(
        !evaluator.reached_agreement(270_503_270),
        "27% of total_weight must not satisfy the 70% threshold"
    );
    // Boundary: exactly 70% must pass.
    assert!(
        evaluator.reached_agreement(700_000_000),
        "70% of total_weight must satisfy the 70% threshold"
    );
    // Just below 70% must fail.
    assert!(
        !evaluator.reached_agreement(699_999_999),
        "below 70% must not satisfy the threshold"
    );
}
```
