### Title
Floor-vs-ceil mismatch between `GlobalStateEvaluator::reached_agreement` and `NakamotoBlockHeader::compute_voting_weight_threshold` lets the signer-state consensus fire below the true block-approval supermajority - (File: `libsigner/src/v0/signer_state.rs`)

### Summary
`GlobalStateEvaluator::reached_agreement`, which decides when the signer set's *global state machine* (active protocol version, burn view, current miner, tx-replay-set) has reached the 70% `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD`, computes the threshold with a plain floor division, while the canonical definition of that same threshold used for actual block-signature acceptance, `NakamotoBlockHeader::compute_voting_weight_threshold`, uses ceiling division. For any `total_weight` not evenly divisible by 10 these two "70%" thresholds diverge by one unit of weight, so the global-state consensus can be declared reached by a coalition that is provably below the weight required to actually get a block signed/accepted.

### Finding Description
`reached_agreement` in [1](#0-0)  computes:
```
u64::from(vote_weight) >= u64::from(self.total_weight).strict_mul(NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD) / 10
```
i.e. `floor(total_weight * 7 / 10)`.

The canonical block-approval threshold, used by chainstate to actually verify signer signatures on a block and by the signer's own signature-tallying code, is [2](#0-1) :
```rust
pub fn compute_voting_weight_threshold(total_weight: u32) -> Result<u32, ChainstateError> {
    let threshold = NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD;
    let total_weight = u64::from(total_weight);
    let ceil = if (total_weight * threshold) % 10 == 0 { 0 } else { 1 };
    u32::try_from((total_weight * threshold) / 10 + ceil)...
}
```
i.e. `ceil(total_weight * 7 / 10)`. This exact function (round-up behavior) is directly exercised in `stacks-signer/src/v0/signer.rs::store_and_process_block_signature` and `handle_block_pre_commit` as `min_weight`, and in the node-side `NakamotoBlockHeader::verify_signer_signatures` as the block's signature-acceptance gate: [3](#0-2) [4](#0-3) .

Both functions claim to implement the same named constant, `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD` (70%), but round differently. For any `total_weight` where `total_weight * 7` is not a multiple of 10 (i.e. most values), `reached_agreement`'s floor threshold is exactly one unit of weight lower than `compute_voting_weight_threshold`'s ceiling threshold.

`reached_agreement` gates every branch of the global state machine's consensus determination: `determine_latest_supported_signer_protocol_version`, `determine_global_burn_view`, and `determine_global_state` (which fixes the agreed `current_miner`, burn view, and tx-replay-set) all call it directly: [5](#0-4) [6](#0-5) .

### Impact Explanation
Because `reached_agreement` accepts a weaker coalition than the block-signing supermajority actually requires, the global state machine can "lock in" a `current_miner` / burn view / active protocol version / tx-replay-set using support that is provably below the threshold the same signer set needs to actually approve a block on-chain (`compute_voting_weight_threshold`). This is precisely an aggregated-weight-vs-verified-threshold divergence: the state machine's notion of "70% agreement" is not the same 70% enforced by chainstate block-signature verification. A signer relying on `determine_global_state()`'s output (e.g. to decide which miner's blocks to consider canonical, or which tx-replay-set to enforce) is "acting on a stale/looser threshold" than the one the protocol actually requires for block finality — the High-impact category called out for a signer acting on a stale reward-set/threshold.

### Likelihood Explanation
The divergence is deterministic and automatic for any signer-set weight distribution where `total_weight * 7` is not a multiple of 10 (the overwhelming majority of possible weight totals, since weights sum from PoX stacking amounts). No majority collusion, key compromise, or malicious message crafting is needed — any legitimate signer population whose combined weight happens to fall in the one-unit gap between the floor and ceiling thresholds will exhibit the mismatch purely from normal state-machine-update gossip.

### Recommendation
Make `GlobalStateEvaluator::reached_agreement` (and correspondingly `reached_disagreement`) use the same ceiling-rounding formula as `NakamotoBlockHeader::compute_voting_weight_threshold`, ideally by sharing a single threshold-computation helper between `libsigner` and `stackslib` so the two can never drift apart again.

### Proof of Concept
1. Construct a signer set with `total_weight = 13` (achievable with normal, non-adversarial stacking amounts).
2. `NakamotoBlockHeader::compute_voting_weight_threshold(13)` → `(13*7)/10 + 1 = 9 + 1 = 10` (per `stackslib/src/chainstate/nakamoto/mod.rs`), i.e. 10/13 weight is required to actually get a block accepted.
3. `GlobalStateEvaluator::reached_agreement(9)` on the same `total_weight = 13` → `9 >= (13*7)/10 = 9` → `true` (per `libsigner/src/v0/signer_state.rs`), i.e. only 9/13 weight is needed for the global state machine to declare agreement on `current_miner`/burn-view/tx-replay-set/protocol-version.
4. Signers holding exactly 9/13 weight (69.2%) — a coalition strictly insufficient to ever get a block signed under the real 70%-ceil rule — can drive `determine_global_state()` to converge on a specific `current_miner`/replay-set, while the remaining weight disagrees; this consensus is not backed by the actual block-approval supermajority the chain enforces.

### Citations

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

**File:** libsigner/src/v0/signer_state.rs (L169-175)
```rust
    /// Check if the supplied vote weight crosses the global agreement threshold.
    /// Returns true if it has, false otherwise.
    pub fn reached_agreement(&self, vote_weight: u32) -> bool {
        u64::from(vote_weight)
            >= u64::from(self.total_weight).strict_mul(NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
                / 10
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1180-1187)
```rust
        let threshold = Self::compute_voting_weight_threshold(total_weight)?;

        if total_weight_signed < threshold {
            return Err(ChainstateError::InvalidStacksBlock(format!(
                "Not enough signatures. Needed at least {} but got {} (out of {})",
                threshold, total_weight_signed, total_weight,
            )));
        }
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
