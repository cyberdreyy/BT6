### Title
Floor-rounded 70%/30% weight thresholds in `GlobalStateEvaluator` let a signer capitulate to a new "current miner" (and update burn view / protocol version / tx-replay-set) with less than the true supermajority — ([File: libsigner/src/v0/signer_state.rs])

### Summary
`GlobalStateEvaluator::reached_agreement` and `reached_disagreement` compute the 70%/30% weight thresholds with plain integer (floor) division instead of rounding up, unlike the equivalent and already-hardened `NakamotoBlockHeader::compute_voting_weight_threshold`, which explicitly adds a ceiling term. This is the same rounding-direction defect as the referenced report: a value that gates an accept/agree decision is truncated down instead of rounded up, silently lowering the bar for "reached agreement."

### Finding Description
`reached_agreement` and `reached_disagreement`: [1](#0-0) 
compute `total_weight * 7 / 10` and `total_weight * 3 / 10` using integer (flooring) division, with no ceiling adjustment. Compare this with the consensus-critical, node-side threshold used to actually validate block signatures, which explicitly ceils: [2](#0-1) 
and is tested to require rounding up (e.g. `511 -> 358`, i.e. `ceil(511*0.7)`): [3](#0-2) 

Because `GlobalStateEvaluator`'s helpers flooring instead of ceiling, for any `total_weight` not a multiple of 10, the effective agreement bar is strictly below the intended 70% supermajority (e.g. `total_weight=11`: floor(11*7/10)=7, so 7/11≈63.6% is treated as "reached agreement," when the intended rule requires ≥70%, i.e. 8/11). The same weakening applies to `reached_disagreement`'s 30% blocking-minority check.

These helpers are load-bearing for the signer's local state machine, not just cosmetic logging: `capitulate_miner_view` uses `reached_disagreement`/`reached_agreement` to decide whether to switch the local view of the "current miner" to a competing candidate: [4](#0-3) 
and `determine_global_burn_view` / `determine_global_state` (burn view, active miner, protocol version, tx-replay-set agreement) use `reached_agreement` the same way: [5](#0-4) [6](#0-5) 

Once `capitulate_miner_view` returns a `new_miner`, the signer immediately overwrites its `LocalStateMachine` and, if the miner pubkey-hash mismatches the local sortition view, marks the current sortition's miner as invalid: [7](#0-6) 
This local-state transition subsequently governs which proposals the signer treats as coming from a legitimate/active miner (via `check_block_against_signer_db_state` / sortition validity checks fed by this same `eval`), so an under-threshold "agreement" can steer a signer's proposal-acceptance behavior toward a miner/tenure that a true 70% supermajority did not actually endorse.

### Impact Explanation
This does not directly forge a signature over an invalid block by itself, but it corrupts the equality the protocol relies on ("aggregated weight ⩾ verified 70% threshold") used to decide the signer's authoritative local view of the active miner, burn view, active protocol version, and tx-replay set. A signer capitulating to a "new_miner" view backed by less than the true supermajority can begin validating/signing proposals from a miner/tenure that the honest supermajority had not actually agreed upon, and can mark another (possibly legitimately still-active) miner's sortition as `InvalidatedBeforeFirstBlock`. This is consistent with the report's High-impact category: acting on a stale/incorrectly-thresholded state ("acting on a stale reward set/threshold").

Note that the actual on-chain, node-verified block-signature threshold (`compute_voting_weight_threshold` in `stackslib`) is unaffected — it already ceils correctly — so this bug cannot by itself make an invalid block canonical; its blast radius is confined to the signer-side, off-chain `GlobalStateEvaluator` consensus used for view reconciliation (miner/burn-view/protocol-version/tx-replay-set capitulation).

### Likelihood Explanation
Triggering requires only naturally-occurring signer weight distributions where `total_weight * 7` (or `* 3`) is not a multiple of 10 — which is the common case, not an edge case — combined with weight aggregating into the truncation gap (e.g., between 63.6% and 70% for `total_weight=11`). No majority or key compromise is needed to *create* the rounding gap; it's a deterministic property of the arithmetic that shaves the effective threshold on every non-exact-multiple-of-10 weight total. It does still require that enough weight (just under true 70%) actually vote for a specific candidate state, which can occur during ordinary gossip/state-machine-update propagation, particularly during miner transitions or forks where address weights split unevenly.

### Recommendation
Change `reached_agreement` and `reached_disagreement` in `libsigner/src/v0/signer_state.rs` to use the same ceiling logic as `NakamotoBlockHeader::compute_voting_weight_threshold` (i.e., compute `ceil(total_weight * threshold / 10)` for agreement and the correct complementary ceiling/floor pairing for the blocking-minority check) so that "reached agreement" always requires at least the true supermajority weight and cannot be satisfied by silently-truncated values.

### Proof of Concept
1. Construct a `GlobalStateEvaluator` with `total_weight = 11` (e.g., signer weights summing to 11 units across multiple addresses).
2. Have addresses submit `StateMachineUpdate`s such that a particular candidate view (e.g., a specific `current_miner` / burn block / protocol version) accumulates exactly `7` weight units (63.6% of total).
3. `reached_agreement(7)` returns `true` because `u64::from(11).strict_mul(7) / 10 == 7` (floor), even though `7/11 < 0.70`.
4. Any of `determine_global_burn_view`, `determine_global_state`, `determine_latest_supported_signer_protocol_version`, or `capitulate_miner_view` (all gated by `reached_agreement`/`reached_disagreement`) will accept this sub-threshold vote as consensus, causing the signer to update its local view (miner, burn block, protocol version, or replay set) without the actually-required 70% supermajority backing it. [1](#0-0)

### Citations

**File:** libsigner/src/v0/signer_state.rs (L82-99)
```rust
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

**File:** libsigner/src/v0/signer_state.rs (L126-140)
```rust
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

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L4118-4122)
```rust
        // Round-up check
        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(511_u32).unwrap(),
            358_u32,
        );
```

**File:** stacks-signer/src/v0/signer_state.rs (L943-978)
```rust
        if current_miner != &new_miner {
            info!("Signer State: Capitulating local state machine's current miner viewpoint";
                "current_miner" => ?current_miner,
                "new_miner" => ?new_miner,
                "burn_block" => %burn_block,
                "burn_block_height" => burn_block_height,
                "tx_replay_set" => ?tx_replay_set,
            );
            crate::monitoring::actions::increment_signer_agreement_state_change_reason(
                crate::monitoring::SignerAgreementStateChangeReason::MinerViewUpdate,
            );
            Self::monitor_miner_parent_tenure_update(current_miner, &new_miner);

            *self = Self::Initialized(SignerStateMachine {
                burn_block: burn_block.clone(),
                burn_block_height,
                current_miner: new_miner.clone().into(),
                active_signer_protocol_version: local_update.active_signer_protocol_version,
                tx_replay_set,
            });

            match new_miner {
                StateMachineUpdateMinerState::ActiveMiner {
                    current_miner_pkh, ..
                } => {
                    if let Some(sortition_state) = sortition_state {
                        // if there is a mismatch between the new_miner ad the current sortition view, mark the current miner as invalid
                        if current_miner_pkh != sortition_state.cur_sortition.data.miner_pkh {
                            sortition_state.cur_sortition.miner_status =
                                SortitionMinerStatus::InvalidatedBeforeFirstBlock
                        }
                    }
                }
                StateMachineUpdateMinerState::NoValidMiner => (),
            }
        }
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
