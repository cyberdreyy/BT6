### Title
Global state-machine agreement/disagreement thresholds are computed with floor rounding while the on-chain block-approval threshold uses ceiling rounding, producing a mismatched consensus boundary - (File: `libsigner/src/v0/signer_state.rs`)

### Summary
`GlobalStateEvaluator::reached_agreement()` and `reached_disagreement()` independently re-derive the "70% weight" consensus test using integer floor division, while the canonical threshold implementation, `NakamotoBlockHeader::compute_voting_weight_threshold()`, computes the same 70% test using ceiling division. This is structurally the same class of bug as the IdleCDO report: two implementations of "the same" threshold check exist in the codebase and can disagree at the boundary, exactly the situation the external report flags as dangerous even when the individual cases look like harmless corner cases.

### Finding Description
`NakamotoBlockHeader::compute_voting_weight_threshold` is the consensus-critical function used by both the node (`verify_signer_signatures`) and signers (`store_and_process_block_signature`, `handle_block_pre_commit`, rejection tallying) to decide whether a block has enough signing weight: [1](#0-0) 
It computes `ceil(total_weight * threshold / 10)` — i.e. it rounds **up**, requiring strictly more weight than a naive floor division when the product isn't a multiple of 10.

`GlobalStateEvaluator::reached_agreement` and `reached_disagreement`, used for signer state-machine agreement (e.g. burn-view agreement time in `signerdb.rs`), instead perform the comparison inline using floor division: [2](#0-1) 
`reached_agreement` checks `vote_weight >= floor(total_weight * threshold / 10)`, which is a strictly weaker (easier to satisfy) bound than the ceiling-based `compute_voting_weight_threshold` whenever `total_weight * threshold` is not evenly divisible by 10. For example, with `total_weight = 13` and `threshold = 7` (70%): `13*7=91`, `91/10=9` (floor) vs `ceil(91/10)=10`. A vote weight of `9` satisfies `reached_agreement` but would be rejected as insufficient by `compute_voting_weight_threshold`.

This directly mirrors the IdleCDO pattern: the report explicitly calls out that "the use of two separate implementations of the same calculation suggest the potential for more undiscovered discrepancies" and that in one branch "precision loss ... favors" one side over another. Here, the discrepancy systematically favors reaching "agreement" one weight-unit earlier than the canonical block-approval threshold would allow.

`reached_agreement`/`reached_disagreement` are consumed by `signerdb.rs`'s `get_burn_block_received_time_from_signers`, which accumulates vote weight over signer state-machine updates and returns as soon as `eval.reached_agreement(vote_weight)` is true: [3](#0-2) 

### Impact Explanation
The consequence is a **liveness/consistency wedge on the global state machine**, not a direct forged block signature: a signer can be convinced that "global agreement" on a piece of state (e.g., the earliest burn view all signers have converged on) has been reached one weight-unit earlier than the block-header-verification code would treat as sufficient. Since global-state agreement gates whether a signer treats a burn view, tx-replay set, or highest-accepted-block as authoritative for its own subsequent voting/signing decisions, a signer could act on a "prematurely agreed" global state relative to what the on-chain `verify_signer_signatures` threshold demands, causing divergent behavior across the signer set at the boundary case. This falls under "a signer acting on a stale/incorrectly-thresholded reward set/threshold" (High-severity class per the given rubric) rather than Critical, since it does not by itself allow signing an invalid/non-canonical block — it is a threshold-consistency defect between two supposedly-equivalent "70%" checks.

### Likelihood Explanation
This triggers deterministically whenever `total_weight * NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD` is not a multiple of 10 (i.e., most reward-set weight totals), which is common given weights are derived from stacked-STX apportionment and not guaranteed to be round numbers. No majority collusion or privileged access is required — it is a latent arithmetic inconsistency reachable by ordinary state-machine-update gossip from any subset of signers whose combined weight lands in the floor/ceiling gap.

### Recommendation
Replace the inline floor-division comparisons in `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` with calls to the single canonical threshold function (`NakamotoBlockHeader::compute_voting_weight_threshold`, or a shared helper it delegates to), so there is exactly one implementation of the 70%/30% weight-threshold semantics used everywhere in the signer and node code, matching the IdleCDO remediation approach of consolidating to one shared calculation method.

### Proof of Concept
1. Construct a reward set with `total_weight = 13` and confirm `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD = 7` (70% expressed as a factor of 10) as referenced in `stackslib/src/core/mod.rs`.
2. Call `NakamotoBlockHeader::compute_voting_weight_threshold(13)`: `13*7=91`, `91 % 10 = 1 != 0` → `ceil = 1` → threshold `= 91/10 + 1 = 10`.
3. Call `GlobalStateEvaluator::reached_agreement(9)` with `total_weight = 13`: `9 >= (13*7)/10 = 9` → returns `true`.
4. Weight `9` is judged "sufficient for global agreement" by `reached_agreement` but is one unit below the `10` required by `compute_voting_weight_threshold`, demonstrating the two "same" 70% checks diverge at this boundary — analogous to the `virtualPrice()`/`_updatePrices()` divergence in the referenced report. [1](#0-0) [2](#0-1)

### Citations

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
