I have enough evidence to write up the finding now. This confirms `check_block_against_global_state` (in `stacks-signer/src/v0/signer.rs`) uses `determine_global_state()`, which relies on `GlobalStateEvaluator::reached_agreement`, to decide the "global signer state" (active miner / burn view / tx replay set) against which block proposals are validated (`GlobalStateView::check_proposal`) — this is squarely on the block-acceptance decision path, not merely diagnostic.

### Title
Global signer state agreement uses a floor-rounded 70% threshold instead of the canonical ceiling-rounded supermajority, letting sub-70% weight force consensus on miner/burn-view/replay-set state - ([File: libsigner/src/v0/signer_state.rs])

### Summary
`GlobalStateEvaluator::reached_agreement` (`libsigner/src/v0/signer_state.rs:171-175`) computes the 70% supermajority threshold as `total_weight * 7 / 10` using plain integer (floor) division, whereas the canonical, consensus-critical definition of the same "70%" threshold, `NakamotoBlockHeader::compute_voting_weight_threshold` (`stackslib/src/chainstate/nakamoto/mod.rs:1194-1207`), rounds *up* (`(total_weight*7)/10 + ceil`). Whenever `total_weight * 7` is not an exact multiple of 10, the floor-based threshold is strictly lower than the true 70% supermajority, letting a coalition with less than the canonical block-signing threshold force the signer's `determine_global_state()` / `determine_global_burn_view()` to reach "agreement." [1](#0-0) 

### Finding Description
`compute_voting_weight_threshold` is the one place `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD` (=7, i.e. 70%) is turned into an actual weight cutoff for accepting a Nakamoto block, and it deliberately rounds the fractional remainder *up*: [2](#0-1) 

This ceiling behavior is verified by the codebase's own regression test (e.g. `total_weight=511` → threshold `358`, i.e. `ceil(511*0.7)=358`, not `floor=357`): [3](#0-2) 

`stacks-signer/src/v0/signer.rs` uses this same ceiling function consistently for actual signature/rejection accounting (`store_and_process_block_signature`, block-rejection consensus): [4](#0-3) [5](#0-4) 

However, the *global signer state machine* — which decides the agreed active miner, agreed burn view, agreed signer-protocol version, and agreed transaction-replay set — is evaluated with a **different, floor-rounded** implementation of the same nominal "70%" constant: [6](#0-5) 

This global state is directly consumed on the block-proposal validation path: `check_block_against_global_state` fetches `self.global_state_evaluator.determine_global_state()` and uses it to build the `GlobalStateView` that gates whether a proposal is rejected: [7](#0-6) 

`determine_global_state` and `determine_global_burn_view` both gate on `reached_agreement`: [8](#0-7) 

Concretely, for `total_weight = 11` (e.g. 11 equal-weight signers), the canonical threshold is `ceil(11*7/10) = ceil(7.7) = 8` (≈72.7%), but `reached_agreement` uses `floor(11*7/10) = 7` (≈63.6%). A set of signers holding only 7/11 (63.6%) weight — clearly short of the intended 70% supermajority, and nowhere near the 8/11 that the canonical block-approval logic would require — is enough to make the local signer treat a *particular burn view, active-miner tenure, and transaction-replay set* as the agreed global state. Any weight distribution where `total_weight * 7 % 10 != 0` exhibits the same gap (up to just under one full "unit" of weight out of `total_weight`, i.e. up to ~10%/`total_weight` percentage points, non-negligible for small signer sets/weight totals — directly analogous to the referenced precision-loss report where small denominators amplify a rounding-direction mismatch).

Unlike `reached_agreement`, `reached_disagreement`'s floor-based blocking-minority check (`> total_weight*3/10`) is mathematically complementary to the ceiling-based acceptance threshold (`floor(3n/10) = n - ceil(7n/10)`), so it stays consistent with the canonical threshold. Only the acceptance side (`reached_agreement`) is under-strict.

### Impact Explanation
`check_block_against_global_state` → `determine_global_state()` decides which burn view and which miner's tenure a signer considers "canonical" before it validates and eventually pre-commits/signs a block proposal (`GlobalStateView::check_proposal`). Because `reached_agreement` accepts a lower bar than the canonical 70% supermajority intended by `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD`, a coalition of signers that does not actually hold the intended supermajority can force the local signer to adopt their view of the active miner/burn block/replay set. If that view diverges from the true canonical chain (e.g. during a fork or miner transition), the signer can proceed to validate and sign a block against a miner/burn view that a correctly-computed 70% threshold would not yet have endorsed — i.e., the signer's decision to sign is being taken on a stale/incorrectly-computed threshold, matching the "acting on a stale reward set/threshold" High-impact class, with a path toward endorsing a non-canonical view of the chain.

### Likelihood Explanation
No malicious majority or private key access is required: any natural signer-weight distribution where `total_weight * 7` is not a multiple of 10 (i.e. most values of `total_weight`) triggers the discrepancy. It is purely a function of aggregate weight granularity and requires no more coordination than normal gossip of `StateMachineUpdate` messages already exchanged over StackerDB (`handle_state_machine_update` inserts any peer's update directly into `global_state_evaluator`).

### Recommendation
Make `GlobalStateEvaluator::reached_agreement` use the same ceiling-rounded computation as `NakamotoBlockHeader::compute_voting_weight_threshold` (or call that function directly), so that "70% agreement" means the same weight cutoff everywhere the constant `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD` is used.

### Proof of Concept
1. Construct a `GlobalStateEvaluator` with `total_weight = 11` (e.g. 11 signer addresses each with weight 1, as in `generate_random_address_with_equal_weights`/`evaluator_with_total_weight` test helpers in `libsigner/src/tests/signer_state.rs`).
2. Have 7 of the 11 signers broadcast a `StateMachineUpdate` agreeing on burn view `B` / miner `M`; the other 4 broadcast a different view.
3. `reached_agreement(7)` returns `true` (`7 >= floor(11*7/10) = 7`), so `determine_global_burn_view`/`determine_global_state` report consensus on `(B, M)`, even though the canonical block-approval threshold for `total_weight=11` is `8` (`compute_voting_weight_threshold(11) == 8`, verified by the existing `test_compute_voting_weight_threshold` pattern), i.e. only 63.6% support instead of the intended ≥70%.
4. The local signer's `check_block_against_global_state` then validates/pre-commits proposals against this under-supported view.

### Citations

**File:** libsigner/src/v0/signer_state.rs (L81-144)
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

**File:** stacks-signer/src/v0/signer.rs (L944-975)
```rust
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
```

**File:** stacks-signer/src/v0/signer.rs (L2305-2313)
```rust
        let total_reject_weight =
            self.compute_signature_signing_weight(rejection_addrs.iter().map(|(addr, _)| addr));
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
        if total_reject_weight.saturating_add(min_weight) <= total_weight {
```

**File:** stacks-signer/src/v0/signer.rs (L2494-2503)
```rust
        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
        let total_signature_weight = self.compute_signature_signing_weight(addrs_to_sigs.keys());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });

        if min_weight > total_signature_weight {
```
