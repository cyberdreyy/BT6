### Title
`GlobalStateEvaluator` uses floor-rounded 70% threshold while canonical on-chain signature threshold is ceiling-rounded, causing signer/chain threshold divergence - (File: `libsigner/src/v0/signer_state.rs`, `stackslib/src/chainstate/nakamoto/mod.rs`)

### Summary
The report's root cause is an unstated equivalence assumption between two formulas that are only equal under a specific numeric condition (`decimals == 8`), and diverge silently otherwise. The same bug class exists between the signer-side supermajority check `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` and the canonical, on-chain signature-weight threshold `NakamotoBlockHeader::compute_voting_weight_threshold`. Both are meant to express "70% of total signer weight," but one rounds down and the other rounds up, so for `total_weight` values where `total_weight * 7` is not a multiple of 10, the two thresholds differ by exactly one weight unit.

### Finding Description
The signer-side threshold, used by `GlobalStateEvaluator` (which backs `determine_global_state`, `determine_latest_supported_signer_protocol_version`, and `get_burn_block_received_time_from_signers`), computes: [1](#0-0) 

```rust
pub fn reached_agreement(&self, vote_weight: u32) -> bool {
    u64::from(vote_weight)
        >= u64::from(self.total_weight).strict_mul(NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
            / 10
}

pub fn reached_disagreement(&self, vote_weight: u32) -> bool {
    u64::from(vote_weight)
        > u64::from(self.total_weight).strict_mul(10 - NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
            / 10
}
```

This is a plain integer-floor division of `total_weight * 7 / 10`, with no ceiling adjustment.

The canonical, chain-consensus-defining threshold used by `NakamotoBlockHeader::verify_signer_signatures` (the function that actually decides whether a block's collected signer signatures are sufficient for the block to be valid) is: [2](#0-1) 

```rust
pub fn compute_voting_weight_threshold(total_weight: u32) -> Result<u32, ChainstateError> {
    let threshold = NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD;
    let total_weight = u64::from(total_weight);
    let ceil = if (total_weight * threshold) % 10 == 0 {
        0
    } else {
        1
    };
    u32::try_from((total_weight * threshold) / 10 + ceil)...
}
```

This explicitly adds a ceiling correction (`+ ceil`), and is fed directly into `verify_signer_signatures`: [3](#0-2) 

For any `total_weight` where `total_weight * 7` is not exactly divisible by 10 (e.g. `total_weight = 13` ⇒ `13*7/10 = 9.1`), the two formulas disagree by exactly one unit of weight: the on-chain threshold is `10`, but `GlobalStateEvaluator::reached_agreement` treats `vote_weight = 9` as already "agreed" (`9 >= 9`).

`GlobalStateEvaluator` (via `determine_global_state`) is what backs the signer's `check_block_against_global_state` path in `stacks-signer/src/v0/signer.rs`, which decides whether the local signer treats the group's declared miner state (including `parent_tenure_last_block`, described in the docs as "the equality key for global agreement") as having reached supermajority: [4](#0-3) 

Because the signer-side "has the group agreed" check (`reached_agreement`) and the chain-side "were there enough signatures" check (`compute_voting_weight_threshold`) are supposed to encode the identical concept — "≥70% of signer weight" — but are implemented with different rounding, they are not actually equivalent. This mirrors the Chainlink bug exactly: two formulas assumed interchangeable (`decimals == 8` there, "same 70% threshold" here) that silently diverge for specific numeric inputs.

### Impact Explanation
This falls under the allowed High-impact category: "a signer... acting on a stale reward set/threshold." Because `total_weight` is derived from the actual reward-set weight distribution (a value an adversarial or even benign reward-cycle composition can naturally produce — any `total_weight` not a multiple of 10, e.g. 13, 16, 19, 23, etc., every non-multiple-of-10 total weight triggers this gap), the signer's own notion of "the group reached 70% agreement" can fire one weight-unit earlier than the canonical on-chain notion of "the block collected a valid supermajority of signatures." This produces divergent behavior across the honest signer set:
- Some signers, using `reached_agreement`, will treat the miner-view/global-state as settled and act on it (adopt a miner view, stop retrying, or accept a state-machine update) one unit of weight before the canonical chain-level threshold would be satisfied.
- The actual chain-level acceptance path (`verify_signer_signatures`/`compute_voting_weight_threshold`) requires strictly more weight, so a block that a signer's local logic considers "backed by consensus" can still be rejected at the node level, or vice versa signers can disagree amongst themselves about whether agreement was reached depending on which threshold function is consulted at which point in the flow.

This is a real, always-present rounding discrepancy (not requiring a compromised key or a majority of signers) that undermines the assumed equivalence between the signer state machine's internal supermajority gate and the protocol's actual supermajority requirement.

### Likelihood Explanation
High likelihood of occurrence: it triggers for the majority of possible `total_weight` values (any value where `total_weight * 7 % 10 != 0`), not just an edge case. No malicious action, majority collusion, or special crafting is required — it is a deterministic property of the reward-set weight total for a given cycle.

### Recommendation
Make `GlobalStateEvaluator::reached_agreement` (and, symmetrically, `reached_disagreement`) use the same ceiling-rounded formula as `NakamotoBlockHeader::compute_voting_weight_threshold`, or better, have both call into one shared, single-source-of-truth threshold function so the signer-side agreement gate and the chain-side signature-validity gate can never diverge.

### Proof of Concept
```
total_weight = 13
NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD = 7

// Canonical / on-chain (stackslib/src/chainstate/nakamoto/mod.rs::compute_voting_weight_threshold)
ceil(13 * 7 / 10) = ceil(9.1) = 10   // block needs weight >= 10 to be valid

// Signer-side (libsigner/src/v0/signer_state.rs::reached_agreement)
floor(13 * 7 / 10) = 9               // GlobalStateEvaluator treats weight >= 9 as "agreement reached"

vote_weight = 9:
  reached_agreement(9)  -> true   (9 >= 9)
  compute_voting_weight_threshold check -> false (9 < 10, block invalid on-chain)
```
A signer whose `GlobalStateEvaluator` observes `vote_weight = 9` out of `total_weight = 13` will consider the group state "agreed"/globally settled, while the same weight would be insufficient for `verify_signer_signatures` to accept a block on-chain — demonstrating the two "70% threshold" computations are not interchangeable.

### Citations

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
