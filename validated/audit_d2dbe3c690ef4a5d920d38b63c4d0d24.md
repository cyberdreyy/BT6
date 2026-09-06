### Title
`GlobalStateEvaluator::reached_agreement`/`reached_disagreement` use floor division while the on-chain block-approval threshold rounds up, letting sub-quorum weight fix the signer set's "global state" (current miner / tenure) - ([File: libsigner/src/v0/signer_state.rs])

### Summary
`NakamotoBlockHeader::compute_voting_weight_threshold` (the threshold actually enforced when verifying a block's aggregated signer signatures) always rounds the 70% quorum **up** (`ceil`) so that at least 70% of weight is genuinely required [1](#0-0) . `GlobalStateEvaluator::reached_agreement`/`reached_disagreement`, which decide when the signer set has reached "global agreement" on things like the active miner, tenure, burn view, protocol version and tx-replay set, instead compute the same nominal 70%/30% split with plain integer division that **floors** [2](#0-1) . For small/typical signer-weight totals this floor can be substantially below the true 70% supermajority (e.g. with 4 equal-weight signers, floor gives `28/10=2` i.e. 50%, while the ceil-based on-chain rule requires `3` i.e. 75%).

### Finding Description
The two "70%" computations are meant to encode the same protocol invariant (a genuine supermajority of signer weight), but they round in opposite directions:

- On-chain / signature-verification path (`verify_signer_signatures` → `compute_voting_weight_threshold`) rounds **up**, so it always demands *at least* 70% of weight — the safe direction. [3](#0-2) 
- Signer-local consensus path (`GlobalStateEvaluator::reached_agreement` / `reached_disagreement`) rounds **down**, so it can declare "global agreement" with strictly less than the true 70% (and can declare a "blocking minority" with less than the true 30%). [2](#0-1) 

`determine_global_state`/`determine_global_burn_view`/`determine_latest_supported_signer_protocol_version` all gate on `reached_agreement`, and the resulting `SignerStateMachine` (including `current_miner`, `tenure_id`, `parent_tenure_id`) is used directly to accept or reject block proposals once the global-state protocol version is active: [4](#0-3)  feeds into `check_block_against_global_state` → `GlobalStateView::check_proposal`, which rejects any proposal whose `consensus_hash`/miner key does not match the "agreed" `current_miner` state [5](#0-4) [6](#0-5) .

Because the floor rule under-counts the required weight, a coalition holding less weight than the real 70% quorum (but more than the floor threshold) can flip what every other signer treats as the canonical "current miner"/tenure view purely by broadcasting `StateMachineUpdate` messages over StackerDB — no majority-of-keys compromise, no invalid signature, and no interaction with the node's own `verify_signer_signatures` check is required to *change the signers' shared belief* about which miner/tenure is authoritative. This is exactly the reported bug class: the same conceptual threshold rounded in two different directions, with the laxer rounding (favoring the party trying to cross the threshold) sitting on the path that decides consensus, while the stricter rounding sits on the path that is supposed to be the actual safety backstop.

### Impact Explanation
This is a High-severity liveness/consistency issue: signers can be steered by sub-quorum gossip into adopting a `SignerStateMachine` (miner/tenure/burn view) that does not reflect a genuine 70% supermajority. Since `check_block_against_global_state` uses this view as the sole gate for accepting/rejecting proposals under the global-state protocol version [7](#0-6) , honest signers can end up rejecting a legitimately-supported miner's blocks, or pre-committing/signing on the basis of a miner view that a true supermajority never endorsed — i.e., "acting on a stale/incorrectly-derived threshold view," matching the High-impact bucket in scope.

### Likelihood Explanation
No signing keys, node access, or actual majority collusion are required — only enough signer weight to sit between the floor(70%) and ceil(70%) bands (which, per the arithmetic above, can be as low as ~50–65% of weight for small signer sets) sending ordinary `StateMachineUpdateMessage` gossip. This is reachable by any subset of participating signers (or a set of signers whose views happen to be split near this boundary during normal operation, e.g. during a miner handoff), making it a plausible, not purely theoretical, occurrence — the project's own test comments acknowledge these boundary computations are being exercised in exactly this "5 signers / 4-of-5 (70%)" scenario [8](#0-7) .

### Recommendation
Make `reached_agreement`/`reached_disagreement` round in the direction that is safe for the invariant they gate, mirroring `compute_voting_weight_threshold`'s ceiling behavior for the 70% agreement bound, and using the complementary floor/ceil choice for the 30% disagreement bound so it can never be satisfied with less than a genuine blocking-minority. Concretely, `reached_agreement` should require `vote_weight` to be at least the ceiling of `total_weight * 7/10` (as in `NakamotoBlockHeader::compute_voting_weight_threshold`), not the floor.

### Proof of Concept
Not executed (analysis only, per scan constraints). Arithmetic demonstration: for `total_weight = 4` (4 equal-weight signers), `NakamotoBlockHeader::compute_voting_weight_threshold(4)` returns `3` (75%, ceil of 2.8) per its own rounding rule [9](#0-8) , while `GlobalStateEvaluator::reached_agreement(2)` on the same `total_weight=4` returns `true` (`4*7/10 = 2` via floor division) [10](#0-9) , i.e. only 50% of signer weight is enough to fix the "global state" (miner/tenure view) that gates block-proposal acceptance, well under the 75% a true supermajority would require for that signer-weight distribution.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1180-1207)
```rust
        let threshold = Self::compute_voting_weight_threshold(total_weight)?;

        if total_weight_signed < threshold {
            return Err(ChainstateError::InvalidStacksBlock(format!(
                "Not enough signatures. Needed at least {} but got {} (out of {})",
                threshold, total_weight_signed, total_weight,
            )));
        }

        return Ok(total_weight_signed);
    }

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

**File:** stacks-signer/src/v0/signer.rs (L865-870)
```rust
        if state_version.uses_global_state() {
            self.check_block_against_global_state(stacks_client, &block_info.block)
        } else {
            self.check_block_against_local_state(stacks_client, sortition_state, &block_info.block)
        }
    }
```

**File:** stacks-signer/src/v0/signer.rs (L941-998)
```rust
    /// Check if block should be rejected based on global signer state
    /// Will return a BlockRejection if the block is invalid, none otherwise.
    /// This is the Post-global signer state activation path
    fn check_block_against_global_state(
        &mut self,
        stacks_client: &StacksClient,
        block: &NakamotoBlock,
    ) -> Option<BlockRejection> {
        let signer_signature_hash = block.header.signer_signature_hash();
        let block_id = block.block_id();
        let Some(global_state) = self.global_state_evaluator.determine_global_state() else {
            warn!(
                "{self}: Cannot validate block, no global signer state";
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_id,
                "local_signer_state" => ?self.local_state_machine
            );
            return Some(self.create_block_rejection(RejectReason::NoSignerConsensus, block));
        };

        let global_state_view = GlobalStateView {
            signer_state: global_state,
            config: self.proposal_config.clone(),
        };

        info!(
            "{self}: Evaluating proposal against global state";
            "signer_state" => ?global_state_view.signer_state,
            "signer_signature_hash" => %signer_signature_hash,
            "block_id" => %block_id,
            "local_signer_state" => ?self.local_state_machine,
        );

        // Check if proposal can be rejected now if not valid against the global state
        match global_state_view.check_proposal(stacks_client, &mut self.signer_db, block) {
            // Error validating block
            Err(RejectReason::ConnectivityIssues(e)) => {
                warn!(
                    "{self}: Error checking block proposal: {e}";
                    "signer_signature_hash" => %signer_signature_hash,
                    "block_id" => %block_id,
                );
                Some(self.create_block_rejection(RejectReason::ConnectivityIssues(e), block))
            }
            // Block proposal is bad
            Err(reject_code) => {
                warn!(
                    "{self}: Block proposal invalid";
                    "signer_signature_hash" => %signer_signature_hash,
                    "block_id" => %block_id,
                    "reject_reason" => %reject_code,
                    "reject_code" => ?reject_code,
                );
                Some(self.create_block_rejection(reject_code, block))
            }
            // Block proposal passed check, still don't know if valid
            Ok(_) => None,
        }
```

**File:** stacks-signer/src/chainstate/v2.rs (L111-152)
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
```

**File:** stacks-node/src/tests/signer/v0/tenure_extend.rs (L3582-3591)
```rust
    // What we expect the short-timeout signers to broadcast after switching back
    // to miner 1, and which signer set must reach 70% agreement for the wait to
    // succeed. For FavourIncomingMiner the short-timeout signers are the
    // majority, and we need their switched-back broadcasts to flip the *global*
    // state to miner 1 BEFORE we unstall miner 2 - otherwise miner 2's
    // BlockFound is evaluated against stale global state and gets accepted
    // instead of rejected. The long-timeout minority signer never switches back
    // within this window, so waiting against the full signer set (5) with the
    // 70% threshold (>= 4) is exactly the threshold the GlobalStateEvaluator
    // uses to flip the global state.
```
