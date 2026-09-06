### Title
`GlobalStateEvaluator::reached_agreement` uses floor-rounded 70% threshold while block-signature consensus uses ceiling-rounded threshold, letting sub-70%-weight signers force a global-state decision - (File: `libsigner/src/v0/signer_state.rs`)

### Summary
The canonical 70% supermajority threshold used for Nakamoto block-signature acceptance/rejection is computed with a ceiling division (`NakamotoBlockHeader::compute_voting_weight_threshold`), guaranteeing that at least mathematically-true 70% of weight is required. The parallel, weight-based supermajority check used by the signer set's `GlobalStateEvaluator` (which decides the active signer protocol version, the agreed burn-block view, and the agreed miner/tenure state fed into every subsequent proposal/pre-commit/signature decision) uses a floor-rounded division instead, so at specific `total_weight` values it accepts strictly less than 70% weight as "agreement."

### Finding Description
`NakamotoBlockHeader::compute_voting_weight_threshold` rounds the 70% threshold up (ceiling): [1](#0-0) 

This ceiling-based threshold is used consistently for the security-critical block acceptance/rejection paths: chainstate signature verification, and the signer's own accept/reject/pre-commit tallies: [2](#0-1) [3](#0-2) [4](#0-3) 

In contrast, `GlobalStateEvaluator::reached_agreement` (and its complement `reached_disagreement`) computes the same "70% of `total_weight`" boundary with a plain integer (floor) division, not a ceiling: [5](#0-4) 

For any `total_weight` where `total_weight * 7` is not a multiple of 10, `compute_voting_weight_threshold` rounds up while `reached_agreement`'s inline `/10` rounds down, e.g. `total_weight = 11` gives `ceil(77/10)=8` vs `floor(77/10)=7`. A vote weight of `7` (63.6% of `11`) is therefore treated by `GlobalStateEvaluator` as having "reached agreement," one weight-unit short of the true 70% supermajority that the rest of the protocol enforces everywhere else.

This threshold function is the sole gatekeeper for:
- `determine_latest_supported_signer_protocol_version` — decides which signer-protocol version is globally active [6](#0-5) 
- `determine_global_burn_view` — decides the agreed-upon burn block/height [7](#0-6) 
- `determine_global_state` — decides the agreed `SignerStateMachine` (current miner, tenure, protocol version) and the agreed transaction-replay set [8](#0-7) 

All of these feed the global-state-machine version of the signer, which other signers use to decide who the active miner is and which chain of blocks/tenure to sign for. A rounding gap of one weight-unit at the 70% boundary means a minority coalition just below the true supermajority can push a `current_miner`/`burn_block`/`active_signer_protocol_version` decision into "agreed" state that would not have cleared the real 70% bar used everywhere else in the protocol (block signature threshold, rejection threshold, pre-commit threshold). This mirrors the reported bug class exactly: two code paths compute what should be the same guarantee ("70% of weight") using different rounding, and one path silently accepts a smaller value than intended — exactly as the Revert Lend report shows two different bases (fullValue vs debt) producing a fee that no longer matches the documented 10%-of-debt invariant.

Note: there already exists a regression test in this codebase guarding against a *different*, more severe u32-overflow bug in this same function (`reached_agreement_no_u32_overflow`), which confirms this exact function is a known point of scrutiny for threshold-arithmetic correctness — but that fix only widened the multiplication to u64; it did not change floor to ceiling, so the rounding-direction mismatch with `compute_voting_weight_threshold` remains. [9](#0-8) 

### Impact Explanation
This breaks the "aggregated weight vs verified accepts" equality: the aggregated-weight threshold that gates global-state decisions (miner identity, burn view, protocol version, replay set) is *weaker* than the verified/canonical 70% threshold used for actual block signing. A signer subset holding slightly less than the documented 70% weight can force the rest of the fleet's `GlobalStateEvaluator`-driven logic to treat a particular miner/tenure/burn-view as the agreed global state. Since `current_miner`/burn-view agreement steers which blocks a signer is willing to propose/pre-commit/sign for, this can be leveraged to steer honest signers toward signing for a miner/tenure that a true 70%-weight supermajority never actually endorsed, or to accept/lock-in a stale burn view/protocol version below the intended safety margin. This is a Medium-severity design-invariant break (analogous to the cited report's judged severity): it does not by itself forge a signature or accept an invalid block, but it corrupts a decision that all subsequent proposal/pre-commit/signing decisions are conditioned on, undermining the guaranteed 70% supermajority safety margin the protocol advertises.

### Likelihood Explanation
Triggerable by any coalition of signers (or a single signer plus normal gossip of `StateMachineUpdate` messages) whose combined weight lands in the narrow gap between the floor-rounded and ceiling-rounded thresholds for the current `total_weight`. This gap exists for the majority of possible `total_weight` values (any `total_weight` not evenly divisible such that `total_weight*7 % 10 == 0`), so it is reachable in essentially every real reward-cycle weight distribution, not just contrived edge cases. It requires only normal message propagation (StateMachineUpdate gossip already in scope) — no majority key compromise, no auth_token, no local access.

### Recommendation
Make `reached_agreement`/`reached_disagreement` use the same ceiling-rounding logic as `NakamotoBlockHeader::compute_voting_weight_threshold` (or better, have `GlobalStateEvaluator` call that shared function directly) so that all supermajority/blocking-minority checks in the codebase use one consistent, ceiling-rounded 70%/30% threshold definition.

### Proof of Concept
For `total_weight = 11`:
- `NakamotoBlockHeader::compute_voting_weight_threshold(11)` = `ceil(11*7/10)` = `ceil(7.7)` = `8`.
- `GlobalStateEvaluator::reached_agreement(7)` evaluates `7 >= (11*7)/10 = 77/10 = 7` (integer floor) → `true`.

So a vote weight of `7` (63.6% of total weight `11`, below the true 70% bar of `8`) is accepted by `GlobalStateEvaluator::reached_agreement` as "agreement" for `determine_global_state`/`determine_global_burn_view`/`determine_latest_supported_signer_protocol_version`, while the same weight would be rejected by the canonical block-approval threshold check used in `stacks-signer/src/v0/signer.rs::store_and_process_block_signature` and `stackslib`'s `verify_signer_signatures`. [10](#0-9) [11](#0-10)

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

**File:** stacks-signer/src/v0/signer.rs (L2304-2313)
```rust
        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
        let total_reject_weight =
            self.compute_signature_signing_weight(rejection_addrs.iter().map(|(addr, _)| addr));
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
        if total_reject_weight.saturating_add(min_weight) <= total_weight {
```

**File:** stacks-signer/src/v0/signer.rs (L2494-2503)
```rust
        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
        let total_signature_weight = self.compute_signature_signing_weight(addrs_to_sigs.keys());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });

        if min_weight > total_signature_weight {
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
