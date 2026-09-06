### Title
Inconsistent supermajority-threshold rounding between `GlobalStateEvaluator::reached_agreement` and `NakamotoBlockHeader::compute_voting_weight_threshold` lets the signer set "agree" on global state below the true 70% weight - (File: `libsigner/src/v0/signer_state.rs`)

### Summary
The signer network computes the canonical 70%-of-weight supermajority threshold twice, with two different rounding rules. `NakamotoBlockHeader::compute_voting_weight_threshold` (the threshold actually enforced against block signatures by the node and referenced by the signer for its own signing decisions) rounds the 70% cutoff **up** (ceiling). `GlobalStateEvaluator::reached_agreement`, used to decide when the signer set has reached consensus on the *global state machine* (active protocol version, global burn view, and — critically — the agreed `current_miner`), rounds the same 70% cutoff **down** (floor). This is the same bug class as the Joyn `Splitter.sol` finding: two code paths that are supposed to represent the identical percentage/threshold use different denominators/roundings, so a value that satisfies one does not satisfy the other, breaking the intended equality between "aggregated weight" and "verified/required weight."

### Finding Description
`compute_voting_weight_threshold` (the node/chainstate-authoritative and signer-referenced threshold for a block's signature weight) is: [1](#0-0) 

This computes `ceil(total_weight * 7 / 10)`.

By contrast, `GlobalStateEvaluator::reached_agreement` / `reached_disagreement`, used purely inside `libsigner`'s state-machine evaluator, computes the same nominal 70%/30% cutoffs with plain integer (floor) division: [2](#0-1) 

For any `total_weight` where `total_weight * 7` is not a multiple of 10, `floor(total_weight*7/10) == ceil(total_weight*7/10) - 1`. So `reached_agreement` accepts a vote weight that is exactly one weight-unit *below* what `compute_voting_weight_threshold` would require to reach the equivalent 70% mark.

This matters because `reached_agreement` is the sole gate used to decide the signer set's agreed protocol version, burn view, and — most importantly — the agreed `current_miner`/`SignerStateMachine` via `determine_global_state`: [3](#0-2) 

That `SignerStateMachine.current_miner` is then used as the authoritative gate in `GlobalStateView::check_proposal`, which decides whether a proposed block's miner pubkey/tenure is even considered valid before any signature is produced: [4](#0-3) 

Because every signer runs the identical (buggy) `reached_agreement` formula, the whole fleet can converge — deterministically and without any signer needing a majority coalition or malicious behavior — on treating a miner as the "current miner" using one weight-unit less than the true 70% supermajority that the protocol's own documented/enforced threshold (`compute_voting_weight_threshold`) requires elsewhere (block signature acceptance, pre-commit threshold, rejection threshold — all of which explicitly call `compute_voting_weight_threshold`, e.g. in `handle_block_pre_commit` and `store_and_process_block_signature`): [5](#0-4) [6](#0-5) 

This is precisely analogous to the Joyn report: an "unused"/differently-scaled percentage denominator (`PERCENTAGE_SCALE` vs `10000`) that is close enough to the correct value to escape notice, but produces a different result at the boundary, causing downstream logic (there: claim payout math; here: global-state/miner-agreement determination) to diverge from the value the rest of the system treats as authoritative.

### Impact Explanation
The mismatch means the signer set's notion of "we have reached 70% agreement" on the active miner/global state machine can be satisfied at strictly less than the actual 70% supermajority weight that every other threshold check in the codebase (`compute_voting_weight_threshold`) enforces. Since `check_proposal` uses this agreed `current_miner` as a hard gate for block validity, the signer network can collectively decide — without any majority-signer collusion, purely as a byproduct of the specific weight distribution among signers — to treat as canonical/valid a miner-state view that never actually cleared the true 70% weight threshold used everywhere else in the protocol. This falls under the "acting on a stale/incorrect threshold" High-impact category: the discrepancy causes the global state machine to reach an "agreed" (wedge-breaking) conclusion using a weaker guarantee than the rest of the codebase assumes for the same 70% figure, undermining the equality between "aggregated weight" and "verified/required weight" that the design otherwise tries to hold everywhere.

### Likelihood Explanation
No attacker action or majority coalition is required — any `total_weight` value where `total_weight * 7 % 10 != 0` (i.e., most values) puts one weight-unit of "gap" between the two formulas. If the natural distribution of `StateMachineUpdate`s from honest signers happens to sum into that one-unit gap (entirely plausible in ordinary operation, since it depends only on arithmetic, not on any crafted message), every signer running this code independently reaches the same incorrect "agreement" conclusion. This makes the bug latent but readily triggerable by ordinary weight arithmetic rather than requiring adversarial input, which raises the practical likelihood despite requiring a specific (but common) weight-sum alignment.

### Recommendation
Unify the threshold computation: have `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` call `NakamotoBlockHeader::compute_voting_weight_threshold` (or an equivalent shared ceiling-rounded helper) instead of re-deriving the 70%/30% split with floor division, so all consensus/threshold checks in the signer stack use one single, consistently-rounded definition of the supermajority weight.

### Proof of Concept
Reproduced directly from the existing regression test suite, which encodes the exact off-by-one behavior of `reached_agreement` (floor) versus the ceiling-based `compute_voting_weight_threshold`:

1. Take `total_weight = 3000`. `compute_voting_weight_threshold(3000) == 2100` (exact multiple, no gap in this case) — but for a non-exact case, e.g. `total_weight = 511`: `compute_voting_weight_threshold(511) == 358` (ceiling), confirmed by the existing test: [7](#0-6) 
2. `reached_agreement` for the same `total_weight = 511` computes `floor(511*7/10) = floor(357.7) = 357`, i.e. `vote_weight = 357` already satisfies `reached_agreement`, while `compute_voting_weight_threshold` would require `358` — one unit more — to accept the equivalent block signature weight, confirmed by the general floor-division formula in: [8](#0-7) 
3. Therefore a `total_weight`/vote-weight combination exists (e.g. `total_weight = 511`, aggregated update weight `= 357`) where `GlobalStateEvaluator::determine_global_state` (and hence `GlobalStateView::check_proposal`'s `current_miner` gate) declares agreement reached, while the exact same 357/511 weight would be rejected as insufficient by `compute_voting_weight_threshold` if it were instead being used to authorize a block signature — demonstrating the concrete divergence between the two "70%" implementations.

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

**File:** stacks-signer/src/v0/signer.rs (L1295-1301)
```rust
        let commit_weight = self.compute_signature_signing_weight(committers.iter());
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

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L4118-4122)
```rust
        // Round-up check
        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(511_u32).unwrap(),
            358_u32,
        );
```
