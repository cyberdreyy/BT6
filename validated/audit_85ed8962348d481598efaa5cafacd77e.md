### Title
Inconsistent rounding between `GlobalStateEvaluator::reached_agreement` (floor) and `NakamotoBlockHeader::compute_voting_weight_threshold` (ceiling) allows global state-machine "agreement" to be declared below the true block-approval supermajority - (File: `libsigner/src/v0/signer_state.rs`)

### Summary
The signer-side global state machine evaluator (`GlobalStateEvaluator::reached_agreement`) computes the 70% supermajority threshold using floor division, while the consensus-critical block-approval threshold (`NakamotoBlockHeader::compute_voting_weight_threshold`, used both on the node side in `verify_signer_signatures` and mirrored on the signer side in `signer.rs`) computes the same nominal threshold using ceiling division. This is the same rounding-direction inconsistency as the reported `previewWithdraw`/`ceilDiv` bug: one code path floors a fraction that should be ceiled, silently lowering the bar for "agreement" relative to the value actually required elsewhere in the same protocol.

### Finding Description
`GlobalStateEvaluator::reached_agreement` is defined as: [1](#0-0) 

which computes `floor(total_weight * THRESHOLD / 10)` and requires `vote_weight >= floor(...)`.

By contrast, the block-signature approval threshold used elsewhere in the exact same codebase for the exact same nominal 70% supermajority is: [2](#0-1) 

which explicitly adds a `ceil` term so that the threshold is `ceil(total_weight * THRESHOLD / 10)`. This ceiling-based threshold is what the node uses to accept a block's aggregate signature in `verify_signer_signatures`: [3](#0-2) 

and it is what the signer itself uses (via the identical function) to decide when to sign/pre-commit: [4](#0-3) [5](#0-4) 

So within the same repository, the "70%" supermajority concept is computed two different ways: ceiling for block signature acceptance, floor for global signer-state agreement. For any `total_weight` where `total_weight * THRESHOLD` is not a multiple of 10 (the common case), `floor(total_weight*THRESHOLD/10) < ceil(total_weight*THRESHOLD/10)`. Concretely, for `total_weight = 13` and `THRESHOLD = 7`: `13*7=91`, `ceil(91/10)=10`, `floor(91/10)=9`. A coalition holding weight `9` would be judged as having *not* reached the block-signing threshold (`compute_voting_weight_threshold` requires `10`), but would be judged by `reached_agreement` as having reached "global agreement" on a `SignerStateMachine` view (current miner, burn block view, tx replay set) or on the supported signer protocol version.

`reached_agreement` gates several consensus-relevant decisions in the global state evaluator: [6](#0-5) [7](#0-6) 

`determine_global_state` uses it to decide the agreed `current_miner` (`MinerState::ActiveMiner`) and the agreed transaction-replay set, and `determine_latest_supported_signer_protocol_version` uses it to decide the active protocol version — all foundational inputs that other parts of the signer (e.g. `stacks-signer/src/v0/signer_state.rs`, referenced from `signerdb.rs`) treat as the network's converged/canonical view.

### Impact Explanation
Because `reached_agreement` under-counts the required weight relative to the ceiling-based threshold used for actual block-signature approval, a signer can declare "global agreement" on a `current_miner`/burn-block view or tx-replay set using strictly less weight than the supermajority the protocol elsewhere requires to authorize a block. A signer that latches onto this prematurely-declared global state can act on a stale or incorrectly-supported view of "who is the canonical miner" or which transactions should be replayed — this falls under the High-impact category of "a signer... acting on a stale reward set/threshold." It does not require compromising a majority of signers or their keys: it is a structural rounding-direction mismatch that is naturally triggered whenever `total_weight * THRESHOLD` is not a multiple of 10, which is the common case for arbitrary signer-weight distributions.

### Likelihood Explanation
Likelihood is high because: (1) the discrepancy is deterministic and structural, not dependent on any particular attack — it fires whenever the total signer weight doesn't divide the threshold evenly by 10, which is the typical case; (2) `reached_agreement`/`reached_disagreement` are the sole gating functions for `determine_global_state`, `determine_global_burn_view`, and `determine_latest_supported_signer_protocol_version`, so every global-state convergence decision in the signer set is affected; (3) no majority-key compromise, node access, or auth token is required — it's purely a function of the natural weight distribution of the active honest signer set.

### Recommendation
Make `reached_agreement` (and correspondingly `reached_disagreement`) use the same ceiling-rounding convention as `NakamotoBlockHeader::compute_voting_weight_threshold`, ideally by sharing the exact same threshold-computation routine between `stackslib::chainstate::nakamoto::mod.rs::compute_voting_weight_threshold` and `libsigner::v0::signer_state::GlobalStateEvaluator`, e.g.:
```diff
 pub fn reached_agreement(&self, vote_weight: u32) -> bool {
-    u64::from(vote_weight)
-        >= u64::from(self.total_weight).strict_mul(NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
-            / 10
+    u64::from(vote_weight) >= u64::from(self.total_weight)
+        .strict_mul(NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
+        .div_ceil(10)
 }
```
and adjust `reached_disagreement`'s complementary threshold consistently so blocking-minority accounting stays exactly symmetric with the block-approval threshold.

### Proof of Concept
1. Construct a signer set where `total_weight = 13` and `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD = 7` (13*7=91).
2. `NakamotoBlockHeader::compute_voting_weight_threshold(13)` returns `10` (ceiling), per `stackslib/src/chainstate/nakamoto/mod.rs:1194-1207`.
3. Have signers with combined weight `9` all gossip a `StateMachineUpdate` agreeing on the same `MinerState`/burn-block view/tx-replay-set.
4. `GlobalStateEvaluator::reached_agreement(9)` returns `true` (`9 >= floor(91/10)=9`), per `libsigner/src/v0/signer_state.rs:171-175`, causing `determine_global_state` to declare this the agreed global state — even though `9 < 10`, the actual weight `compute_voting_weight_threshold` would require to authorize a corresponding block signature.
5. Any signer relying on this declared "global state" (e.g., to decide the canonical current miner) is now acting on a view that only 9/13 (≈69.2%) of weight supports, below the true ≥70% ceiling-based bar enforced for block signatures.

### Citations

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

**File:** stacks-signer/src/v0/signer.rs (L1295-1301)
```rust
        let commit_weight = self.compute_signature_signing_weight(committers.iter());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
```

**File:** stacks-signer/src/v0/signer.rs (L2498-2503)
```rust
        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });

        if min_weight > total_signature_weight {
```
