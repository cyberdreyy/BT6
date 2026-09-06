### Title
Global state agreement threshold uses floor-rounding while the canonical block-signing threshold uses ceil-rounding, letting `GlobalStateEvaluator` accept sub-70% weight as "consensus" - (File: `libsigner/src/v0/signer_state.rs`)

### Summary
`GlobalStateEvaluator::reached_agreement` computes the 70% supermajority threshold with truncating (floor) integer division, while the canonical, consensus-critical threshold used for actual block-signature verification, `NakamotoBlockHeader::compute_voting_weight_threshold`, uses ceiling division. Both derive from the same nominal constant `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD == 7` ("70%"), but for any `total_weight` where `total_weight * 7` is not a multiple of 10, the two computations diverge by one unit of weight — exactly the class of shares↔assets round-trip mismatch described in the EigenLayer report, where two independently rounded views of "the same ratio" silently disagree.

### Finding Description
The chainstate-side, consensus-enforced threshold rounds up: [1](#0-0) 

This ceil-based threshold is what actually gates a Nakamoto block header's signature validity in `verify_signer_signatures`, and it is also what `stacks-signer` reuses for its own pre-commit/acceptance/rejection tallies: [2](#0-1) [3](#0-2) 

However, `GlobalStateEvaluator::reached_agreement` — used to compute the *global* signer state machine (agreed miner state, agreed active protocol version, agreed burn view, agreed tx-replay set) — recomputes the same nominal "70%" ratio with a floor division and a `>=` comparison instead of the ceil used elsewhere: [4](#0-3) 

For `total_weight = 511`: `compute_voting_weight_threshold(511) == 358` (ceil), but `reached_agreement` is satisfied at `vote_weight = 357` (`357/511 ≈ 69.86% < 70%`). This is confirmed by the existing unit test for the "real" threshold: [5](#0-4) 

`reached_agreement` is used to decide the *entire* global state machine result — active protocol version, burn view, current-miner state, and tx-replay set — all through `determine_global_state`/`determine_latest_supported_signer_protocol_version`/`determine_global_burn_view`: [6](#0-5) 

This mirrors the EigenLayer bug class exactly: two mathematically-related conversions of the same nominal ratio (here, "70% of total weight") are implemented with different rounding directions in different parts of the codebase, so an equality/threshold check that is supposed to represent one consistent supermajority invariant is silently violated by up to one weight unit whenever the division isn't exact.

### Impact Explanation
This falls under the listed High-impact category "acting on a stale reward set/threshold": the global signer state machine (which determines the agreed current miner, active protocol version, burn view, and transaction-replay set across the whole signer set) can be finalized by `GlobalStateEvaluator` with strictly less than the true 70% supermajority weight that the rest of the protocol (chainstate block-signature verification, pre-commit/acceptance/rejection tallies in `stacks-signer/src/v0/signer.rs`) requires. Any consumer of `determine_global_state`/`reached_agreement` output that assumes it represents a genuine ≥70%-weight consensus (e.g. gating protocol version activation, miner-state agreement, or transaction-replay-set agreement) is operating on a threshold that is inconsistent with the canonical one, which can produce a divergent, inconsistent notion of "the agreed global state" between the loosely-rounded evaluator and the strictly-rounded on-chain/pre-commit logic.

### Likelihood Explanation
This triggers deterministically whenever `total_weight * 7` is not a multiple of 10 (the common case for realistic signer weight distributions), and requires no majority collusion, malicious signer, or privileged access — it is a pure arithmetic inconsistency baked into normal signer-set operation. It also does not require a majority of signers to exploit: a set of signers whose combined weight is one unit below the true ceil-based 70% threshold can already cause `reached_agreement` to return `true`.

### Recommendation
Make `GlobalStateEvaluator::reached_agreement` (and its complement `reached_disagreement`) use the same ceiling-based computation as `NakamotoBlockHeader::compute_voting_weight_threshold`, ideally by having one call the other (or sharing a single canonical threshold function), so that "70% of total weight" is defined identically everywhere in the codebase.

### Proof of Concept
```rust
// libsigner/src/v0/signer_state.rs
let evaluator = GlobalStateEvaluator {
    address_weights: HashMap::new(),
    address_updates: HashMap::new(),
    total_weight: 511,
};

// Canonical, consensus-enforced threshold (ceil):
assert_eq!(NakamotoBlockHeader::compute_voting_weight_threshold(511).unwrap(), 358);

// GlobalStateEvaluator's threshold (floor) declares "agreement" one unit early:
assert!(evaluator.reached_agreement(357)); // 357/511 ≈ 69.86% < 70%, yet treated as consensus
```
This demonstrates that `determine_global_state`/`determine_latest_supported_signer_protocol_version`/`determine_global_burn_view` can finalize a "globally agreed" miner state, protocol version, or tx-replay set at a weight the rest of the protocol would not consider a valid 70% supermajority.

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

**File:** stacks-signer/src/v0/signer.rs (L1298-1301)
```rust
        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
```

**File:** libsigner/src/v0/signer_state.rs (L102-144)
```rust
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

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L4118-4122)
```rust
        // Round-up check
        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(511_u32).unwrap(),
            358_u32,
        );
```
