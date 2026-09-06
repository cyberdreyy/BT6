### Title
Global-state supermajority threshold rounds down instead of up, letting the signer set's agreed miner/burn-view/replay-set state be adopted below the true 70% weight - ([File: libsigner/src/v0/signer_state.rs])

### Summary
The Morpho report flags a threshold that is placed too close to (effectively coincident with) the danger boundary instead of leaving a safety buffer below it. The analog here is the inverse but equivalent failure: `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` compute the "70%" supermajority boundary with a **floor** division, while the consensus-critical block-approval threshold `NakamotoBlockHeader::compute_voting_weight_threshold` computes the same "70%" boundary with a **ceiling**. The two supposedly-identical 70% boundaries silently diverge whenever `total_weight` is not a multiple of 10 — which is the common case — so the signer network's internal "global state" (current miner, burn view, active protocol version, tx replay set) can be certified as agreed-upon with strictly less than the real 70% weight required for block signing.

### Finding Description
`NakamotoBlockHeader::compute_voting_weight_threshold` rounds the 70% threshold **up** (ceiling), guaranteeing that reaching the block-signature threshold always requires *at least* 70% of weight: [1](#0-0) 

`GlobalStateEvaluator::reached_agreement` computes the analogous 70% boundary with plain integer division (**floor**), and `reached_disagreement` mirrors it for the 30% minority boundary: [2](#0-1) 

Both are described as the "global agreement threshold" / "blocking minority threshold" and both use the same `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD` constant (7, i.e. 70%), so a reader (and the code's own doc-comments) treat them as the same 70%/30% boundary used for block approval. But for any `total_weight` not divisible by 10 (e.g. 17, 23, 41 — the normal case for a real signer set with weighted, non-round stake distributions), `reached_agreement` returns `true` at a weight strictly below the true ceiling-rounded 70% that `compute_voting_weight_threshold` would require. Example: `total_weight = 17` → chainstate's ceiling threshold is `ceil(17*7/10) = 12`, but `reached_agreement` accepts `vote_weight = 11` (`11 >= floor(17*7/10) = 11`), i.e. only 64.7% of weight.

This evaluator's "agreement" result drives `determine_global_state`, `determine_global_burn_view`, and `determine_latest_supported_signer_protocol_version`, which together decide the network-wide `SignerStateMachine` (who the current miner is, what burn view is canonical, which protocol version is active, and which tx-replay set applies) as seen by every honest signer: [3](#0-2) [4](#0-3) [5](#0-4) 

The rounding gap means a signer can lock in a "globally agreed" miner state, burn view, or protocol version using weight support that would *not* have been sufficient to sign an actual block under `verify_signer_signatures`/`compute_voting_weight_threshold`: [6](#0-5) 

This breaks the equality the two 70% thresholds are meant to share: the "aggregated weight vs verified/required weight" invariant no longer matches between the state-machine-agreement path and the block-signature path, exactly analogous to the Morpho report's core complaint — a boundary computed with an inadequate safety margin (here, rounding the wrong direction) relative to the value it is meant to gate.

### Impact Explanation
Because the mismatch always exists for any non-round `total_weight` (no attacker action, no majority of colluding signers, and no need for another signer's key is required — it is a deterministic arithmetic property of the two threshold functions), it is a systemic, always-latent defect rather than a crafted edge case. Its consequence is a signer acting on a global-state view (miner identity, burn view, replay set, protocol version) that was certified with less than the intended 70% supermajority. If this global view then gates whether/how a signer proposes or evaluates blocks, it corresponds to the "acting on a stale/threshold-mismatched reward set" class of High-impact issue: honest signers can converge on and act upon a `SignerStateMachine` view that is not actually backed by the protocol's real 70% bar, diverging from what `compute_voting_weight_threshold`/`verify_signer_signatures` would recognize as sufficient.

### Likelihood Explanation
High likelihood of triggering in practice: it requires no adversarial input, malicious signer, or majority collusion — it fires automatically whenever the sum of signer weights in the active reward set is not an exact multiple of 10, which is the normal/expected case for stake-weighted signer sets rather than the exception.

### Recommendation
Make `reached_agreement`/`reached_disagreement` use the same ceiling-rounded computation as `NakamotoBlockHeader::compute_voting_weight_threshold` (or better, have `GlobalStateEvaluator` call `compute_voting_weight_threshold` directly for the 70% side and compute the 30% blocking-minority as `total_weight - threshold` derived from that same ceiling value), so the "70%" agreement bar used for global-state determination can never be satisfied with less weight than would actually be required to produce a valid block signature.

### Proof of Concept
Using `total_weight = 17` (a realistic non-round signer set):
- `NakamotoBlockHeader::compute_voting_weight_threshold(17)` → `ceil(17*7/10) = 12` (per the ceiling logic at [1](#0-0) ).
- `GlobalStateEvaluator::reached_agreement(11)` with `total_weight = 17` → `11 >= (17*7)/10 = 11` (integer floor) → returns `true` (per [7](#0-6) ), even though 11/17 ≈ 64.7% < 70% and is below the 12 that the chainstate-level threshold function would require to actually sign a block.

Existing repo tests only exercise `total_weight = 1_000_000_000` (a value chosen to be exactly divisible by 10), which masks this rounding-direction mismatch: [8](#0-7)

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1096-1190)
```rust
    #[cfg_attr(test, mutants::skip)]
    pub fn verify_signer_signatures(
        &self,
        reward_set: &RewardSet,
        epoch_id: StacksEpochId,
    ) -> Result<u32, ChainstateError> {
        let message = self.signer_signature_hash();
        let Some(signers) = reward_set.signers() else {
            return Err(ChainstateError::InvalidStacksBlock(
                "No signers in the reward set".to_string(),
            ));
        };

        // if this is a shadow block, then its signing weight is as if every signer signed it, even
        // though the signature vector is undefined.
        if self.is_shadow_block() {
            return Ok(self.get_shadow_signer_weight(reward_set)?);
        }

        let mut total_weight_signed: u32 = 0;
        // `last_index` is used to prevent out-of-order signatures
        let mut last_index = None;
        // Before Epoch 4.0, signature order check contained a bug, so gate the
        // strict ordering behavior on the epoch.
        let strict_order = epoch_id.enforces_strict_signature_order();

        let total_weight = reward_set
            .total_signing_weight()
            .map_err(|_| ChainstateError::NoRegisteredSigners(0))?;

        // HashMap of <PublicKey, (Signer, Index)>
        let mut signers_by_pk: HashMap<_, _> = signers
            .iter()
            .enumerate()
            .map(|(i, signer)| (&signer.signing_key, (signer, i)))
            .collect();

        for signature in self.signer_signature.iter() {
            let public_key = Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(
                message.bits(),
                signature,
            )
            .map_err(|_| {
                ChainstateError::InvalidStacksBlock(format!(
                    "Unable to recover public key from signature {}",
                    signature.to_hex()
                ))
            })?;

            let mut public_key_bytes = [0u8; 33];
            public_key_bytes.copy_from_slice(&public_key.to_bytes_compressed()[..]);

            let (signer, signer_index) = signers_by_pk.remove(&public_key_bytes).ok_or_else(|| {
                warn!(
                    "Found an invalid public key. Reward set has {} signers. Chain length {}. Signatures length {}",
                    signers.len(),
                    self.chain_length,
                    self.signer_signature.len(),
                );
                ChainstateError::InvalidStacksBlock(format!(
                    "Public key {} not found in the reward set",
                    public_key.to_hex()
                ))
            })?;

            // Enforce order of signatures
            if let Some(index) = last_index.as_ref() {
                if *index >= signer_index {
                    return Err(ChainstateError::InvalidStacksBlock(
                        "Signatures are out of order".to_string(),
                    ));
                }
                if strict_order {
                    last_index = Some(signer_index);
                }
            } else {
                last_index = Some(signer_index);
            }

            total_weight_signed = total_weight_signed
                .checked_add(signer.weight)
                .expect("FATAL: overflow while computing signer set threshold");
        }

        let threshold = Self::compute_voting_weight_threshold(total_weight)?;

        if total_weight_signed < threshold {
            return Err(ChainstateError::InvalidStacksBlock(format!(
                "Not enough signatures. Needed at least {} but got {} (out of {})",
                threshold, total_weight_signed, total_weight,
            )));
        }

        return Ok(total_weight_signed);
    }
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

**File:** libsigner/src/tests/signer_state.rs (L789-823)
```rust
#[test]
/// Locks the joint behavior of `reached_agreement` and `reached_disagreement`
/// at a single `total_weight`:
///   - agreement: `>=` 70% (inclusive)
///   - disagreement: `>` 30% (strict)
///   - three regions: [0, 30%] neither, (30%, 70%) disagreement only,
///     [70%, total] both
///
/// Walking 0, 30%, 30%+1, 70%-1, 70% catches a flipped inequality on either
/// side and any drift between the two threshold constants. Also pins the
/// "agreement implies disagreement" relation, which holds as long as the
/// agreement threshold sits above the disagreement one.
fn thresholds_partition_weight_space() {
    let evaluator = evaluator_with_total_weight(1_000_000_000);

    // 0%: neither.
    assert!(!evaluator.reached_agreement(0));
    assert!(!evaluator.reached_disagreement(0));

    // Exactly 30%: strict `>`, so not yet disagreement.
    assert!(!evaluator.reached_agreement(300_000_000));
    assert!(!evaluator.reached_disagreement(300_000_000));

    // One unit past 30%: gap region, disagreement only.
    assert!(!evaluator.reached_agreement(300_000_001));
    assert!(evaluator.reached_disagreement(300_000_001));

    // One unit below 70%: still in the gap.
    assert!(!evaluator.reached_agreement(699_999_999));
    assert!(evaluator.reached_disagreement(699_999_999));

    // Exactly 70%: agreement (`>=`), and disagreement still holds since 70% > 30%.
    assert!(evaluator.reached_agreement(700_000_000));
    assert!(evaluator.reached_disagreement(700_000_000));
}
```
