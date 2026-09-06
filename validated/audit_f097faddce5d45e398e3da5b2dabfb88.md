### Title
Reorg-timing bypass via unset `approved_time` treats never-signed tenures as always "poorly timed" - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`SortitionData::check_parent_tenure_choice` decides whether a miner is allowed to reorg a prior tenure that already produced a block. The rule is supposed to be: a reorg is permitted only if the reorged tenure's first block was proposed *close enough* to the next sortition (within `first_proposal_burn_block_timing`). The elapsed time is computed as `sortition_state_received_time.saturating_sub(approved_at)`. When this signer has no `approved_time` recorded for that block, the code substitutes `0` for the elapsed time instead of failing closed, which makes the "was it late?" check pass unconditionally — regardless of how much real wall-clock time actually separated the block's proposal from the sortition.

### Finding Description
The check lives in `SortitionData::check_parent_tenure_choice`: [1](#0-0) 

```
let checked_proposal_timing = if let Some(sortition_state_received_time) =
    sortition_state_received_time
{
    let proposal_to_sortition = if let Some(approved_at) =
        local_block_info.approved_time
    {
        sortition_state_received_time.saturating_sub(approved_at)
    } else {
        info!("We did not sign over the reorged tenure's first block, considering it as a late-arriving proposal");
        0
    };
    if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
        ...
        superseded_tenures.push(tenure);
        continue;
    }
    true
} else {
    false
};
```

This mirrors the analog bug class: the report describes two lockers — one locking for the full week, one locking for a few seconds right at the boundary — both getting identical treatment (`tokenAmount*1`) because the contract truncates/collapses actual elapsed duration into a coarse bucket. Here, the signer collapses "unknown/no local timing data" into the *most favorable* bucket for the reorg (`proposal_to_sortition = 0`), which is always `< first_proposal_burn_block_timing`. A tenure this signer never signed is *always* treated as if it had been proposed in the last instant before the sortition — even if, in objective reality (as seen by other signers or the node), it was proposed a long time before the sortition and should fail the "poorly timed" test and block the reorg.

Practically: `local_block_info` is only non-`None` because `get_first_approved_block_in_tenure` found a record for that tenure (i.e., the signer at least locally knows of an approved block there), but `approved_time` can still be `None` for that record (e.g., a block that reached a different lifecycle state where this particular timestamp field was never populated for this signer, or a record inserted through a path that doesn't set it). In that situation, the code doesn't fail closed (reject the reorg / treat as long-elapsed) — it fails *open*, always granting the "poorly timed" allowance.

Once `check_parent_tenure_choice` returns `true`, `validate_tenure_change_payload` (both v1 and v2) accepts the tenure-change block and the signer signs a tenure-change block over a competing tenure that should have been protected by the timing gate: [2](#0-1) [3](#0-2) 

The reorg is also then recorded as sanctioned (`mark_tenure_superseded`), permanently removing this signer's own prior signature as a future conflict guard for that tenure: [4](#0-3) 

### Impact Explanation
This breaks the "reorg is only allowed when it is objectively poorly timed" equality the protocol relies on to prevent a single miner from reorging established tenures at will. A signer can end up signing a tenure-change block that reorgs a tenure that was, in objective/consensus-relevant time, established well outside the `first_proposal_burn_block_timing` window — i.e., signing a non-canonical/invalid reorg it should have rejected. This matches the "signer signing an invalid/non-canonical/conflicting block" Critical impact category, since it corrupts the reorg-permission invariant that guards against a single one-slot miner unilaterally discarding a competitor's already-established tenure.

### Likelihood Explanation
The trigger condition (a `local_block_info` record existing but its `approved_time` field being unset) requires a specific state combination in this signer's own `SignerDb` that is reachable purely through normal validation code paths (this signer's own record-keeping for a tenure it observed but did not itself sign/approve fully) — no majority collusion, no other signer's key, and no node-side changes are needed. However, exploiting it reliably requires the reorging miner (a single one-slot miner is sufficient per the bug-class scope) to arrange the specific scenario where the targeted signer has this record gap for the reorged tenure, which depends on internal signer bookkeeping timing rather than attacker-controlled input directly, making it a real but state-dependent likelihood rather than a trivially universal one.

### Recommendation
Fail closed instead of failing open when `approved_time` is missing: if the local record for the reorged tenure's first block has no `approved_time`, do not substitute `0` (the most permissive value). Either (a) reject the reorg by treating the tenure as violating the timing rule by default, or (b) fall back to an independently-verifiable, non-forgeable timestamp (e.g., the tenure's node-reported/burn-block-derived proposal time) rather than defaulting to "just now." At minimum, this path should log at `warn!` (not `info!`) since it silently disables the anti-reorg timing protection for that tenure.

### Proof of Concept
Conceptual reproduction, following the existing test harness in `stacks-signer/src/chainstate/tests/v1.rs`/`v2.rs` (`reorg_timing_testing`):
1. Set up a tenure `T` that produced exactly one first block, long before the next sortition (e.g., `sortition_timing_secs` far greater than `first_proposal_burn_block_timing_secs`, which in `check_proposal_reorg_timing_bad` correctly causes rejection today).
2. Insert the local `BlockInfo` for `T`'s first block via a path that leaves `approved_time` unset (e.g., a locally-known/approved record populated without going through the normal `mark_locally_accepted`/timestamp-setting flow), while still causing `get_first_approved_block_in_tenure` to return `Some(local_block_info)`.
3. Call `check_parent_tenure_choice` for a new sortition whose `parent_tenure_id` reorgs `T`.
4. Observe that despite the large real elapsed time, `proposal_to_sortition` is computed as `0`, `Duration::from_secs(0) < first_proposal_burn_block_timing` is always true, and the function returns `Ok(true)` (reorg permitted) and marks `T` as superseded — contradicting the intended timing guard that the existing `check_parent_tenure_choice_reorg_timing_bad` test (`stacks-signer/src/chainstate/tests/v2.rs:362-370`) demonstrates should reject this reorg.

Note: I could not fully verify from the index alone every code path that can leave `approved_time` unset for a record for which `get_first_approved_block_in_tenure` returns `Some` (the full `signerdb.rs` insertion/state-transition logic exceeds what I could inspect within the available tool calls). A Devin session with full repository access would be needed to enumerate all such paths precisely and confirm reachability end-to-end.

### Citations

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

**File:** stacks-signer/src/chainstate/v1.rs (L483-504)
```rust
        // Ensure that the tenure change block confirms the expected parent block
        let confirms_expected_parent = SortitionData::check_tenure_change_confirms_parent(
            tenure_change,
            block,
            signer_db,
            client,
            self.config.tenure_last_block_proposal_timeout,
            self.config.reorg_attempts_activity_timeout,
        )
        .map_err(SignerChainstateError::from)?;
        if !confirms_expected_parent {
            return Err(RejectReason::InvalidParentBlock);
        }
        // now, we have to check if the parent tenure was a valid choice.
        let is_valid_parent_tenure = proposed_by.state().data.check_parent_tenure_choice(
            signer_db,
            client,
            &self.config.first_proposal_burn_block_timing,
        )?;
        if !is_valid_parent_tenure {
            return Err(RejectReason::ReorgNotAllowed);
        }
```

**File:** stacks-signer/src/chainstate/v2.rs (L327-339)
```rust
        // Ensure that the tenure change block confirms the expected parent block
        let confirms_expected_parent = SortitionData::check_tenure_change_confirms_parent(
            tenure_change,
            block,
            signer_db,
            client,
            config.tenure_last_block_proposal_timeout,
            config.reorg_attempts_activity_timeout,
        )
        .map_err(SignerChainstateError::from)?;
        if !confirms_expected_parent {
            return Err(RejectReason::InvalidParentBlock);
        }
```
