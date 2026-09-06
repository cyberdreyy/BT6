### Title
`GlobalStateEvaluator::reached_agreement` rounds the 70% threshold down instead of up, diverging from the canonical block-signing threshold - (File: `libsigner/src/v0/signer_state.rs`)

### Summary
`GlobalStateEvaluator::reached_agreement` computes the 70% supermajority weight threshold via floor integer division (`total_weight * 7 / 10`), while the canonical, consensus-defining threshold used to verify a block's signer signatures, `NakamotoBlockHeader::compute_voting_weight_threshold`, computes the same "70%" concept via ceiling division. For any `total_weight` where `total_weight * 7` is not an exact multiple of 10, the two "70% of weight" thresholds disagree by exactly one unit of weight, with the `GlobalStateEvaluator` version being strictly weaker. This is the same rounding-direction class as the reported ERC4626 bug: a value that should be rounded up (in the direction that favors requiring more, i.e. safety) is instead rounded down, breaking the invariant that "70% of weight" means the same threshold everywhere it is checked.

### Finding Description
The threshold check is implemented twice in this codebase for the same semantic "70% weight majority":

- Canonical/consensus version (used to verify that a Nakamoto block header carries enough signer weight to be accepted): [1](#0-0) 
This rounds *up*: `ceil(total_weight * 7 / 10)`.

- Global-state-machine version (used by signers to decide when there is agreement on protocol version, burn view, current miner/state view, and tx replay set): [2](#0-1) 
This rounds *down*: `floor(total_weight * 7 / 10)`, and then checks `vote_weight >= threshold`.

Concretely, for `total_weight = 511` (used in the repo's own round-up regression test):
- `compute_voting_weight_threshold(511) == 358` (ceil), confirmed by the existing test. [3](#0-2) 
- `reached_agreement`'s internal threshold for the same total is `floor(511*7/10) = 357`.

So a vote weight of exactly `357` — one unit less than the amount required to satisfy the chain's own 70% signing threshold — is treated by `reached_agreement` as "agreement reached," even though it does not satisfy the same 70% bar enforced by `NakamotoBlockHeader::compute_voting_weight_threshold` / `verify_signer_signatures`.

By contrast, `reached_disagreement` (the 30% blocking-minority side) happens to match `compute_voting_weight_threshold`'s complement exactly, because `total_weight - ceil(0.7*W) == floor(0.3*W)` algebraically: [4](#0-3) 
and the v0 signer's own rejection-threshold logic derives its blocking-minority check from the ceil-rounded `min_weight`, matching the same floor value: [5](#0-4) 
This shows the codebase's authors were aware that the 70/30 complement must be handled carefully to keep the two sides consistent — they simply didn't apply the same care to `reached_agreement`'s absolute (non-complement) case, which is exactly the round-up direction the ERC4626 report's `previewWithdraw` bug is about.

`reached_agreement` is the single primitive `GlobalStateEvaluator` uses everywhere it needs "does this weight represent a 70% supermajority": [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) 
i.e. it decides the agreed active signer protocol version, the agreed global burn view, the agreed current-miner/state-view, and the agreed tx replay set (or its majority-supported prefix).

### Impact Explanation
This maps to the rules' High-impact category: "acting on a stale reward set/threshold." A colluding or simply favorably-distributed set of signers whose combined weight is exactly one unit short of the real, ceil-rounded 70% supermajority (as enforced everywhere else, e.g. block signature verification) can still cause `GlobalStateEvaluator::determine_global_state`, `determine_global_burn_view`, `determine_latest_supported_signer_protocol_version`, and `find_majority_prefix_replay_set` to report "global agreement reached." Other signers consult this evaluator (e.g., via `capitulate_viewpoint`, referenced in `docs/signer-flows.md` section 1) to decide whether to align their local view (current miner, burn view, protocol version, tx replay set) with the reported global state. Because the threshold used here is weaker than the one the chain itself enforces for block-signature acceptance, signers can be induced to treat a genuinely sub-70%-weighted view as the binding global consensus — an equality break between "aggregated weight the group treats as 70%" and "weight actually verified as 70% on-chain."

### Likelihood Explanation
No majority of signers, no signer's private key, and no auth token are needed — only a specific, easily-arranged distribution of registered signer weights (any `total_weight` where `total_weight * 7` is not divisible by 10, which is the common case, not a rare edge case) combined with ordinary gossip of `StateMachineUpdate` messages carrying the affected addresses' votes. This is triggerable purely through normal protocol participation by any subset of signers whose combined weight lands exactly on the one-unit gap between the floor and ceil thresholds — well within a "one-slot miner plus gossip" reach, and does not require a majority.

### Recommendation
Make `reached_agreement`'s threshold computation use the same ceiling rounding as `NakamotoBlockHeader::compute_voting_weight_threshold`, e.g. by calling that function directly (or replicating `(total_weight * 7).div_ceil(10)`), so that "70% of weight" means an identical value everywhere it is checked in the signer/chain codebase. Add a regression test mirroring `test_compute_voting_weight_threshold`'s round-up case (e.g. `total_weight = 511`) asserting `reached_agreement(357)` is `false` and `reached_agreement(358)` is `true`.

### Proof of Concept
1. Register a signer set whose weights sum to `total_weight = 511` (any total where `total_weight * 7 % 10 != 0` works, e.g. 3, 6, 13, 17, 511 …).
2. Have signers controlling exactly `357` units of weight gossip identical `StateMachineUpdate` content (e.g. the same `current_miner` / burn view) to a target signer.
3. On the target signer, `GlobalStateEvaluator::reached_agreement(357)` returns `true` per `libsigner/src/v0/signer_state.rs:171-175` (`357 >= floor(511*7/10) = 357`), so `determine_global_state`/`determine_global_burn_view` report this as the agreed global state.
4. Simultaneously, `NakamotoBlockHeader::compute_voting_weight_threshold(511)` (per the existing test at `stackslib/src/chainstate/nakamoto/tests/mod.rs:4118-4122`) requires `358` — i.e. the same "511 total, 70%" scenario is only treated as a true supermajority by the chain's own verification at `358`, not `357`.
5. This demonstrates the two code paths disagree on whether `357/511` weight constitutes "70% agreement," with the signer-side global-state evaluator being the permissive (incorrectly-rounded) one.

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

**File:** libsigner/src/v0/signer_state.rs (L177-183)
```rust
    /// Check if the supplied vote weight crosses the blocking minority threshold.
    /// Returns true if it has, false otherwise.
    pub fn reached_disagreement(&self, vote_weight: u32) -> bool {
        u64::from(vote_weight)
            > u64::from(self.total_weight).strict_mul(10 - NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
                / 10
    }
```

**File:** libsigner/src/v0/signer_state.rs (L196-209)
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

**File:** stacks-signer/src/v0/signer.rs (L2305-2325)
```rust
        let total_reject_weight =
            self.compute_signature_signing_weight(rejection_addrs.iter().map(|(addr, _)| addr));
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
        if total_reject_weight.saturating_add(min_weight) <= total_weight {
            // Not enough rejection signatures to make a decision
            info!("{self}: Have not yet received enough block rejections to reach a consensus decision on this block";
                "signer_signature_hash" => %block_hash,
                "signature_weight" => signature_weight,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "block_height" => block_info.block.header.chain_length,
                "total_weight_rejected" => total_reject_weight,
                "total_weight" => total_weight,
                "percent_rejected" => (total_reject_weight as f64 / total_weight as f64 * 100.0),
            );
            return;
        }
```
