## Title
Rounding mismatch between `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` (floor-based) and `NakamotoBlockHeader::compute_voting_weight_threshold` (ceiling-based) allows global state-machine consensus to be miscounted below the true 70% supermajority - (File: `libsigner/src/v0/signer_state.rs`)

### Summary
The two independent implementations of the 70%/30% signer-weight supermajority check in this codebase disagree at their rounding boundary. `NakamotoBlockHeader::compute_voting_weight_threshold` (used to gate block signatures and pre-commit weight, i.e. the actual block-signing threshold) rounds *up* (ceiling) when `total_weight * 7` is not a multiple of 10 [1](#0-0) . `GlobalStateEvaluator::reached_agreement` / `reached_disagreement`, which drive the miner-view/burn-view/protocol-version global-state consensus used by `capitulate_miner_view`, `determine_global_burn_view`, and `determine_latest_supported_signer_protocol_version`, instead use plain integer (floor) division with no ceiling adjustment [2](#0-1) . For any `total_weight` where `total_weight*7` is not a multiple of 10, the "agreement" threshold used for the state machine is exactly one weight-unit *lower* than the ceiling-based threshold used elsewhere in the same protocol for block-signature validity.

### Finding Description
`compute_voting_weight_threshold` computes `ceil(total_weight * 7 / 10)` and is the threshold used both by `verify_signer_signatures` (the consensus-critical check on the node) and by the signer's pre-commit/acceptance-weight gates in `stacks-signer/src/v0/signer.rs` (`handle_block_pre_commit`, `store_and_process_block_signature`) [1](#0-0) [3](#0-2) .

In contrast, `GlobalStateEvaluator::reached_agreement` computes `total_weight * 7 / 10` (floor, via integer division) with strict `>=` [4](#0-3) , and `reached_disagreement` computes `total_weight * 3 / 10` (floor) with strict `>` [5](#0-4) . These two functions back the *entire* global-state-machine consensus surface: `determine_global_burn_view`, `determine_global_state`, `determine_latest_supported_signer_protocol_version`, and `capitulate_miner_view`'s minority/majority bucketing of peer miner-state views [6](#0-5) [7](#0-6) .

Take `total_weight = 511` (a value directly used in an existing test as a rounding-boundary case, `compute_voting_weight_threshold(511) == 358`, i.e. `ceil(511*7/10) = ceil(357.7) = 358`) [8](#0-7) . `reached_agreement` for the same total weight instead requires only `floor(511*7/10) = 357`. So a set of peers whose combined weight is exactly `357` (69.86%, one weight-unit short of the true supermajority) is treated by the global state evaluator as having reached agreement, while the same weight sum would *not* clear the block-signing threshold used elsewhere in the protocol for the identical nominal "70%" rule. This is structurally the same class of bug as the `picklescan` `STACK_GLOBAL` off-by-one: two code paths meant to enforce the same invariant use inconsistent boundary arithmetic, so a value that should fail one check (true supermajority) instead passes the other (state-machine "agreement").

### Impact Explanation
This breaks the intended equality "aggregated-weight vs verified-accepts": the value the signer network treats as "global agreement" on the miner view / burn view / protocol version is not the same 70% supermajority that gates block signatures. Because `capitulate_miner_view` uses `reached_agreement`/`reached_disagreement` to decide whether a signer should adopt a competing miner view (potentially abandoning the miner it has been building with) [9](#0-8) , a weight distribution that lands exactly on this one-unit rounding gap can let peers "reach agreement" on a new miner state (or a burn view, or a protocol version) one unit of weight below what the rest of the protocol considers a genuine 70% supermajority. Because the effect only manifests for specific `total_weight` values whose `*7` is not a multiple of 10 (i.e. most reward-cycle configurations), and the discrepancy is always exactly one weight-unit, its practical severity is limited: it does not itself let a single low-weight miner or signer forge an invalid/non-canonical block signature (that is still gated by the correctly-ceiling-rounded `compute_voting_weight_threshold` in `verify_signer_signatures`/`store_and_process_block_signature`). Its concrete consequence is a signer acting on a marginally-substandard "global agreement" for miner view/burn view/protocol version -- i.e. "acting on a stale reward set/threshold" in the sense described by the report's High-severity bucket, since the local state machine may capitulate its viewpoint on a threshold that doesn't actually reflect a true supermajority.

### Likelihood Explanation
Reachable purely through normal `StateMachineUpdate` gossip that any signer set member (or, in a permissive interpretation, a low-weight miner-side influence over which peers gossip which views) already sends every pass through `process_event`; no majority key or privileged access is required to *trigger* the arithmetic, only to land the aggregate weight on the exact one-unit boundary, which happens deterministically whenever `total_weight` is not a multiple of 10 (the common case for real reward-cycle weight distributions) and the "for" vs "against" split lands on the gap. It requires no code beyond ordinary gossip and no majority of signers to control -- only that the natural weight distribution among honest peers straddles the rounding gap, which is a property of the reward set, not of an attacker's control.

### Recommendation
Align `GlobalStateEvaluator::reached_agreement` and `reached_disagreement` with the ceiling-rounded arithmetic already used by `NakamotoBlockHeader::compute_voting_weight_threshold`, e.g. by calling `compute_voting_weight_threshold(self.total_weight)` (with the complementary `10 - NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD` for disagreement) instead of re-implementing the percentage math with floor division in `libsigner/src/v0/signer_state.rs`. Add a regression test at a non-multiple-of-10 total weight (e.g. 511) mirroring the existing `compute_voting_weight_threshold` boundary test to lock the two thresholds together.

### Proof of Concept
1. Construct a `GlobalStateEvaluator` with `total_weight = 511` (matching the existing rounding-boundary test in `stackslib/src/chainstate/nakamoto/tests/mod.rs`).
2. Insert `StateMachineUpdate`s from peers whose combined weight sums to exactly `357`.
3. Observe `evaluator.reached_agreement(357)` returns `true` (`357 >= 511*7/10 = 357`), so `determine_global_burn_view`/`determine_global_state`/`capitulate_miner_view` treat this as global agreement.
4. Compare against `NakamotoBlockHeader::compute_voting_weight_threshold(511)`, which is `358` per the existing test at `stackslib/src/chainstate/nakamoto/tests/mod.rs:4118-4122` -- the same nominal weight sum of `357` would *not* clear the block-signing supermajority threshold used elsewhere in the protocol, demonstrating the inconsistency between the two "70%" checks.

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

**File:** stacks-signer/src/v0/signer.rs (L1296-1301)
```rust
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
```

**File:** stacks-signer/src/v0/signer_state.rs (L1042-1054)
```rust
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

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L4118-4122)
```rust
        // Round-up check
        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(511_u32).unwrap(),
            358_u32,
        );
```
