### Title
`check_parent_tenure_choice` treats a missing local `approved_time` as "0 seconds elapsed", always granting reorg permission regardless of actual tenure age - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`SortitionData::check_parent_tenure_choice` decides whether a miner is allowed to reorg a competing tenure by comparing the elapsed time between that tenure's first-block approval and the new sortition (`proposal_to_sortition`) against the configured tolerance window `first_proposal_burn_block_timing`. When the signer has no local `approved_time` for the reorged tenure's first block, the code substitutes `0` for the elapsed time instead of treating the case as "unknown"/conservatively rejecting it. Since `Duration::from_secs(0) < first_proposal_burn_block_timing` is true for any nonzero timing window (default 60s), the missing-data branch unconditionally satisfies the "poorly timed, reorg allowed" condition — mirroring the reported bug class where an overly permissive tolerance window converts a should-be-rejected state into an always-accepted one.

### Finding Description
The relevant logic lives in `check_parent_tenure_choice`: [1](#0-0) 

For each reorged tenure with more than zero but at most one globally-accepted block, the signer fetches `local_block_info` via `get_first_approved_block_in_tenure` and computes `proposal_to_sortition` as the gap between that block's local approval and the new sortition's receive time:

```
let proposal_to_sortition = if let Some(approved_at) = local_block_info.approved_time {
    sortition_state_received_time.saturating_sub(approved_at)
} else {
    info!("We did not sign over the reorged tenure's first block, considering it as a late-arriving proposal");
    0
};
if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
    // ... reorg permitted, tenure marked superseded
}
``` [2](#0-1) 

`approved_time` is only stamped when *this signer itself* pre-commits or locally accepts a block: [3](#0-2) 

A signer can legitimately lack `approved_time` for a tenure's first block even though the block is a real, well-established, globally-accepted block observed on the node/StackerDB — e.g. a signer that restarted, was late in receiving the original proposal, or otherwise never locally processed that specific proposal, but still has a `BlockInfo` row for it (via `get_first_approved_block_in_tenure`) with `approved_time = None`. In that situation the code does not treat this as "unknown, be conservative" — it collapses the unknown gap to `0`, which is always less than any positive `first_proposal_burn_block_timing`, so the "poorly timed" (reorg-allowed) branch is taken unconditionally, and the tenure is marked superseded (`superseded_tenures.push(tenure)` / `record_superseded_tenure`).

This breaks the "approved-parent vs canonical" tenure-choice equality this function exists to enforce: the intent of `first_proposal_burn_block_timing` is to distinguish a tenure that had *no real chance* to be RBF'd (short window, permit reorg) from one that was *established long enough* that a reorg should be rejected. Substituting `0` for missing local knowledge always classifies the tenure as "no real chance", even when it may have been live and confirmed for a long time from the network's perspective.

### Impact Explanation
When this branch fires, `check_parent_tenure_choice` returns `Ok(true)` (valid parent tenure choice), which is consumed directly by `validate_tenure_change_payload`/`check_proposal` in both v1 and v2 chainstate paths as a hard requirement for accepting a tenure-change block: [4](#0-3) [5](#0-4) 

A signer in this state will sign a tenure-change block that reorgs a tenure it should have rejected, i.e., it produces a signature over a non-canonical/conflicting block — the Critical impact category (a signer signing an invalid/non-canonical/conflicting block). This is reachable by a single one-slot miner racing a sortition against an established tenure; it requires no majority of signers or extra keys — only that the affected signer's local `BlockInfo` for that tenure lacks `approved_time` (e.g., following a signer restart, a missed early proposal, or the block being learned about only via later, non-approval channels).

### Likelihood Explanation
The precondition (a `BlockInfo` present via `get_first_approved_block_in_tenure` but with `approved_time == None`) is explicitly anticipated and handled by the code (`info!("We did not sign over the reorged tenure's first block ...")`), indicating it is a real, reachable state rather than a purely theoretical one — e.g., any signer that was offline, restarted, or otherwise never itself pre-committed/locally-accepted the tenure's first block while still learning of it. A miner attempting to win a sortition and reorg a recently-confirmed competitor's tenure needs only for the burn-block timing gate to pass on enough signers; this bug removes that gate entirely for signers with no local approval record for that specific block, regardless of how long the tenure was actually alive.

### Recommendation
Do not default to `0` when `approved_time` is unknown. Either:
- Query the node for actual timing/height information (similar to how `get_tenure_tip` is used elsewhere) before deciding, or
- Treat "no local approval time" as reorg-not-permitted by default (fail closed), since the purpose of the timing check is specifically to protect established tenures from reorg — an unknown timing gap should not be interpreted as the shortest possible gap.

### Proof of Concept
1. Signer S restarts (or otherwise never received/locally-approved the original proposal) after tenure T's single block was globally accepted by the rest of the network; S's DB retains a `BlockInfo` for T's first block (learned via a later channel) with `approved_time = None`.
2. A malicious/racing miner wins the very next sortition and proposes a tenure-change block whose `prev_tenure_consensus_hash` reorgs tenure T (T has exactly one globally accepted block, satisfying `globally_accepted_blocks <= 1`).
3. `check_parent_tenure_choice` on signer S calls `get_first_approved_block_in_tenure(T)`, gets `local_block_info.approved_time == None`, hits the `else` branch, sets `proposal_to_sortition = 0`.
4. `Duration::from_secs(0) < first_proposal_burn_block_timing` (default 60s) is true, so S treats T as "poorly timed" and permits the reorg — signing (via `validate_tenure_change_payload` → `check_proposal` → pre-commit/signature flow) a tenure-change block that reorgs an already-established tenure, even though the real elapsed time (as seen by the rest of the network with proper `approved_time`) may have been well beyond the configured tolerance.

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

**File:** stacks-signer/src/signerdb.rs (L272-289)
```rust
    /// Mark this block as valid, record the approved time timestamp if not already set and attempt to mark it as pre-committed.
    pub fn mark_pre_committed(&mut self) -> Result<(), String> {
        self.valid = Some(true);
        self.approved_time.get_or_insert(get_epoch_time_secs());
        self.move_to(BlockState::PreCommitted)
    }

    /// Mark this block as valid and the appropriate timestamps if they aren't already set, and attempt to mark it as locally accepted.
    pub fn mark_locally_accepted(&mut self, group_signed: bool) -> Result<(), String> {
        if group_signed {
            self.signed_group.get_or_insert(get_epoch_time_secs());
        } else {
            self.valid = Some(true);
            self.approved_time.get_or_insert(get_epoch_time_secs());
            self.signed_self.get_or_insert(get_epoch_time_secs());
        }
        self.move_to(BlockState::LocallyAccepted)
    }
```

**File:** stacks-signer/src/chainstate/v1.rs (L496-504)
```rust
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
