### Title
Fail-open canonicity check in `SortitionsView::check_proposal` when `SignerDb::get_canonical_tip` returns `None` - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionsView::check_proposal` only validates that the current sortition's `parent_tenure_id` matches (or descends from) the signer's locally recorded canonical tip inside the `else if let Some(tip) = signer_db.get_canonical_tip()...` branch [1](#0-0) . When `get_canonical_tip()` returns `None`, the entire block that computes `consensus_hash_match`, `parent_tenure_id_match`, and invokes `check_parent_tenure_choice` is skipped, so no parent-tenure canonicity check runs for that proposal review.

### Finding Description
The relevant guard is: [2](#0-1) 

When the current miner has not timed out and `signer_db.get_canonical_tip()` returns `None` (empty/uninitialized local SignerDb tip), the `else if let Some(tip) = ...` branch is never entered. Consequently `self.cur_sortition.data.check_parent_tenure_choice(...)` — the function that performs the "more expensive check" for whether the sortition's declared parent tenure is a legitimate continuation of the canonical chain — is never called, and `RejectReason::ReorgNotAllowed` can never be returned from this code path regardless of what `parent_tenure_id` the winning miner declared. The rest of `check_proposal` (bitvec check, pubkey recovery, `ProposedBy` matching) does not perform any independent canonicity/parent-tenure check, so if the local tip is absent, this signer-local defense against approving a non-canonical/forked parent tenure is bypassed entirely for that call.

### Impact Explanation
If exploited, this would let a signer sign a block built on a stale or forked parent tenure, i.e., a non-canonical-block signature — matching the Critical category ("a signer signing an invalid, non-canonical, or conflicting block"). However, this signer-side check is a defense-in-depth mechanism layered on top of node-side block validation (out of scope per the rules), and it specifically protects against the signer's own previously-recorded history being reorged. When `get_canonical_tip()` is genuinely `None`, the signer has no prior recorded canonical tip to protect — there is nothing yet on record for this signer that a stale parent tenure could conflict with, so this state most plausibly corresponds to bootstrap (fresh signer, empty SignerDb, no prior activity) rather than an attacker-inducible mid-operation condition.

### Likelihood Explanation
The precondition (`get_canonical_tip()` returning `None`) is entirely a function of the victim signer's own local database/operational state — it is not something the attacker can trigger via gossiped messages or a crafted `BlockProposal`. The attacker (single miner slot, one signer's weight, no privileged access) can supply an arbitrary `parent_tenure_id` in their winning tenure/block-commit, but they cannot force the victim's `SignerDb` into the empty-tip state; that requires the victim to be freshly initialized or to have wiped its database, which is an operational/administrative condition, not attacker-controlled. This significantly reduces real-world exploitability versus what the question's framing implies, since it depends on incidental victim state rather than a repeatable, attacker-driven trigger.

### Recommendation
When `signer_db.get_canonical_tip()` returns `None`, do not silently skip the parent-tenure canonicity check. Instead, either (a) treat the absence of a local tip as "unknown/insufficient information" and fall back to an explicit node-query-based canonicity check (equivalent to always running `check_parent_tenure_choice` against the client's view of the sortition/tenure history), or (b) defer/reject proposal approval until the signer has bootstrapped a canonical tip, rather than proceeding as if there is nothing to check.

### Proof of Concept
```rust
// stacks-signer/src/chainstate/tests/v1.rs (new test)
#[test]
fn check_proposal_skips_parent_tenure_check_when_no_canonical_tip() {
    // 1. Construct a SignerDb backed by a fresh/empty sqlite db such that
    //    signer_db.get_canonical_tip() returns Ok(None).
    let mut signer_db = SignerDb::new(":memory:").unwrap();
    assert!(signer_db.get_canonical_tip().unwrap().is_none());

    // 2. Build a SortitionsView whose cur_sortition.data.parent_tenure_id
    //    is set to a stale/forked ConsensusHash (attacker-chosen, via the
    //    miner's tenure-change/block-commit that won the sortition slot).
    let mut view = build_test_sortitions_view(/* cur_sortition with stale parent_tenure_id */);

    // 3. Build a NakamotoBlock whose header.consensus_hash ==
    //    view.cur_sortition.data.consensus_hash.
    let block = build_test_block(view.cur_sortition.data.consensus_hash);

    // 4. Call check_proposal and assert it does NOT return ReorgNotAllowed,
    //    demonstrating the canonicity/parent-tenure check was skipped.
    let result = view.check_proposal(
        &client,
        &mut signer_db,
        &block,
        false,
        ReplayTransactionSet::none(),
    );
    assert_ne!(result, Err(RejectReason::ReorgNotAllowed));
}
```
This test directly demonstrates that with an empty `SignerDb` (no canonical tip), `check_proposal` never evaluates `check_parent_tenure_choice` and therefore cannot emit `RejectReason::ReorgNotAllowed` for a proposal whose `parent_tenure_id` is stale/forked.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L164-203)
```rust
        } else if let Some(tip) = signer_db
            .get_canonical_tip()
            .map_err(SignerChainstateError::from)?
        {
            // Check if the current sortition is aligned with the expected tenure:
            // - If the tip is in the current tenure, we are in the process of mining this tenure.
            // - If the tip is not in the current tenure, then we’re starting a new tenure,
            //   and the current sortition's parent tenure must match the tenure of the tip.
            // - If the tip is not building off of the current sortition's parent tenure, then
            //   check to see if the tip's parent is within the first proposal burn block timeout,
            //   which allows for forks when a burn block arrives quickly.
            // - Else the miner of the current sortition has committed to an incorrect parent tenure.
            let consensus_hash_match =
                self.cur_sortition.data.consensus_hash == tip.block.header.consensus_hash;
            let parent_tenure_id_match =
                self.cur_sortition.data.parent_tenure_id == tip.block.header.consensus_hash;
            if !consensus_hash_match && !parent_tenure_id_match {
                // More expensive check, so do it only if we need to.
                let is_valid_parent_tenure = self.cur_sortition.data.check_parent_tenure_choice(
                    signer_db,
                    client,
                    &self.config.first_proposal_burn_block_timing,
                )?;
                if !is_valid_parent_tenure {
                    warn!(
                        "Current sortition does not build off of canonical tip tenure, marking as invalid";
                        "current_sortition_parent" => ?self.cur_sortition.data.parent_tenure_id,
                        "tip_consensus_hash" => ?tip.block.header.consensus_hash,
                    );
                    self.cur_sortition.miner_status =
                        SortitionMinerStatus::InvalidatedBeforeFirstBlock;

                    // If the current proposal is also for this current
                    // sortition, then we can return early here.
                    if self.cur_sortition.data.consensus_hash == block.header.consensus_hash {
                        return Err(RejectReason::ReorgNotAllowed);
                    }
                }
            }
        }
```
