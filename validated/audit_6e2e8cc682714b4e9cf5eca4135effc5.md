### Title
Divergent rounding in the signer's global-agreement threshold vs. the canonical block-approval threshold weakens the 70% quorum guarantee - (File: `libsigner/src/v0/signer_state.rs`)

### Summary
The signer network computes the "70% signer-weight quorum" concept in two independent places that must agree for the signer set's safety guarantees to hold: the canonical, consensus-critical `NakamotoBlockHeader::compute_voting_weight_threshold` (used to verify block signatures on-chain and to gate signer block-response/pre-commit logic) and `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` (used by every signer to decide when the network has reached consensus on its local state machine — current miner, burn-block view, tx-replay set, protocol version). The two formulas round in opposite directions, so for many `total_weight` values a coalition can be judged to have "reached agreement" by the signer's local state machine with strictly less weight than the canonical protocol requires for a block to actually be approved on-chain — exactly the "shares != tokens" conversion-mismatch bug class from the referenced report, applied to signer weight/threshold accounting instead of token shares.

### Finding Description
`NakamotoBlockHeader::compute_voting_weight_threshold` computes the canonical minimum weight needed to approve a Nakamoto block using ceiling division: [1](#0-0) 

This is the formula enforced during actual on-chain signature verification (`verify_signer_signatures`) and mirrored by the node's `stacks-node/src/nakamoto_node/signer_coordinator.rs` / `stackerdb_listener.rs` weight_threshold logic, and by the signer's own pre-commit/threshold checks in `stacks-signer/src/v0/signer.rs` (`handle_block_pre_commit`, `store_and_process_block_signature`), all of which call the same `compute_voting_weight_threshold`: [2](#0-1) [3](#0-2) 

In contrast, `libsigner`'s `GlobalStateEvaluator`, which every v0 signer uses to determine whether the network has reached agreement on its *local state machine* (current miner, burn view, tx replay set, supported protocol version), uses a **floor**-division formula that never adds the ceiling correction: [4](#0-3) 

`reached_agreement`/`reached_disagreement` are the sole gating condition for `determine_latest_supported_signer_protocol_version`, `determine_global_burn_view`, and `determine_global_state` (which itself computes the agreed `current_miner` and `tx_replay_set`): [5](#0-4) [6](#0-5) [7](#0-6) 

These global-state results feed directly into a signer's tenure/miner-validity checks (`SortitionState::is_tenure_valid`, `LocalStateMachine::capitulate_miner_view`, `check_miner_inactivity`) in `stacks-signer/src/v0/signer_state.rs`, which in turn gate whether a signer treats a proposed block's miner/tenure as valid and therefore whether it signs the block: [8](#0-7) [9](#0-8) 

Concretely, for `total_weight = 11` and `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD = 7` (70%, confirmed by the existing unit test that shows `compute_voting_weight_threshold(100) == 70`, i.e. `threshold = 7`): [10](#0-9) 

- `compute_voting_weight_threshold(11)`: `11*7 = 77`, `77 % 10 = 7 != 0` → ceil = 1 → `77/10 + 1 = 8`. The canonical/on-chain threshold requires **8/11 (≈72.7%)**.
- `reached_agreement(vote_weight)` for the same `total_weight = 11`: `11*7/10 = 77/10 = 7` (floored, no ceiling term). A coalition of only **7/11 (≈63.6%)** is judged to have "reached agreement".

So a subset of signers holding 7/11 of the weight — a minority below the canonical 70% threshold and below what would actually be required for a block signature to be valid on-chain — is treated by every signer's `GlobalStateEvaluator` as having established the network's agreed current miner / burn view / tx-replay set. This is the same class of defect as the report: two formulas that are supposed to compute the *same* ratio-based quantity (here, "70% of weight") diverge because one truncates and the other doesn't, breaking the intended equality between "signer-perceived consensus" and "protocol-canonical consensus".

### Impact Explanation
This breaks the equality between the weight-threshold enforced when a block's signatures are canonically verified and the weight-threshold each signer uses internally to decide it has reached quorum on the current miner/tenure/tx-replay view that governs its willingness to sign blocks. A signer can be steered by a sub-quorum coalition (as low as ~63.6% depending on `total_weight`) into adopting a `current_miner`/tx-replay state that has not actually met the genuine 70% bar the protocol intends, causing it to validate/sign blocks (or capitulate to a different miner's tenure, or adopt a different tx-replay set) based on a weaker-than-designed agreement threshold. This falls into the "acting on a mis-computed threshold" high-impact bucket: the signer's safety-relevant decisions (which miner/tenure/replay-set it treats as authoritative, and therefore which blocks it is willing to sign) rest on a threshold formula that is inconsistent with — and strictly weaker than — the one actually enforced on-chain.

### Likelihood Explanation
This is triggerable purely through the normal `StateMachineUpdate` gossip mechanism that every signer already sends/receives (no majority collusion, no key compromise required beyond controlling the specific fraction of weight computed above, which for many `total_weight` values is meaningfully below the canonical 70%). The divergence exists for any `total_weight` where `(total_weight * 7) % 10 != 0`, which is the majority of possible weight totals, making this reachable under ordinary reward-set configurations rather than a rare edge case.

### Recommendation
Use a single shared implementation of the weight-threshold computation (the ceiling-based `NakamotoBlockHeader::compute_voting_weight_threshold`) for both the canonical block-approval logic and `GlobalStateEvaluator::reached_agreement`/`reached_disagreement`, so that "70% agreement" and "30% blocking minority" mean exactly the same weight cutoffs everywhere in the signer/network stack.

### Proof of Concept
1. Construct a reward set with `total_weight = 11` (e.g., signers with weights summing to 11) and `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD = 7`.
2. On-chain / canonical threshold: `NakamotoBlockHeader::compute_voting_weight_threshold(11)` returns `8` (see `stackslib/src/chainstate/nakamoto/mod.rs:1194-1207`), i.e. a block needs signatures totalling at least 8/11 weight to be valid.
3. Signer-side global agreement: have signers controlling `7` total weight send matching `StateMachineUpdate`s (e.g., agreeing on the same `current_miner` or burn-block view). `GlobalStateEvaluator::reached_agreement(7)` (see `libsigner/src/v0/signer_state.rs:171-175`) evaluates `7 >= 11*7/10 = 7` → `true`.
4. Every signer's `determine_global_state`/`determine_global_burn_view` therefore concludes "global agreement reached" at 7/11 (~63.6%) weight, well below the 8/11 (~72.7%) weight that would actually be required to get a block signed and accepted on-chain — demonstrating the threshold mismatch that lets signers act on a weaker-than-intended consensus signal.

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

**File:** stacks-signer/src/v0/signer.rs (L1295-1301)
```rust
        let commit_weight = self.compute_signature_signing_weight(committers.iter());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
```

**File:** stacks-signer/src/v0/signer.rs (L2496-2501)
```rust
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
```

**File:** libsigner/src/v0/signer_state.rs (L72-76)
```rust
        for (version, weight_support) in protocol_versions.into_iter().rev() {
            total_weight_support += weight_support;
            if self.reached_agreement(total_weight_support) {
                return Some(version);
            }
```

**File:** libsigner/src/v0/signer_state.rs (L93-96)
```rust
            *entry += weight;
            if self.reached_agreement(*entry) {
                return Some((burn_block, burn_block_height));
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

**File:** stacks-signer/src/v0/signer_state.rs (L284-311)
```rust
    pub fn check_miner_inactivity(
        &mut self,
        db: &mut SignerDb,
        client: &StacksClient,
        proposal_config: &ProposalEvalConfig,
        eval: &GlobalStateEvaluator,
    ) -> Result<(), SignerChainstateError> {
        let Self::Initialized(ref mut state_machine) = self else {
            // no inactivity if the state machine isn't initialized
            return Ok(());
        };

        let MinerState::ActiveMiner { ref tenure_id, .. } = state_machine.current_miner else {
            // no inactivity if there's no active miner
            return Ok(());
        };

        let version = SortitionStateVersion::from_protocol_version(
            state_machine.active_signer_protocol_version,
        );
        let is_timed_out = SortitionState::is_timed_out(
            &version,
            tenure_id,
            db,
            client.get_signer_address(),
            proposal_config,
            eval,
        )?;
```

**File:** stacks-signer/src/v0/signer_state.rs (L670-714)
```rust

        let CurrentAndLastSortition {
            current_sortition,
            last_sortition,
        } = client.get_current_and_last_sortition()?;

        let version = SortitionStateVersion::from_protocol_version(
            prior_state_machine.active_signer_protocol_version,
        );
        let cur_sortition = SortitionState::new(version.clone(), current_sortition.try_into()?);
        let is_current_valid = cur_sortition.is_tenure_valid(db, client, proposal_config, eval)?;

        let miner_state = if is_current_valid {
            Self::make_miner_state(
                cur_sortition.data().clone(),
                client,
                db,
                proposal_config.tenure_last_block_proposal_timeout,
            )?
        } else {
            let last_sortition_data = last_sortition
                .ok_or_else(|| {
                    ClientError::InvalidResponse(
                        "Fetching latest and last sortitions failed to return both sortitions"
                            .into(),
                    )
                })?
                .try_into()?;

            let last_sortition = SortitionState::new(version, last_sortition_data);
            let is_last_valid =
                last_sortition.is_tenure_valid(db, client, proposal_config, eval)?;

            if is_last_valid {
                Self::make_miner_state(
                    last_sortition.data().clone(),
                    client,
                    db,
                    proposal_config.tenure_last_block_proposal_timeout,
                )?
            } else {
                warn!("Signer State: Neither the current nor the prior sortition winner is considered a valid tenure");
                MinerState::NoValidMiner
            }
        };
```

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L4096-4116)
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
```
