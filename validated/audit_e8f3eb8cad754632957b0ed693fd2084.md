### Title
Global-state agreement threshold uses floor division instead of the canonical ceiling, weakening the 70% supermajority required for signer consensus decisions - (File: `libsigner/src/v0/signer_state.rs`)

### Summary
The `GlobalStateEvaluator::reached_agreement` helper, which the signer set uses to reach consensus on shared state (current miner, burn view, active protocol version, and the transaction-replay set), computes the required threshold as `total_weight * 7 / 10` using **floor** integer division. The canonical, consensus-critical threshold used to actually validate a block's signature weight, `NakamotoBlockHeader::compute_voting_weight_threshold`, computes the same 70% quantity but explicitly **rounds up** (`ceil`) when the division is not exact. These two "70% of total signer weight" computations are supposed to represent the same supermajority equality, but they diverge whenever `total_weight * 7` is not a multiple of 10.

### Finding Description
`reached_agreement` in `libsigner/src/v0/signer_state.rs` is defined as: [1](#0-0) 

This is a strict floor: `vote_weight >= (total_weight * 7) / 10` with truncation. Contrast this with the canonical block-approval threshold used elsewhere in the same codebase to gate actual block signature acceptance: [2](#0-1) 

and confirmed by its own test suite, which explicitly checks a "round-up" case: [3](#0-2) 

For any `total_weight` where `total_weight * 7 % 10 != 0` (e.g. `total_weight = 11` → floor gives `7`, ceil gives `8`), `GlobalStateEvaluator::reached_agreement` accepts a strictly smaller vote weight as "agreement" than the canonical block-approval threshold would require. This function is used to drive several safety-relevant global-state decisions inside the signer's state machine: `determine_latest_supported_signer_protocol_version`, `determine_global_burn_view`, and `determine_global_state` (which fixes the agreed `current_miner` and `tx_replay_set`): [4](#0-3) [5](#0-4) 

Because these functions decide what the signer considers the network's agreed-upon miner/state/protocol version, the off-by-one-weight-unit weaker threshold means the local state machine can "lock in" an agreed `current_miner` or `tx_replay_set` on weight that is below the true 70% supermajority (e.g., 63.6% instead of 72.7% for `total_weight=11`). This breaks the intended equality between "signer's locally-perceived aggregated weight has reached the supermajority" and "the supermajority has actually been verified," which is exactly the class of bug the report describes (a rounding-down that silently weakens/loses a value that should never be reduced below its true threshold).

I was not able to fully trace, within the remaining tool budget, every downstream consumer of `determine_global_state()`'s `current_miner`/`tx_replay_set` result inside `stacks-signer/src/v0/signer.rs` to confirm whether this looser agreement can be leveraged by a single miner (plus ordinary StackerDB gossip of `StateMachineUpdate` messages, which the report's rules permit) to make honest signers treat a non-canonical miner or a divergent transaction-replay set as "the" agreed global state, and thus sign for it. The `reached_agreement`/`reached_disagreement` u32-overflow issues have already been fixed and covered by regression tests in `libsigner/src/tests/signer_state.rs` (lines 731-787), but no equivalent regression test exists for the floor-vs-ceiling divergence from the canonical threshold, suggesting it was not previously identified as a discrepancy.

### Impact Explanation
If confirmed reachable, this would fall into "acting on a stale reward set/threshold" / potentially "signing an invalid or non-canonical block": a signer's global-state machine could conclude that a specific miner or specific tx-replay set has reached the required supermajority agreement when, by the canonical 70% ceiling rule enforced elsewhere in the codebase (`compute_voting_weight_threshold`), it has not. This is a discrepancy between two implementations of what should be the identical "70% supermajority" equality.

### Likelihood Explanation
The divergence is deterministic and requires no majority collusion — it exists purely from the floor-vs-ceiling difference in how the same threshold constant (`NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD = 7`) is applied to `total_weight`. It triggers automatically whenever `total_weight * 7` isn't a multiple of 10 (a very common case for reward-set sizes seen in practice, e.g. any `total_weight` not a multiple of 10). However, I could not confirm the full exploit chain (i.e., whether an attacker fully controls which weight values land in these "gap" regions, and whether the affected global-state fields are used in a check that gates block signing directly) within the available budget, so I present this as an unconfirmed analog rather than a fully proven vulnerability.

### Recommendation
Change `reached_agreement`/`reached_disagreement` in `libsigner/src/v0/signer_state.rs` to use the same ceiling-based computation as `NakamotoBlockHeader::compute_voting_weight_threshold`, so that the "global agreement" threshold used by the signer's local state machine is provably identical to the canonical block-approval threshold, and add a regression test analogous to `test_compute_voting_weight_threshold` covering the round-up boundary case for `reached_agreement`.

### Proof of Concept
Not independently constructed/verified against the full signer message-processing path in the time available; the core discrepancy is demonstrable purely arithmetically: for `total_weight = 11`, `compute_voting_weight_threshold(11) = ceil(77/10) = 8`, while `GlobalStateEvaluator::reached_agreement` with the same total_weight treats `vote_weight = 7` (63.6%) as sufficient (`7 >= 77/10 = 7` under floor truncation), 1 weight unit below the canonical requirement of 8 (72.7%).

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

**File:** libsigner/src/v0/signer_state.rs (L101-101)
```rust
    /// Check if there is an agreed upon global state
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

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L4118-4123)
```rust
        // Round-up check
        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(511_u32).unwrap(),
            358_u32,
        );
    }
```
