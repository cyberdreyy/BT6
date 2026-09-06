### Title
`GlobalStateEvaluator::reached_agreement`/`reached_disagreement` floor the 70%/30% threshold instead of using the ceiling used everywhere else, letting sub-quorum weight be treated as global-state consensus - ([File: libsigner/src/v0/signer_state.rs])

### Summary
The bug-class analog from the Sublime report is "integer division that rounds a fee/weight calculation down to zero (or below the intended cutoff), letting an actor evade the true threshold." In this codebase the analogous rounding-down happens in the signer-side global state-machine agreement check, `GlobalStateEvaluator::reached_agreement` / `reached_disagreement`, which use plain floor division (`total_weight * threshold / 10`) instead of the ceiling formula used by the canonical chainstate threshold check `NakamotoBlockHeader::compute_voting_weight_threshold`.

### Finding Description
The authoritative 70% supermajority computation used for actual block-signature verification is `NakamotoBlockHeader::compute_voting_weight_threshold`, which explicitly rounds **up**: [1](#0-0) 

By contrast, the signer-side `GlobalStateEvaluator` (used to determine the signer-set's agreed protocol version, burn view, current miner, and tx-replay set) computes agreement with a plain floor division and no ceiling correction: [2](#0-1) 

Because `total_weight * 7 / 10` is floored, any `total_weight` that is not an exact multiple of 10 produces a threshold that is strictly *lower* than the true 70% cutoff (e.g. `total_weight = 13` gives `floor(13*7/10) = 9`, i.e. `9/13 ≈ 69.2%` is accepted as "reached agreement" even though it is under 70%). The regression tests in this file confirm the developers were aware of overflow issues in this exact formula but did not add the ceiling correction that the chainstate version has: [3](#0-2) 

`reached_agreement` is the sole gate used by `determine_latest_supported_signer_protocol_version`, `determine_global_burn_view`, and `determine_global_state` to decide what the signer set has collectively "agreed" on (protocol version, current miner, burn view, tx-replay set): [4](#0-3) [5](#0-4) 

This creates an equality break between the threshold that the node/chainstate treats as canonical 70% supermajority (`compute_voting_weight_threshold`, ceiling) and the threshold the signer's own state machine treats as "global agreement reached" (`reached_agreement`, floor). A signer can therefore lock in an `active_signer_protocol_version`, `current_miner`, or `tx_replay_set` as the "global state" on weight that is provably below the real 70% supermajority whenever `total_weight` is not a multiple of 10.

### Impact Explanation
This falls under the High-impact category: "a signer... acting on a stale reward set/threshold." Because `determine_global_state` feeds directly into what miner the signer considers "current" and what protocol version/tx-replay set is considered agreed, a signer can act (including signing blocks per its adopted state machine view) on a state that did not actually reach the intended 70% supermajority, deviating from the strict threshold enforced elsewhere (`compute_voting_weight_threshold`, `verify_signer_signatures`). This is a genuine mismatch between two implementations of "the same" 70%/30% threshold concept in the same codebase, not merely a cosmetic issue.

### Likelihood Explanation
The rounding error triggers deterministically whenever `total_weight * 7` is not a multiple of 10, which is common for realistic reward-set weight totals (not just adversarially chosen ones), so it is highly likely to occur in normal operation rather than requiring a contrived edge case. It requires no majority collusion or malicious message crafting beyond the normal flow of `StateMachineUpdate` gossip that already exists in the protocol; it is a latent logic bug present for every legitimately participating signer.

### Recommendation
Make `reached_agreement`/`reached_disagreement` use the same ceiling-based computation as `NakamotoBlockHeader::compute_voting_weight_threshold` (i.e., compute `threshold = ceil(total_weight * THRESHOLD / 10)` and compare `vote_weight >= threshold` for agreement, and mirror the analogous ceiling logic for the disagreement/blocking-minority check), so that the signer-side global state machine's notion of "70% supermajority" is always at least as strict as the chainstate's.

### Proof of Concept
With `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD = 7` (70%) and a reward set whose signers sum to `total_weight = 13` (e.g., weights `[2,2,2,2,2,2,1]`):
- True 70% cutoff (chainstate): `compute_voting_weight_threshold(13) = ceil(13*7/10) = ceil(9.1) = 10` (as validated by the existing unit test pattern in `stackslib/src/chainstate/nakamoto/tests/mod.rs` round-up assertions).
- Signer-side `reached_agreement(9)` (using `libsigner/src/v0/signer_state.rs:171-175`): `9 >= floor(13*7/10) = 9` → **true**, even though `9/13 ≈ 69.2% < 70%`.

This means a signer set can converge on (and the local signer will act on) a "global state" (protocol version / current miner / burn view / tx replay set) backed by only 9 of 13 weight units — below the canonical 70% supermajority that block signing itself requires — purely due to the missing ceiling correction in `reached_agreement`.

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

**File:** libsigner/src/tests/signer_state.rs (L758-787)
```rust
#[test]
/// Regression for the same u32-overflow class as `reached_agreement_no_u32_overflow`,
/// but on the disagreement path. Here the multiplier is `10 - threshold = 3`,
/// so the wrap point is `total_weight > u32::MAX / 3 ≈ 1_431_655_765`, well
/// above the agreement wrap point (≈ 613M). The agreement test uses
/// `total_weight = 1_000_000_000` and so doesn't cover this path.
///
/// At `total_weight = 2_000_000_000`, the buggy `total_weight * 3` wrapped to
/// 1_705_032_704 and `/ 10` landed at 170_503_270. That made ~8.5% of total
/// weight look like a blocking minority instead of the required > 30%.
fn reached_disagreement_no_u32_overflow() {
    let evaluator = evaluator_with_total_weight(2_000_000_000);

    // One past the pre-fix wrap value (~8.5% of total). Must not count as a
    // blocking minority.
    assert!(
        !evaluator.reached_disagreement(170_503_271),
        "~8.5% of total_weight must not satisfy the >30% disagreement check"
    );
    // Exactly 30%: strict `>`, so still not disagreement.
    assert!(
        !evaluator.reached_disagreement(600_000_000),
        "exactly 30% must not satisfy the strict > threshold"
    );
    // One unit past 30%: disagreement.
    assert!(
        evaluator.reached_disagreement(600_000_001),
        "just above 30% must satisfy the threshold"
    );
}
```
