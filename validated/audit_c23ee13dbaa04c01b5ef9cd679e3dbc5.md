### Title
Floor-vs-ceiling mismatch between `GlobalStateEvaluator::reached_agreement` and `NakamotoBlockHeader::compute_voting_weight_threshold` lets sub-70%-weight quorums be treated as globally agreed state - (File: `libsigner/src/v0/signer_state.rs`)

### Summary
The signer's local `GlobalStateEvaluator` uses a **floor** division to decide whether the 70% supermajority threshold has been crossed for global state agreement (active protocol version, global burn view, current-miner state-machine view, tx replay set), while the canonical, consensus-critical threshold used everywhere else in the codebase (`NakamotoBlockHeader::compute_voting_weight_threshold`, used by `verify_signer_signatures`, `handle_block_pre_commit`, `store_and_process_block_signature`, `store_and_process_block_rejection`) uses a **ceiling** division. This creates two different numeric definitions of "70% of total_weight" that disagree whenever `total_weight * 7` is not a multiple of 10.

### Finding Description
`compute_voting_weight_threshold` computes the minimum weight required to approve a block, rounding **up**: [1](#0-0) 

This ceiling-based threshold is the one actually enforced consensus-side in `verify_signer_signatures` (deciding whether a `NakamotoBlock`'s aggregated signer weight is sufficient to be a valid block): [2](#0-1) 

and it's the same function reused by the signer's own pre-commit/signature/rejection tallying paths (`handle_block_pre_commit`, `store_and_process_block_signature`, `store_and_process_block_rejection`): [3](#0-2) [4](#0-3) 

However, `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` (used to decide the signer's *own* view of the global signer-network state — active protocol version, global burn view, agreed "current miner" state machine, and replay set) instead compute the same "70%"/"30%" boundary with a plain **floor** division and an inclusive `>=`: [5](#0-4) 

These two thresholds are mathematically supposed to represent the same protocol constant (`NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD == 7`, i.e., 70%), but for any `total_weight` where `total_weight * 7 % 10 != 0`, `reached_agreement`'s floor-based bound is strictly *lower* than `compute_voting_weight_threshold`'s ceiling-based bound.

Concretely, for `total_weight = 11`:
- `compute_voting_weight_threshold(11)` = `ceil(11*7/10)` = `ceil(7.7)` = **8** (real block-signing/consensus threshold, tested directly in `test_compute_voting_weight_threshold`) [6](#0-5) 
- `reached_agreement(7)` = `7 >= floor(11*7/10) = floor(7.7) = 7` → **true**

So a coalition holding vote-weight 7 out of 11 (≈63.6%, strictly below the mandated 70% supermajority) is treated by `GlobalStateEvaluator` as having reached "global agreement," even though that same weight would *not* satisfy the canonical block-approval threshold used by `verify_signer_signatures` and the rest of the signer's block-signing tally logic.

`reached_agreement` is the sole gate for `determine_latest_supported_signer_protocol_version`, `determine_global_burn_view`, and `determine_global_state` (which fixes the network's agreed "current miner"/state-machine view and the transaction replay set): [7](#0-6) [8](#0-7) 

Because these functions gate what a signer believes to be the network-wide agreed state (including which miner is canonical and which transaction-replay set is active), a sub-threshold, rounding-favorable weight distribution can cause an individual signer to lock in a `current_miner`/replay-set view and an "active protocol version" that has not actually reached the protocol's mandated 70% supermajority — a real weight/threshold whose value diverges from the value the rest of the codebase (and the chain-level consensus check) uses for the identical constant.

### Impact Explanation
This is a "signer acting on a stale/incorrectly-computed threshold" class bug (High impact per the given rubric): the signer's belief about global consensus state (current miner, active protocol version, replay set) is derived from a threshold calculation that is measurably laxer than the canonical, consensus-enforced threshold (`compute_voting_weight_threshold`) used for actual block-signature verification. A signer can converge on (and subsequently sign for) a "current miner" or replay set that a genuinely 70%-supermajority-based evaluation would not have agreed to, purely because of the floor-vs-ceil rounding discrepancy. In borderline `total_weight` values (any value not evenly divisible in the `*7/10` calculation — the overwhelming majority of possible reward-set totals), the two code paths disagree by exactly one unit of weight, silently lowering the practical threshold from `ceil(0.7*W)` to `floor(0.7*W)`.

### Likelihood Explanation
The divergence is deterministic and occurs for essentially every `total_weight` value except multiples of 10 (since `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD = 7`), which in practice is nearly always the case for real signer reward sets. No majority collusion beyond what would normally be needed to reach ~63–69% weight is required — this is strictly *less* than the 70% supermajority the protocol intends, so the "bar" for triggering divergent behavior is lower than the documented threshold, not higher. It is a deterministic arithmetic/logic bug reachable purely through the normal `StateMachineUpdate` message flow that any signer set can hit, not something requiring a majority coalition beyond ordinary operation.

### Recommendation
Make `GlobalStateEvaluator::reached_agreement` and `reached_disagreement` use the same ceiling-based formula as `NakamotoBlockHeader::compute_voting_weight_threshold` (i.e., call that function, or replicate its `ceil` logic) so that "70% agreement" and "30% blocking minority" are computed identically everywhere in the codebase. Ideally, factor the threshold arithmetic into a single shared helper used by both `stackslib`'s block-header verification and `libsigner`'s `GlobalStateEvaluator`, eliminating the possibility of the two diverging again.

### Proof of Concept
1. Construct a signer set with `total_weight = 11` (e.g., signer weights `[4, 3, 2, 1, 1]`).
2. Have signers controlling exactly weight `7` (e.g., the first three: `4+3=7`... adjust so weight sums to 7, e.g. `[4,2,1]`) submit identical `StateMachineUpdate`s (e.g., same burn view / current miner / protocol version).
3. Call `GlobalStateEvaluator::reached_agreement(7)` (as invoked internally by `determine_global_burn_view`/`determine_global_state`/`determine_latest_supported_signer_protocol_version`): returns `true` because `7 >= floor(11*7/10) = 7`.
4. Compare against `NakamotoBlockHeader::compute_voting_weight_threshold(11)`, which returns `8` — i.e., the same weight of `7` would be rejected as insufficient by the canonical block-approval logic (`verify_signer_signatures`, `store_and_process_block_signature`).
5. This demonstrates the signer locking in a "global state" (current miner / replay set / protocol version) based on only 7/11 (~63.6%) weight agreement, below the intended 70% threshold enforced elsewhere in the same codebase for the semantically identical calculation. [9](#0-8) [10](#0-9)

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

**File:** stacks-signer/src/v0/signer.rs (L1295-1301)
```rust
        let commit_weight = self.compute_signature_signing_weight(committers.iter());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
```

**File:** stacks-signer/src/v0/signer.rs (L2495-2501)
```rust
        let total_signature_weight = self.compute_signature_signing_weight(addrs_to_sigs.keys());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
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

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L4096-4122)
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
```
