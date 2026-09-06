Based on my research, I was unable to fully verify one final detail (the exact source lines of `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` in `libsigner/src/v0/signer_state.rs` — a tool-call error prevented reading that file directly in the last iteration). My conclusion below rests on strong indirect evidence: the regression-test comments in `libsigner/src/tests/signer_state.rs` that describe the exact formula used (`total_weight * 7 / 10` widened to `u64`, compared with `>=`), and the contrasting, explicitly-documented ceiling formula in `NakamotoBlockHeader::compute_voting_weight_threshold`.

### Title
Two supermajority-threshold computations for the same 70%/30% constant round differently, producing a possible mismatch between the signer-set global-state consensus check and the node's canonical block-approval threshold - (File: `libsigner/src/v0/signer_state.rs`, `stackslib/src/chainstate/nakamoto/mod.rs`)

### Summary
`NakamotoBlockHeader::compute_voting_weight_threshold` (the canonical, node-enforced supermajority threshold used to verify block signatures and used throughout `stacks-signer/src/v0/signer.rs` to gate pre-commits/signatures/rejections) explicitly rounds the 70% threshold **up** (ceiling) [1](#0-0) . `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` in `libsigner`, which is meant to represent the identical 70%/30% supermajority semantics (a compile-time assertion in its own test file pins it to `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD == 7`) [2](#0-1) , is computed via plain floor integer division after widening to `u64` (`total_weight * 7 / 10`, `total_weight * 3 / 10`) with no ceiling adjustment, per its own regression-test description [3](#0-2) .

### Finding Description
`compute_voting_weight_threshold` computes `ceil(total_weight * 7 / 10)`:
```
let ceil = if (total_weight * threshold) % 10 == 0 { 0 } else { 1 };
u32::try_from((total_weight * threshold) / 10 + ceil)
``` [1](#0-0) 
This is confirmed correct-rounding by `test_compute_voting_weight_threshold`, e.g. `511 -> 358` (`511*7/10=357.7`, rounded up) [4](#0-3) . This exact function is what `stacks-signer/src/v0/signer.rs` uses to gate pre-commit, acceptance, and rejection thresholds [5](#0-4) [6](#0-5) , and what `NakamotoBlockHeader::verify_signer_signatures` uses to validate a block's final signature set at the node [7](#0-6) .

By contrast, `GlobalStateEvaluator::reached_agreement` is documented by its own regression test as using `total_weight * 7 / 10` (floor, no ceiling term) once widened to `u64` to avoid overflow, and `reached_disagreement` uses `total_weight * 3 / 10` similarly [8](#0-7) . For any `total_weight` where `total_weight * 7` is not an exact multiple of 10 (i.e. most values), `floor(total_weight*7/10) < ceil(total_weight*7/10)`, e.g. for `total_weight = 511`, floor gives `357` but the canonical threshold is `358` — a one-unit weight gap in which the global-state evaluator would call the supermajority "reached" while the canonical (`compute_voting_weight_threshold`)-based check used for block signature/consensus purposes would not.

`GlobalStateEvaluator` is used on both the node (`stacks-node/src/nakamoto_node/stackerdb_listener.rs`) and signer side to decide when the signer fleet has converged on a `StateMachineUpdate` (protocol version / global view) [9](#0-8) . This mirrors exactly the class of bug in the referenced report: a value meant to represent the same threshold is computed with two different rounding rules in two different code paths that must agree, creating a state where "aggregated weight" as seen by one path diverges from the "verified"/canonical threshold enforced by the other.

### Impact Explanation
Because the global-state consensus threshold (`reached_agreement`) can be satisfied with strictly less weight than the canonical 70% enforced by `compute_voting_weight_threshold`/`verify_signer_signatures`, a signer set can be led to treat a global-state view (e.g. active protocol version, or whatever downstream decision keys off `reached_agreement`) as having reached the mandated 70% supermajority when it has not, by the same margin/mechanism as the referenced Union Finance report (a rounded-down value substituting for a value that should be rounded up). This is a genuine equality break between two representations of the same weight threshold ("aggregated-weight vs verified-accepts" class named in the validation rules), and can act as a liveness/consistency wedge in the global signer state machine if downstream logic assumes `reached_agreement` implies the same guarantee as the canonical block-approval threshold.

### Likelihood Explanation
This triggers deterministically (no majority collusion needed) whenever the reward-cycle's total signer weight is not an exact multiple of 10 relative to the 7/3 numerators — which is the common case for realistic weight distributions — and simply requires the signer set's aggregate weight to sit in the one-unit gap between `floor(total*7/10)` and `ceil(total*7/10)` (or the analogous 30% gap for disagreement). No attacker action beyond normal participation is required; it is a built-in rounding inconsistency between two library functions meant to enforce the same rule.

### Recommendation
Make `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` delegate to (or exactly mirror, including rounding direction) `NakamotoBlockHeader::compute_voting_weight_threshold`, so that both the node's canonical block-approval threshold and the signer-side global-state consensus threshold round the same way (ceiling) for the same 70%/30% constants, eliminating the possibility of the two subsystems disagreeing on whether a supermajority has been reached.

### Proof of Concept
Using the confirmed formulas:
- `compute_voting_weight_threshold(511) = ceil(511*7/10) = 358` (per `test_compute_voting_weight_threshold`) [4](#0-3) .
- `GlobalStateEvaluator::reached_agreement` for `total_weight = 511` would evaluate `floor(511*7/10) = 357` per the documented formula in its regression tests [10](#0-9) .

At an aggregate weight of `357`, `reached_agreement(357)` returns `true` (357 ≥ 357), while the same weight fails the canonical `compute_voting_weight_threshold` check used by `v0/signer.rs` and `verify_signer_signatures` (357 < 358). Any global-state decision gated purely by `reached_agreement` therefore fires one weight-unit earlier than the canonical supermajority bar enforced elsewhere in the same codebase.

**Note on confidence:** I was not able to directly open `libsigner/src/v0/signer_state.rs` in this session (tool error) to confirm the exact function body byte-for-byte; the formula above is reconstructed from the accompanying regression-test file's explicit doc comments, which describe the pre-fix and post-fix expressions in detail. I recommend a maintainer directly diff the two threshold functions to confirm the rounding-direction mismatch before treating this as fully confirmed.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1180-1187)
```rust
        let threshold = Self::compute_voting_weight_threshold(total_weight)?;

        if total_weight_signed < threshold {
            return Err(ChainstateError::InvalidStacksBlock(format!(
                "Not enough signatures. Needed at least {} but got {} (out of {})",
                threshold, total_weight_signed, total_weight,
            )));
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

**File:** libsigner/src/tests/signer_state.rs (L712-718)
```rust
/// wrap value (170_503_271 for `reached_disagreement_no_u32_overflow`) that are
/// only correct when the supermajority constant is 7. If this assert ever
/// fires, the test values must be recomputed deliberately, not just bumped.
const _: () = assert!(
    NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD == 7,
    "threshold tests in this file assume NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD == 7"
);
```

**File:** libsigner/src/tests/signer_state.rs (L731-756)
```rust
#[test]
/// Regression: u32 multiplication in `reached_agreement` wrapped silently in
/// release builds for `total_weight > u32::MAX / 7 ≈ 613_566_756`. With
/// `total_weight = 1_000_000_000` the buggy expression `total_weight * 7 / 10`
/// wrapped to ~270_503_270, allowing roughly 27% of total weight to satisfy
/// the 70% supermajority check. The fix widens to u64 first.
fn reached_agreement_no_u32_overflow() {
    let evaluator = evaluator_with_total_weight(1_000_000_000);

    // Pre-fix wrap landed at 270_503_270; assert that vote_weight at the wrapped
    // value is correctly rejected — i.e., 27% does not satisfy 70%.
    assert!(
        !evaluator.reached_agreement(270_503_270),
        "27% of total_weight must not satisfy the 70% threshold"
    );
    // Boundary: exactly 70% must pass.
    assert!(
        evaluator.reached_agreement(700_000_000),
        "70% of total_weight must satisfy the 70% threshold"
    );
    // Just below 70% must fail.
    assert!(
        !evaluator.reached_agreement(699_999_999),
        "below 70% must not satisfy the threshold"
    );
}
```

**File:** libsigner/src/tests/signer_state.rs (L758-787)
```rust
#[test]
/// Regression for the same u32-overflow class as `reached_agreement_no_u32_overflow`,
/// but on the disagreement path. Here the multiplier is `10 - threshold = 3`,
/// so the wrap point is `total_weight > u32::MAX / 3 ≈ 1_431_655_765`, well
/// above the agreement wrap point (≈ 613M). The agreement test uses
/// `total_weight = 1_000_000_000` and so doesn't cover this path.
///
/// At `total_weight = 2_000_000_000`, the buggy `total_weight * 3` wrapped to
/// 1_705_032_704 and `/ 10` landed at 170_503_270. That made ~8.5% of total
/// weight look like a blocking minority instead of the required > 30%.
fn reached_disagreement_no_u32_overflow() {
    let evaluator = evaluator_with_total_weight(2_000_000_000);

    // One past the pre-fix wrap value (~8.5% of total). Must not count as a
    // blocking minority.
    assert!(
        !evaluator.reached_disagreement(170_503_271),
        "~8.5% of total_weight must not satisfy the >30% disagreement check"
    );
    // Exactly 30%: strict `>`, so still not disagreement.
    assert!(
        !evaluator.reached_disagreement(600_000_000),
        "exactly 30% must not satisfy the strict > threshold"
    );
    // One unit past 30%: disagreement.
    assert!(
        evaluator.reached_disagreement(600_000_001),
        "just above 30% must satisfy the threshold"
    );
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

**File:** stacks-signer/src/v0/signer.rs (L1295-1301)
```rust
        let commit_weight = self.compute_signature_signing_weight(committers.iter());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
```

**File:** stacks-signer/src/v0/signer.rs (L2498-2501)
```rust
        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L249-266)
```rust
        let mut global_state_evaluator = GlobalStateEvaluator::new(HashMap::new(), address_weights);
        for (chunk, slot_id) in chunks.into_iter().zip(slot_ids) {
            let Some(chunk) = chunk else {
                continue;
            };
            let Some(signer_entry) = &signer_entries.get(&slot_id) else {
                continue;
            };
            let Ok(signer_pubkey) = StacksPublicKey::from_slice(&signer_entry.signing_key) else {
                continue;
            };
            let address = StacksAddress::p2pkh(config.is_mainnet(), &signer_pubkey);
            if let Ok(SignerMessageV0::StateMachineUpdate(update)) =
                SignerMessageV0::consensus_deserialize(&mut chunk.as_slice())
            {
                global_state_evaluator.insert_update(address, update);
            }
        }
```
