## Analysis

The Balancer report is about two code paths computing the "same" derived value (spot price) with inconsistent scaling, so a comparison against a canonical value (the oracle price) is done against corrupted units. The closest analog in `stacks-signer`/`libsigner` is a **threshold-rounding mismatch between two implementations of the same 70% quorum formula**: the canonical, consensus-critical threshold (`NakamotoBlockHeader::compute_voting_weight_threshold`) rounds *up* (ceiling), while `GlobalStateEvaluator::reached_agreement` in the signer's state-machine layer rounds *down* (floor) for the identical constant `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD`. [1](#0-0) [2](#0-1) 

### Title
Global signer state-machine quorum threshold uses floor rounding while the canonical block-approval threshold uses ceiling rounding, breaking the 70%-quorum equality — (File: `libsigner/src/v0/signer_state.rs`)

### Summary
`NakamotoBlockHeader::compute_voting_weight_threshold` (the consensus-critical function used by chainstate to accept a block's signatures, and re-used by `stacks-signer` to decide when the local signer has "enough" pre-commits/signatures/rejections) computes the minimum accepting weight with **ceiling** division: `(total_weight * 7) / 10`, rounded up if there's a remainder. [3](#0-2) 

`GlobalStateEvaluator::reached_agreement`, which determines whether the signer network has reached global consensus on the active signer-protocol version, the burn view, the current-miner state, and the transaction-replay set, computes the identical nominal threshold with **floor** division and no remainder adjustment:
```rust
pub fn reached_agreement(&self, vote_weight: u32) -> bool {
    u64::from(vote_weight)
        >= u64::from(self.total_weight).strict_mul(NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
            / 10
}
``` [4](#0-3) 

Whenever `total_weight * 7` is not evenly divisible by 10 (i.e., for almost every possible total signer weight), these two "70% of weight" computations diverge by exactly one weight unit: the on-chain/vote-tally threshold used for real block signatures is one unit *higher* than the threshold the state-machine evaluator uses to declare "global agreement" on burn view / current miner / protocol version / tx-replay-set.

### Finding Description
`compute_voting_weight_threshold` is the single source of truth for "70% of signer weight" everywhere block signatures, pre-commits, and rejections are tallied: chainstate signature verification, and `stacks-signer`'s `handle_block_pre_commit`, `store_and_process_block_signature`, and `store_and_process_block_rejection` all call it and therefore stay internally consistent (as also confirmed by the reject-side algebra: `total_weight - ceil(total*7/10) == floor(total*3/10)`, which matches `reached_disagreement`). [5](#0-4) [6](#0-5) [7](#0-6) 

`GlobalStateEvaluator`, however, independently reimplements "70%" for a *different but equally consensus-shaping* purpose: it decides, from gossiped `StateMachineUpdate` messages, what the global signer view of the active protocol version, current miner/tenure, burn block, and tx-replay set is (`determine_latest_supported_signer_protocol_version`, `determine_global_burn_view`, `determine_global_state`, `find_majority_prefix_replay_set`), all of which call `reached_agreement`. [8](#0-7) [9](#0-8) 

Because `reached_agreement` floors instead of ceils, a coalition holding exactly `floor(total_weight*7/10)` weight — one unit below the real 70% quorum bar used for block signatures — is sufficient to make every honest signer's `GlobalStateEvaluator` conclude that a "global state" (e.g., a specific current-miner/tenure view, burn view, or tx-replay-set) has been reached, even though that same coalition could never actually push a block signature or pre-commit past the identical nominal threshold elsewhere in the code. This is exactly the report's bug class: the same "should be 70%" boundary is scaled/rounded two different ways in two different call sites, and one of them silently accepts a value that fails the other's equality check.

### Impact Explanation
This breaks the intended equality "aggregated weight vs. the same verified 70% threshold used for block approval" for the signer state machine's coordination layer. A one-weight-unit-short coalition (i.e., just under the real 70% signing quorum, not a majority) can force the rest of the honest signer set's `determine_global_state`/`determine_global_burn_view`/`determine_latest_supported_signer_protocol_version` to lock in a `current_miner`, burn view, protocol version, or `tx_replay_set` that the true protocol threshold does not actually endorse. Downstream consumers of this "global state" (documented as driving `capitulate_viewpoint` and miner-inactivity/tenure-following decisions) then treat an under-quorum view as authoritative, which can misdirect honest signers toward following/endorsing a miner or replay set that a genuine 70% of weight never agreed to — a quorum-threshold miscount analogous to "a rejection recounted as an acceptance," but here it is an under-threshold acceptance being recounted as a full global acceptance. This falls into the High-impact category: signers "acting on a stale/incorrect threshold" as a result of a scaling/rounding inconsistency baked into the shared constant `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD`.

### Likelihood Explanation
The discrepancy is deterministic and present for essentially every `total_weight` value except multiples that make `total_weight * 7 % 10 == 0`; it requires no majority, no key compromise, and no protocol violation — only that the network's gossiped `StateMachineUpdate`s from a subset of signers holding `floor(total_weight*7/10)` weight agree on a given view, which is easily reachable by a coalition just under the real quorum bar (e.g. a set of "friendly"/colluding or simply synchronized signers). This is purely a gossip-triggerable divergence in locally-computed state, requiring no majority of signers and no access to another signer's key.

### Recommendation
Make `GlobalStateEvaluator::reached_agreement` (and correspondingly `reached_disagreement`, for symmetry/clarity even though its current floor-based formula happens to coincide with the derived reject bound) call the same canonical `NakamotoBlockHeader::compute_voting_weight_threshold` function instead of re-deriving the 70% cutoff inline, so the "global state agreement" bar is provably identical to the on-chain block-approval bar rather than an independently-rounded approximation of it.

### Proof of Concept
1. Configure a reward set with `total_weight = 511` (matches the existing unit test `test_compute_voting_weight_threshold`, which asserts `compute_voting_weight_threshold(511) == 358`). [10](#0-9) 
2. Have signers holding exactly `357` units of weight (which is `floor(511*7/10)`, one unit below the real threshold `358`) broadcast identical `StateMachineUpdate` messages naming the same `current_miner`/burn view/protocol version.
3. Call `GlobalStateEvaluator::reached_agreement(357)` with `total_weight = 511`: it returns `true` (`357 >= 511*7/10 = 357`), so `determine_global_state`/`determine_global_burn_view` will report this as the network's global state. [11](#0-10) 
4. Simultaneously, the same 357 weight is insufficient to cross `NakamotoBlockHeader::compute_voting_weight_threshold(511) = 358`, so it cannot get a block accepted on-chain via `verify_signer_signatures`, nor could it push `store_and_process_block_signature`'s `min_weight` check in `stacks-signer/src/v0/signer.rs`. [12](#0-11) [13](#0-12) 
5. The result: the signer network's coordination layer (`GlobalStateEvaluator`) reports "global agreement" on a state that never actually meets the real 70% quorum bar enforced everywhere else in the protocol — an under-threshold value silently satisfying an equality check meant to gate on the same canonical threshold.

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

**File:** stacks-signer/src/v0/signer.rs (L1295-1301)
```rust
        let commit_weight = self.compute_signature_signing_weight(committers.iter());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
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

**File:** stacks-signer/src/v0/signer.rs (L2503-2514)
```rust
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

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L4118-4122)
```rust
        // Round-up check
        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(511_u32).unwrap(),
            358_u32,
        );
```
