### Title
`GlobalStateEvaluator` uses floor division for its 70% agreement/disagreement threshold instead of the ceiling used everywhere else, letting global state consensus be declared below the real quorum - (File: libsigner/src/v0/signer_state.rs)

### Summary
`GlobalStateEvaluator::reached_agreement` / `reached_disagreement` compute the 70% (and 30%) weight thresholds with plain integer (floor) division, while the canonical, consensus-critical threshold function `NakamotoBlockHeader::compute_voting_weight_threshold` rounds the same 70% quorum *up*. Whenever `total_weight * 7` is not an exact multiple of 10 (i.e. for almost every real reward-set size), the two functions disagree on the minimum weight required to "reach agreement," and `GlobalStateEvaluator` will report consensus reached with strictly less weight than the amount the rest of the protocol (block-signature verification, pre-commit/rejection tallying in `stacks-signer/src/v0/signer.rs`) treats as the real 70% bar. This is the same bug class as the Trader Joe report: one code path recomputes a proportional threshold with a shortcut formula that is systematically smaller than the "correct"/canonical formula used elsewhere in the same codebase.

### Finding Description
The canonical approval-threshold calculation is: [1](#0-0) 

which explicitly adds a `ceil` bump so the 70% threshold is always rounded **up**. This exact function (`compute_voting_weight_threshold`) is what the node uses to validate on-chain signer signatures in `verify_signer_signatures`, and it is what `stacks-signer/src/v0/signer.rs` mirrors in `handle_block_pre_commit`, `store_and_process_block_signature`, and `store_and_process_block_rejection`: [2](#0-1) [3](#0-2) 

By contrast, `GlobalStateEvaluator` — the struct that determines the signer's view of the *global state machine* (active protocol version, global burn view, and, crucially, the agreed-upon `SignerStateMachine` including `current_miner` and the transaction replay set) — computes the same 70/30 proportion with plain floor division and no ceiling correction: [4](#0-3) 

For any `total_weight` where `total_weight * NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD` is not exactly divisible by 10 (the overwhelming majority of possible signer-weight totals), `floor(total_weight*7/10) < ceil(total_weight*7/10)`. Example: `total_weight = 11` → the canonical threshold is `ceil(7.7) = 8`, but `reached_agreement` accepts `vote_weight >= 7`. In the worst case (`total_weight = 7`, remainder 9/10) the floor threshold drops to just above 50% of total weight, versus the intended 70% supermajority.

`reached_agreement` is the single gate used throughout `GlobalStateEvaluator` to decide when a proposition has "global" support: [5](#0-4) [6](#0-5) 

The resulting `SignerStateMachine` (including `current_miner`) is exactly what feeds `GlobalStateView::check_proposal`, which decides whether a signer treats a block proposal's miner/tenure as legitimate before validating and signing it: [7](#0-6) 

### Impact Explanation
Because `GlobalStateEvaluator` treats a weaker-than-70% weight of `StateMachineUpdate` gossip as sufficient to fix the "current miner" / burn view / tx-replay-set for the signer's local state machine, a signer can settle on a `current_miner`/tenure view that never actually reached the real 70% supermajority defined by `NakamotoBlockHeader::compute_voting_weight_threshold`. Since `check_proposal` gates block-proposal acceptance purely on matching this (incorrectly-established) `current_miner_pkh`/`tenure_id`, an honest signer can end up validating and signing a proposal for a miner/tenure whose legitimacy was decided under a weaker quorum bar than the protocol's actual consensus threshold — i.e. the signer is "acting on a stale/incorrect threshold" for a load-bearing decision (which miner is canonical). This matches the High-impact class: a signer acting on an under-verified threshold for canonical-miner/tenure determination, with the potential to diverge from what a genuine 70%-weighted signer set would have agreed to.

### Likelihood Explanation
This is not a hypothetical edge case: the floor/ceiling mismatch occurs for essentially every reward-set weight total except exact multiples of 10 under this threshold (e.g., 70, 140, ...), so it will manifest routinely as signer weights update via ordinary StackerDB gossip during any epoch/tenure transition. No majority collusion is required — the bug triggers purely because the accumulated weight naturally passes through the (too-low) floor threshold before it would reach the real ceiling threshold, which happens on essentially every state convergence event.

### Recommendation
Change `GlobalStateEvaluator::reached_agreement` / `reached_disagreement` in `libsigner/src/v0/signer_state.rs` to use the same ceiling-rounding logic as `NakamotoBlockHeader::compute_voting_weight_threshold` (or call that function directly), so that "global agreement" can never be declared with less weight than the canonical 70% supermajority used for actual block-signature approval.

### Proof of Concept
Given `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD = 7` and a reward set with `total_weight = 11`:
- `NakamotoBlockHeader::compute_voting_weight_threshold(11)` → `ceil(11*7/10) = ceil(7.7) = 8` (the real quorum, matching block-signature validation used everywhere else, e.g. [8](#0-7) ).
- `GlobalStateEvaluator::reached_agreement(7)` on the same `total_weight = 11` → `7 >= (11*7)/10 = 77/10 = 7` (integer floor) → **returns `true`**.

Thus a set of `StateMachineUpdate`s summing to weight 7 (63.6% of 11) is enough for `determine_global_state`/`determine_global_burn_view`/`determine_latest_supported_signer_protocol_version` to declare "global agreement" on a `current_miner`, burn view, or protocol version, even though the same set of signers signing an actual block would need weight 8 (72.7%) to pass `verify_signer_signatures`/`compute_voting_weight_threshold`. This directly demonstrates the aggregated-weight-vs-verified-quorum equality break: `GlobalStateEvaluator` accepts a weight that would be rejected as insufficient by the code paths that gate actual block approval.

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

**File:** stacks-signer/src/v0/signer.rs (L2305-2312)
```rust
        let total_reject_weight =
            self.compute_signature_signing_weight(rejection_addrs.iter().map(|(addr, _)| addr));
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
```

**File:** stacks-signer/src/v0/signer.rs (L2494-2501)
```rust
        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
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

**File:** stacks-signer/src/chainstate/v2.rs (L111-163)
```rust
impl GlobalStateView {
    /// Apply checks from the signer state machine on the block proposal.
    pub fn check_proposal(
        &self,
        client: &StacksClient,
        signer_db: &mut SignerDb,
        block: &NakamotoBlock,
    ) -> Result<(), RejectReason> {
        let MinerState::ActiveMiner {
            current_miner_pkh,
            tenure_id,
            parent_tenure_id,
            ..
        } = &self.signer_state.current_miner
        else {
            info!(
                "No valid current miner. Considering invalid.";
                "block_height" => block.header.chain_length,
                "signer_signature_hash" => %block.header.signer_signature_hash()
            );
            return Err(RejectReason::InvalidMiner);
        };
        if &block.header.consensus_hash != tenure_id {
            info!("Miner block proposal consensus hash does not match the current miner's tenure id. Considering invalid.";
                "block_height" => block.header.chain_length,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
                "block_consensus_hash" => %block.header.consensus_hash,
                "active_miner_tenure_id" => %tenure_id,
                "active_miner_parent_tenure_id" => %parent_tenure_id,
            );
            return Err(RejectReason::ConsensusHashMismatch {
                actual: block.header.consensus_hash.clone(),
                expected: tenure_id.clone(),
            });
        }
        let Some(miner_pk) = block.header.recover_miner_pk() else {
            warn!("Failed to recover miner pubkey";
                  "signer_signature_hash" => %block.header.signer_signature_hash(),
                  "consensus_hash" => %block.header.consensus_hash);
            return Err(RejectReason::IrrecoverablePubkeyHash);
        };
        let miner_pkh = Hash160::from_data(&miner_pk.to_bytes_compressed());
        if current_miner_pkh != &miner_pkh {
            warn!(
                "Miner block proposal pubkey does not match the winning pubkey hash for its sortition. Considering invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
                "proposed_block_pubkey" => &miner_pk.to_hex(),
                "proposed_block_pubkey_hash" => %miner_pkh,
                "active_miner_pubkey_hash" => %current_miner_pkh,
            );
            return Err(RejectReason::PubkeyHashMismatch);
        }
```
