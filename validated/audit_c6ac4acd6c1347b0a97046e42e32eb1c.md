### Title
Missing local approval timestamp is treated as proof of a "late" proposal, letting a miner obtain unwarranted reorg permission - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
`SortitionData::check_parent_tenure_choice` decides whether a miner is allowed to reorg away a prior tenure's block. The rule is supposed to permit a reorg only when the reorged tenure's first block was proposed too close to the burn-block transition to have been safely accepted (`first_proposal_burn_block_timing`). When the local signer has no recorded `approved_time` for that block, the code silently substitutes `0` for the elapsed time instead of treating the situation as "unknown," which unconditionally satisfies the "poorly timed" branch and grants the reorg permit regardless of the block's real timing.

### Finding Description
In `check_parent_tenure_choice`, for every tenure being reorged away, the code fetches `local_block_info` via `signer_db.get_first_approved_block_in_tenure` and then computes how much time elapsed between that block's local approval and the new sortition: [1](#0-0) 

If `local_block_info.approved_time` is `None` (the signer never pre-committed nor locally accepted that block), the code does not fall back to rejecting the reorg (as it does a few lines above when there is *no* local block record at all — `get_first_approved_block_in_tenure` returning `None` causes an explicit `return Ok(false)`): [2](#0-1) 

Instead, when the block record exists but its `approved_time` is unset, the elapsed time is hard-coded to `0`:

```
} else {
    info!("We did not sign over the reorged tenure's first block, considering it as a late-arriving proposal");
    0
};
if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
    ... superseded_tenures.push(tenure); continue;
}
```

Since `Duration::from_secs(0)` is always less than any positive `first_proposal_burn_block_timing`, this branch is always taken whenever `approved_time` is `None` — i.e., the tenure is always treated as "poorly timed" and the reorg is always sanctioned, irrespective of how much real wall-clock time actually separated the block's proposal from the new sortition.

The `docs/signer-flows.md` narrative for section 8 confirms that `approved_time` is only stamped at pre-commit or local acceptance: [3](#0-2) 

so any circumstance in which a *particular* signer failed to pre-commit/accept that block in time — a validation stall, a network delay, selective/late broadcast by the miner, or simple bad luck — produces exactly the same `None` value as a genuinely too-late proposal. The check conflates "we have no evidence this arrived late" with "we have proved it arrived late," which are not equivalent, and defaults to the unsafe (permissive) outcome instead of the safe one already used for the fully-missing-record case.

### Impact Explanation
A single miner can exploit per-signer differences in local visibility (rather than the objective, global timing the rule is meant to enforce) to obtain reorg permission from signers who happen to lack an `approved_time` for the tenure being reorged, even when that tenure's block was in fact accepted well within the timing window by other signers/the network. Any signer that takes this path proceeds to treat the new, reorging tenure choice as valid — i.e., it will pre-commit/sign a tenure-change block that discards a legitimate prior tenure — deviating from what an honest evaluation of the real timing would have produced. This directly undermines the intended equality that "a reorg is permitted only for provably late tenures," moving the decision away from consensus-visible facts toward attacker-influenceable local gaps in visibility, and can result in a signer contributing its signature toward reorging away a canonical/legitimate tenure block — a conflicting/non-canonical signing outcome.

### Likelihood Explanation
Reachable with only a single miner controlling proposal timing/propagation (no majority of signers, no other signer's key, and no local access needed): the miner simply needs some signers to lack a pre-commit/acceptance record for the previous tenure's first block before it builds the reorging tenure. This can arise from ordinary network jitter or from a miner deliberately delaying/withholding the proposal from a subset of signers, both of which are within a single miner's control.

### Recommendation
When `local_block_info.approved_time` is `None`, do not assume the proposal arrived late. Instead, fall back to a stacks-node lookup of the block's actual proposal/arrival time (or another authoritative, node-verifiable timestamp), or — mirroring the safe default used when no local block record exists at all — reject the reorg (`return Ok(false)`) rather than defaulting `proposal_to_sortition` to `0`.

### Proof of Concept
1. Miner M produces the first (and only) block B of tenure T well within `first_proposal_burn_block_timing` of the sortition; most signers pre-commit/accept B in time (their `approved_time` is set), so from the network's perspective T is a legitimate, non-late tenure.
2. M ensures signer S specifically never receives/validates B in time (e.g., stalls the proposal to S, or S is briefly delayed) so `get_first_approved_block_in_tenure` on S returns B's record but with `approved_time == None`.
3. M immediately produces tenure T′ in the next sortition, building on the prior sortition instead of T (a reorg of T).
4. When S runs `check_parent_tenure_choice` for T′, `globally_accepted_blocks` for T is ≤ 1 (only B), and `local_block_info.approved_time` is `None`, so `proposal_to_sortition` is hard-coded to `0`, which is `< first_proposal_burn_block_timing`; S marks T superseded and returns `Ok(true)`, sanctioning the reorg — even though T was not actually late from the network's point of view.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L234-245)
```rust
            let Some(local_block_info) =
                signer_db.get_first_approved_block_in_tenure(&tenure.consensus_hash)?
            else {
                warn!(
                    "Miner is not building off of most recent tenure, but a tenure they attempted to reorg has already mined blocks, and there is no local knowledge for that tenure's block timing.";
                    "parent_tenure" => %self.parent_tenure_id,
                    "last_sortition" => %self.prior_sortition,
                    "violating_tenure_id" => %tenure.consensus_hash,
                    "violating_tenure_first_block_id" => %first_block_mined,
                );
                return Ok(false);
            };
```

**File:** stacks-signer/src/chainstate/mod.rs (L247-278)
```rust
            let checked_proposal_timing = if let Some(sortition_state_received_time) =
                sortition_state_received_time
            {
                // how long was there between when the proposal was received and the next sortition started?
                let proposal_to_sortition = if let Some(approved_at) =
                    local_block_info.approved_time
                {
                    sortition_state_received_time.saturating_sub(approved_at)
                } else {
                    info!("We did not sign over the reorged tenure's first block, considering it as a late-arriving proposal");
                    0
                };
                if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
                    info!(
                        "Miner is not building off of most recent tenure. A tenure they reorg has already mined blocks, but the block was poorly timed, allowing the reorg.";
                        "parent_tenure" => %self.parent_tenure_id,
                        "last_sortition" => %self.prior_sortition,
                        "violating_tenure_id" => %tenure.consensus_hash,
                        "violating_tenure_first_block_id" => %first_block_mined,
                        "violating_tenure_proposed_time" => local_block_info.proposed_time,
                        "new_tenure_received_time" => sortition_state_received_time,
                        "new_tenure_burn_timestamp" => self.burn_header_timestamp,
                        "first_proposal_burn_block_timing_secs" => first_proposal_burn_block_timing.as_secs(),
                        "proposal_to_sortition" => proposal_to_sortition,
                    );
                    superseded_tenures.push(tenure);
                    continue;
                }
                true
            } else {
                false
            };
```

**File:** docs/signer-flows.md (L156-158)
```markdown
Timestamps: `approved_time` is stamped at pre-commit _or_ local acceptance
(first wins), `signed_self` only when we sign, `signed_group` when the group
threshold is observed.
```
