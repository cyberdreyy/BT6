### Title
Inconsistent Ceiling vs. Floor Rounding of the 70% Weight Threshold Between Chain-Level Signature Verification and the Signer's Global State Machine Agreement Check — ([File: libsigner/src/v0/signer_state.rs])

### Summary
The Nakamoto signer protocol computes the same nominal "70% of signer weight" threshold in two different places using two different rounding rules. `NakamotoBlockHeader::compute_voting_weight_threshold` (used for actual block-signature verification, both in chain validation and in the signer's own block-signing logic) rounds **up** (ceiling), while `GlobalStateEvaluator::reached_agreement` (used by the signer to decide whether the signer network has reached agreement on burn view, current miner, protocol version, and tx-replay set) rounds **down** (floor). This is the same class of bug as the Nouns Builder finding: two formulas that are supposed to compute an identical percentage-based partition diverge because the integer division/rounding is not performed consistently, so a value that should require the "same" threshold in both places can pass one check but fail the other.

### Finding Description
`NakamotoBlockHeader::compute_voting_weight_threshold` explicitly computes a ceiling: [1](#0-0) 

```
let ceil = if (total_weight * threshold) % 10 == 0 { 0 } else { 1 };
u32::try_from((total_weight * threshold) / 10 + ceil)
```

This function is the source of truth for whether a block's aggregated signer signatures meet consensus (`verify_signer_signatures`) and is also invoked by the signer itself when deciding to mark a block as locally accepted / broadcast it, and when deciding a block is globally rejected: [2](#0-1) [3](#0-2) [4](#0-3) 

In contrast, `GlobalStateEvaluator::reached_agreement`, used to determine the *global signer state* (current miner, burn view, active protocol version, tx replay set), performs a plain floor division and does not add the ceiling correction: [5](#0-4) 

```rust
pub fn reached_agreement(&self, vote_weight: u32) -> bool {
    u64::from(vote_weight)
        >= u64::from(self.total_weight).strict_mul(NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
            / 10
}
```

For a `total_weight` and threshold percentage where `total_weight * NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD` is not a multiple of 10, the floor-based `reached_agreement` accepts one unit of weight *less* than the ceiling-based `compute_voting_weight_threshold`. E.g., with `total_weight = 99` and threshold constant `7` (70%): `99*7=693`; `compute_voting_weight_threshold` returns `70` (ceil(69.3)); `reached_agreement` accepts at `69` (floor(69.3)). This mirrors the Nouns Builder bug exactly: two supposedly-equal partitioning computations ("this fraction of the 100 IDs"/"this fraction of signer weight") disagree by exactly one unit due to inconsistent modulo/rounding handling, and the affected code paths silently rely on the two thresholds being interchangeable.

`GlobalStateEvaluator` and `reached_agreement` are used throughout `stacks-signer` state-machine and chainstate v2 logic to determine the network's canonical view (e.g. `determine_global_state`, `determine_global_burn_view`, `determine_latest_supported_signer_protocol_version`) — logic that gates signer decisions such as which miner/tenure to treat as canonical: [6](#0-5) [7](#0-6) [8](#0-7) 

### Impact Explanation
A signer can be misled into believing the signer network has reached "global agreement" (on the current miner/tenure, on burn view, on protocol version, or on a tx-replay set) using a weight aggregate that is one unit below what the real Nakamoto chain-consensus threshold (`compute_voting_weight_threshold`, ceiling-based) requires for the equivalent block-signature acceptance. Because the signer's belief about "who the canonical/global miner is" feeds directly into whether it will sign block proposals from that miner, this creates a gap where a signer can act (sign) on the assumption that a threshold has been met when, by the stricter chain-verification rule, it has not — i.e., a rejection-vs-acceptance/consensus miscount analogous to "a rejection recounted as an accept" for the internal state-machine agreement check. This falls under the "acting on a stale/miscounted threshold, losing consistency between the signer's local agreement decision and the real chain acceptance threshold" impact category.

The magnitude of the discrepancy is at most 1 unit of signer weight (bounded, similar to the bounded nature of the original Nouns Builder bug), and it only manifests for `total_weight` values where `total_weight * threshold_pct` is not a multiple of 10 — i.e. it is conditional on the exact weight distribution, exactly as the original finding was conditional on specific founder percentages.

### Likelihood Explanation
This triggers automatically, without any malicious action, purely from the natural distribution of signer weights across a reward cycle — no adversarial input is required. Because signer weights (`u32`, derived from stacked STX apportionment) are essentially arbitrary integers, `total_weight * 7 mod 10 != 0` will occur for a large fraction of possible `total_weight` values (any `total_weight` not divisible by 10 when unlucky, roughly a majority of possible sums), making this a highly likely and recurring, rather than rare, divergence during normal network operation.

### Recommendation
Make `GlobalStateEvaluator::reached_agreement` (and the corresponding `reached_disagreement`) use the exact same ceiling formula as `NakamotoBlockHeader::compute_voting_weight_threshold`, ideally by calling that shared function directly instead of re-implementing the percentage math with plain integer division:

```rust
pub fn reached_agreement(&self, vote_weight: u32) -> bool {
    let threshold = NakamotoBlockHeader::compute_voting_weight_threshold(self.total_weight)
        .unwrap_or(u32::MAX);
    vote_weight >= threshold
}
```

and adjust `reached_disagreement` analogously so both the signer's internal state-machine agreement notion and the chain's block-signature acceptance notion of "70% of weight" always agree bit-for-bit.

### Proof of Concept
1. Configure a reward cycle where the sum of registered signer weights is `total_weight = 99` (achievable with normal stacked-STX apportionment; not adversarial).
2. With `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD = 7` (70%):
   - `NakamotoBlockHeader::compute_voting_weight_threshold(99)` returns `70` (since `99*7=693`, `693 % 10 != 0`, ceil adds 1: `69 + 1 = 70`).
   - `GlobalStateEvaluator::reached_agreement(69)` returns `true` (since `693/10 = 69`, and `69 >= 69`).
3. A signer accumulates exactly `69` weight of matching `StateMachineUpdate`s pointing to a given miner/tenure/burn-view and calls `determine_global_state`/`determine_global_burn_view`; `reached_agreement(69)` returns `true`, so the signer concludes the network has reached global agreement on that state.
4. Meanwhile, the actual Nakamoto block-signature verification path (`verify_signer_signatures`/`store_and_process_block_signature`) requires `70` weight to accept a block signed under that same 99-unit weight distribution — one unit more than what the signer's own state-machine agreement check required.
5. This produces two different effective thresholds for what is meant to be the identical "70% supermajority" rule, letting the signer's internal state machine (miner selection, burn view, protocol version, replay set) converge on "agreement" one unit of weight earlier than the chain-level signature-acceptance rule would allow.

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

**File:** stacks-signer/src/v0/signer.rs (L2305-2313)
```rust
        let total_reject_weight =
            self.compute_signature_signing_weight(rejection_addrs.iter().map(|(addr, _)| addr));
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
        if total_reject_weight.saturating_add(min_weight) <= total_weight {
```

**File:** stacks-signer/src/v0/signer.rs (L2498-2514)
```rust
        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });

        if min_weight > total_signature_weight {
            info!("{self}: Received block acceptance, but have not yet reached the acceptance threshold.";
                "signer_signature_hash" => %block_hash,
                "signature_weight" => signature_weight,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "block_height" => block_info.block.header.chain_length,
                "total_weight_approved" => total_signature_weight,
                "total_weight" => total_weight,
                "percent_approved" => (total_signature_weight as f64 / total_weight as f64 * 100.0),
            );
            return;
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
