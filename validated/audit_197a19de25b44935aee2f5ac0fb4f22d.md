### Title
Global signer state machine uses a rounded-down (floor) threshold instead of the true supermajority threshold, letting a sub-70% weight force a miner/tenure viewpoint switch - ([File: libsigner/src/v0/signer_state.rs])

### Summary
`GlobalStateEvaluator::reached_agreement` computes the 70%-style consensus bar as `total_weight * 7 / 10` using integer floor division, whereas the analogous, canonical bar used for actual block-signature counting, `NakamotoBlockHeader::compute_voting_weight_threshold`, uses a ceiling (`(total*7)/10 + 1` when there's a remainder). This is the off-by-one/rounding class from the reported advisory (an index/threshold check that should be strict but is off by one), applied here to a weight-threshold comparison instead of an array index.

### Finding Description
`reached_agreement` in [1](#0-0)  computes:

```
vote_weight >= total_weight * NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD / 10   // floor
```

while the block-signature approval bar used everywhere else in consensus is [2](#0-1) :

```
ceil = if (total_weight*threshold) % 10 == 0 { 0 } else { 1 };
(total_weight*threshold)/10 + ceil                             // ceiling
```

and is mirrored inside the v0 signer itself when tallying pre-commits/signatures (`compute_voting_weight_threshold`), e.g. [3](#0-2)  and [4](#0-3) .

For any `total_weight` where `total_weight*7` is not a multiple of 10 (e.g. total_weight = 11, 13, 17, 19, ... - a large fraction of real weight distributions), `reached_agreement`'s bar is exactly one weight-unit lower than the ceiling bar used for actual signing. Example: total_weight = 11 → floor bar = 7, ceiling bar = 8. A coalition holding weight 7 (which is a genuine blocking/losing minority for real block signing, since 7 < 8) is reported by `reached_agreement` as having "reached agreement."

`reached_agreement` is the sole determinant of:
- `determine_latest_supported_signer_protocol_version` [5](#0-4) 
- `determine_global_burn_view` [6](#0-5) 
- `determine_global_state` (the agreed `SignerStateMachine`, i.e. current miner/tenure view and tx-replay set) [7](#0-6) 

This `eval: &GlobalStateEvaluator` is fed directly into `SortitionState::is_tenure_valid`/`is_timed_out` and `LocalStateMachine::capitulate_miner_view`, which decide whether a signer flips its local view of "who the current miner is" and which tenure it will build/sign on [8](#0-7) [9](#0-8) . In the v2 chainstate path, `GlobalStateView::check_proposal` accepts or rejects a block proposal strictly based on whether it matches `self.signer_state.current_miner` (`tenure_id`, `parent_tenure_id`, `current_miner_pkh`) [10](#0-9) .

Because the "agreement" bar that decides *which miner/tenure a signer will sign for* is systematically weaker (by up to 1 weight unit, and disproportionately for weight totals not divisible by 10) than the bar actually required to finalize a block signature set, a set of signers whose combined weight is insufficient to ever assemble a real 70% signature quorum can nonetheless force `determine_global_state`/`capitulate_miner_view` to switch the reporting signer's local `current_miner` viewpoint (or global burn view / protocol version). This breaks the intended equality between "what a supermajority of signers can force as the canonical tenure view" and "what weight can actually finalize a signature," i.e., the aggregated-weight-vs-verified-accepts invariant called out in scope.

### Impact Explanation
This does not by itself forge a signature over an invalid block, but it can wedge or misdirect the signer's block-signing decisions: a signer can be made to believe a different miner/tenure is "current" (via `capitulate_viewpoint`/`check_miner_inactivity`) based on a weight below the real supermajority bar, causing `GlobalStateView::check_proposal` (v2) to reject a legitimately canonical proposal as `InvalidMiner`/`ConsensusHashMismatch`/`PubkeyHashMismatch`, or to accept/track a competing miner's proposal that a true supermajority has not actually endorsed. In the worst case this pushes affected signers toward signing for a tenure that a real 70% weight would not have approved, or refusing to sign for the legitimate one - matching the "wedged into never signing valid blocks" / "acting on a stale reward set or threshold" High-impact category. It is bounded by the fact this is a rounding discrepancy of at most one weight unit and only manifests when `total_weight * 7 % 10 != 0`, so the practical severity depends on the specific weight distribution of the signer set for the affected reward cycle.

### Likelihood Explanation
Triggering only requires ordinary `StateMachineUpdate` gossip from any subset of registered signers whose combined weight lands exactly on the floor-vs-ceiling boundary (no majority key, no auth token, no malicious node access needed) - a one-slot or few-slot signer coalition broadcasting believable state updates is sufficient. Whether this boundary is hit depends on the reward cycle's weight totals, which are attacker-observable in advance (the reward set / stacker weights are public), so an attacker can pick timing/participation to land exactly on a boundary total.

### Recommendation
Make `reached_agreement` (and the complementary `reached_disagreement`) use the same ceiling-based threshold computation as `NakamotoBlockHeader::compute_voting_weight_threshold`, i.e. round the 70% bar up rather than down, so the global state-machine agreement bar can never be weaker than the actual block-signature approval bar. Ideally factor out a single shared threshold function used by both `libsigner::v0::signer_state::GlobalStateEvaluator` and `stackslib::chainstate::nakamoto::NakamotoBlockHeader::compute_voting_weight_threshold` to eliminate this class of drift entirely.

### Proof of Concept
1. Compute a reward cycle whose registered signers' weights sum to a `total_weight` with `total_weight * 7 % 10 != 0` (e.g., `total_weight = 11`).
2. Have signers holding a combined weight of 7 (a real minority, since the true supermajority bar is `compute_voting_weight_threshold(11) = 8`) broadcast a `StateMachineUpdate` (e.g., a `BurnBlock`/miner-state update) agreeing on a specific `SignerStateMachine` value.
3. Call `GlobalStateEvaluator::determine_global_state` (as invoked from `LocalStateMachine::capitulate_miner_view`/`update_protocol_version`) with these updates: `reached_agreement(7)` returns `true` because `11*7/10 = 7` (floor), even though 7 < 8, the real signing threshold.
4. Observe that `LocalStateMachine::capitulate_viewpoint` flips the local `current_miner`/burn view based on this "agreement," even though the same weight (7) would fail `NakamotoBlockHeader::compute_voting_weight_threshold`/`verify_signer_signatures` for an actual block signature set at the same total weight - demonstrating the equality break between the state-machine agreement bar and the real signature-approval bar ( [1](#0-0) , [11](#0-10) ).

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

**File:** stacks-signer/src/v0/signer.rs (L1296-1301)
```rust
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

**File:** stacks-signer/src/v0/signer_state.rs (L282-374)
```rust
    /// Check and update our local view of the current miner based on it's tenure's
    /// validity and the validity of the prior sortition
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

        if !is_timed_out {
            return Ok(());
        }

        // the tenure timed out, try to see if we can use the prior tenure instead
        let CurrentAndLastSortition { last_sortition, .. } =
            client.get_current_and_last_sortition()?;
        let Some(last_sortition) = last_sortition
            .and_then(|val| SortitionData::try_from(val).ok())
            .map(|data| SortitionState::new(version, data))
        else {
            warn!("Signer State: Current miner timed out due to inactivity, but could not find a valid prior miner. Allowing current miner to continue");
            return Ok(());
        };

        let sortition_data = last_sortition.data();
        // If we already reverted to the last sortition miner, don't time it out as it means we have already timed out the current sorititon miner
        // as there is no other miner available.
        if &sortition_data.consensus_hash == tenure_id {
            warn!("Signer State: Last sortition miner has timed out, but no prior valid miner. Allowing last sortition miner to continue");
            return Ok(());
        }

        // Only revert to the prior miner if its tenure is the canonical Stacks tip's
        // tenure. A miner only continues (extends) a tenure it won, so if the canonical
        // tip is in some other tenure due to a Bitcoin reorg orphaning the prior
        // sortition's tenure, the prior miner's node has already stopped mining and
        // will never propose again.
        let stacks_tip_ch = client.get_peer_info()?.stacks_tip_consensus_hash;
        if sortition_data.consensus_hash != stacks_tip_ch {
            warn!(
                "Signer State: Current miner timed out due to inactivity, but the canonical stacks tip is not in the prior miner's tenure, so the prior miner cannot continue it. Allowing current miner to continue";
                "stacks_tip_consensus_hash" => %stacks_tip_ch,
                "prior_sortition_consensus_hash" => %sortition_data.consensus_hash,
            );
            return Ok(());
        }

        if !last_sortition.is_tenure_valid(db, client, proposal_config, eval)? {
            warn!("Signer State: Current miner timed out due to inactivity, but prior miner is not valid. Allowing current miner to continue");
            return Ok(());
        }
        let new_active_tenure_ch = &sortition_data.consensus_hash;
        let inactive_tenure_ch = tenure_id.clone();
        state_machine.current_miner = Self::make_miner_state(
            sortition_data.clone(),
            client,
            db,
            proposal_config.tenure_last_block_proposal_timeout,
        )?;
        info!(
            "Signer State: Current tenure timed out, setting the active miner to the prior tenure";
            "inactive_tenure_ch" => %inactive_tenure_ch,
            "new_active_tenure_ch" => %new_active_tenure_ch
        );

        crate::monitoring::actions::increment_signer_agreement_state_change_reason(
            crate::monitoring::SignerAgreementStateChangeReason::InactiveMiner,
        );

        Ok(())
    }
```

**File:** stacks-signer/src/v0/signer_state.rs (L984-1000)
```rust
    pub fn capitulate_miner_view(
        &mut self,
        stacks_client: &StacksClient,
        eval: &mut GlobalStateEvaluator,
        signerdb: &mut SignerDb,
        local_update: &StateMachineUpdateMessage,
        tenure_last_block_proposal_timeout: Duration,
    ) -> Option<StateMachineUpdateMinerState> {
        // First always make sure we consider our own viewpoint
        eval.insert_update(
            stacks_client.get_signer_address().clone(),
            local_update.clone(),
        );

        // Determine the current burn block from the local update
        let (current_burn_block, current_burn_block_height) =
            local_update.content.burn_block_view();
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
