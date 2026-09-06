### Title
Global state agreement threshold uses floor rounding while the canonical Nakamoto approval threshold uses ceiling rounding, letting a sub-70% weight coalition force `current miner`/burn-view/protocol-version agreement - (File: `libsigner/src/v0/signer_state.rs`)

### Summary
`GlobalStateEvaluator::reached_agreement`/`reached_disagreement` compute the 70%/30% supermajority thresholds with plain floor integer division, while the canonical threshold used everywhere else in the codebase (`NakamotoBlockHeader::compute_voting_weight_threshold`, used both by node-side signature verification and by the signer's own block acceptance/pre-commit gating) rounds the same threshold **up**. This is the same class of bug as the external report: an internal helper reimplements a threshold/rounding computation with different, inconsistent rounding behavior than the canonical implementation, so the same nominal "70%" boundary produces two different integer cut-offs depending on which code path is used.

### Finding Description
The canonical Nakamoto 70% supermajority threshold is: [1](#0-0) 

which ceils `total_weight * 7 / 10`. This is the threshold enforced by the node when it verifies collected signer signatures on a block header, and it is the same threshold `stacks-signer` itself uses to decide when it has enough pre-commits/signatures to sign or broadcast a block: [2](#0-1) [3](#0-2) 

However, `GlobalStateEvaluator`, which decides the *global state machine agreement* used to determine the agreed current miner, burn view, active signer protocol version, and tx-replay set, computes the "same" 70%/30% thresholds with a different (floor) formula: [4](#0-3) 

For any `total_weight` where `total_weight * 7` is not exactly divisible by 10, `ceil(total_weight*7/10) = floor(total_weight*7/10) + 1`. This means there exists a `vote_weight` equal to the floor value that satisfies `reached_agreement` (loose threshold) while it does **not** satisfy `compute_voting_weight_threshold` (canonical/strict threshold) — i.e. a coalition that is exactly one weight-unit short of the true 70% supermajority is nonetheless treated by `GlobalStateEvaluator` as having reached "agreement."

`reached_agreement` is the gate that decides:
- the agreed active signer protocol version (`determine_latest_supported_signer_protocol_version`)
- the agreed global burn view (`determine_global_burn_view`)
- the agreed current-miner/tx-replay-set global state (`determine_global_state`) [5](#0-4) 

and `reached_agreement`/`reached_disagreement` together gate `capitulate_miner_view`, which is what causes a signer to switch its belief about which miner is "current" and therefore whose blocks it will treat as canonical/sign: [6](#0-5) 

So the "current miner" viewpoint that ultimately gates block proposal handling can flip in favor of a candidate backed by strictly less than the canonical 70% weight required by `compute_voting_weight_threshold` (the same threshold the node enforces on the actual block signature aggregate). The `reached_disagreement` (30% blocking-minority) check has the mirrored inconsistency in the opposite direction versus the "blocking minority" definition used elsewhere (`total_weight - compute_voting_weight_threshold(total_weight)`), e.g. in the node's rejection accounting: [7](#0-6) 

### Impact Explanation
This breaks the intended equality between "aggregated signer weight" and "verified/canonical supermajority weight": the global state machine (which determines the canonical current miner and burn view a signer will act on) can reach "agreement" using a strictly weaker rounding rule than the one the network actually enforces for block acceptance. A signer can therefore capitulate its miner viewpoint — and subsequently propose/sign/process blocks under the assumption that a given miner is canonical — based on a coalition weight that is below the true 70% supermajority the protocol is designed to require. This is the direct analog of the reported bug class: an internal rounding/threshold helper diverges from the canonical rounding used elsewhere, producing a wrong accept/reject decision at specific (non-majority-scale) boundary weight distributions.

### Likelihood Explanation
The discrepancy is exactly one weight-unit wide and only manifests when `total_weight * 7` is not a multiple of 10 (i.e., `total_weight mod 10 != 0`), which is common since signer weights are stake-derived and rarely round evenly. It requires signer weight distribution to land near this boundary — not a majority of colluding signers, just the natural weight assignment for a given reward cycle — so it is a low-effort, naturally-occurring condition rather than requiring an attacker-controlled majority.

### Recommendation
Make `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` use the exact same ceiling-rounding formula as `NakamotoBlockHeader::compute_voting_weight_threshold` (or better, call that shared function directly) so that "reaching agreement" in the global state machine is always consistent with the canonical, node-enforced 70% supermajority definition.

### Proof of Concept
With `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD = 7`, take `total_weight = 11`:
- Canonical: `compute_voting_weight_threshold(11) = ceil(77/10) = 8`.
- `GlobalStateEvaluator::reached_agreement(7)`: `7 >= floor(77/10) = 7` → `true`.

So a set of signers/updates summing to weight `7` out of `11` (63.6%, below the true 70% cutoff of 8/11 = 72.7%) makes `determine_global_state`/`determine_global_burn_view`/`capitulate_miner_view` treat that view as globally agreed, even though the exact same nominal 70% threshold, computed via `compute_voting_weight_threshold`, would reject it (`7 < 8`). This is verifiable directly against the existing test scaffolding in [8](#0-7)  and the threshold unit test in [9](#0-8) .

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

**File:** stacks-signer/src/v0/signer.rs (L1296-1301)
```rust
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
```

**File:** stacks-signer/src/v0/signer.rs (L2496-2501)
```rust
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
```

**File:** libsigner/src/v0/signer_state.rs (L56-144)
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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-522)
```rust
            if block_status
                .total_weight_rejected
                .saturating_add(self.weight_threshold)
                > self.total_weight
            {
                info!(
                    "{}/{} signer weight votes to reject block",
                    block_status.total_weight_rejected, self.total_weight;
                    "signer_signature_hash" => %block_signer_sighash,
                );
                counters.bump_naka_rejected_blocks();

                // Only act on failed txids that a blocking minority (>30% weight) agrees on
                let blocking_minority = self.total_weight.saturating_sub(self.weight_threshold);
```

**File:** libsigner/src/tests/signer_state.rs (L712-757)
```rust
/// wrap value (170_503_271 for `reached_disagreement_no_u32_overflow`) that are
/// only correct when the supermajority constant is 7. If this assert ever
/// fires, the test values must be recomputed deliberately, not just bumped.
const _: () = assert!(
    NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD == 7,
    "threshold tests in this file assume NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD == 7"
);

/// Builds a `GlobalStateEvaluator` with empty address maps and the given
/// `total_weight`. Threshold helpers (`reached_agreement` /
/// `reached_disagreement`) only read `total_weight`, so the maps can stay empty.
fn evaluator_with_total_weight(total_weight: u32) -> GlobalStateEvaluator {
    GlobalStateEvaluator {
        address_weights: HashMap::new(),
        address_updates: HashMap::new(),
        total_weight,
    }
}

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

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L4096-4123)
```rust
    #[test]
    pub fn test_compute_voting_weight_threshold() {
        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(100_u32).unwrap(),
            70_u32,
        );

        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(10_u32).unwrap(),
            7_u32,
        );

        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(3000_u32).unwrap(),
            2100_u32,
        );

        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(4000_u32).unwrap(),
            2800_u32,
        );

        // Round-up check
        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(511_u32).unwrap(),
            358_u32,
        );
    }
```
