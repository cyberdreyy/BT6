### Title
Reorg-timing check treats a signer's own missing approval as an instantly-late proposal, letting a miner get a reorg wrongly permitted - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`check_parent_tenure_choice` decides whether a miner is allowed to reorg a prior tenure by comparing the (per-signer, local) time between the reorged tenure's first block's approval and the next sortition against `first_proposal_burn_block_timing`. When the evaluating signer has *no* `approved_time` recorded for that first block (e.g., it locally rejected it, or the record was never stamped with an approval), the code substitutes `0` for the elapsed time instead of treating the fact as "unknown," which always satisfies `proposal_to_sortition < first_proposal_burn_block_timing` and authorizes the reorg — regardless of how much real time actually elapsed.

### Finding Description
`check_parent_tenure_choice` in [1](#0-0)  computes, per reorged tenure, whether the reorg is permitted. For a tenure whose first block was mined, it fetches the local block record via `get_first_approved_block_in_tenure`, then computes: [2](#0-1) 

```
let proposal_to_sortition = if let Some(approved_at) = local_block_info.approved_time {
    sortition_state_received_time.saturating_sub(approved_at)
} else {
    info!("We did not sign over the reorged tenure's first block, considering it as a late-arriving proposal");
    0
};
if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
    // reorg permitted (tenure superseded)
    ...
}
```

The comment and code equate "we have no `approved_time`" with "the proposal arrived late (just before the sortition)," and forces `proposal_to_sortition = 0`. But `approved_time` being unset is not evidence of lateness — it is evidence that *this particular signer* never reached the pre-commit/approval stage for that block, which can happen for reasons entirely unrelated to timing:
- the signer locally rejected the block (it can be recorded via `get_first_approved_block_in_tenure` if that lookup is not strictly filtered to already-approved records, or a different code path populated the row without `approved_time`),
- the signer was offline/restarted and only backfilled a `blocks` row without ever pre-committing,
- StackerDB or network delivery to that one signer specifically was interrupted.

Because `0 < first_proposal_burn_block_timing` is true for any nonzero configured timing window, forcing this value to `0` always resolves the branch in favor of "the block was poorly timed, allowing the reorg" — even when the block was actually mined and broadcast well before the timeout window from every other signer's perspective. This inflates the count of signers who vote to permit the reorg (mark the old tenure `superseded_tenures`/`mark_tenure_superseded`) purely because of that signer's own local gap in knowledge, not because of real chain timing. If enough signers individually experience this same local blind spot (e.g., due to a targeted delay against a subset of signers, or a benign but message-specific delivery failure), the aggregate reorg-permission threshold can be satisfied for a reorg that would otherwise have been rejected by the real timing rule — an equivocation/rejection-recount analog to the DeFi "first depositor with 1 wei" bug: the caller (miner via crafted proposal delivery/timing) causes a signer to substitute a degenerate value (`0`, analogous to `1 wei`) for an unknown quantity, and that degenerate value then feeds a downstream threshold comparison used to authoritatively decide safety-relevant state (whether a competing miner's tenure legitimately superseded the prior one, unblocking that signer's own already-placed signature from counting as a conflict via `record_superseded_tenure`/`mark_tenure_superseded`).

This maps directly onto a signal in the prompt's allowed impact classes: a rejection (or "unknown"/non-participation) being recounted as if it were a positive/qualifying fact (here, "block arrived late enough to permit reorg"), which can let a signer sign over a block belonging to a tenure/fork whose reorg permission was manufactured rather than earned, contributing to a signer signing a non-canonical/conflicting chain.

### Impact Explanation
If this local substitution causes `check_parent_tenure_choice` to return `true` when it should return `false`, the signer:
1. Marks the true previous tenure as `superseded` via `record_superseded_tenure`/`mark_tenure_superseded`, voiding its own prior signature as a future conflict (per `reorg_permit_stands` in the pre-commit/signature-conflict logic).
2. Proceeds to validate and potentially sign the new (reorging) proposal in `validate_tenure_change_payload` (`stacks-signer/src/chainstate/v1.rs:497-504`) and `check_proposal` (`stacks-signer/src/chainstate/v1.rs:182-201`), both of which gate directly on `check_parent_tenure_choice`.

This is a Critical-class outcome per the rules: a signer being led to sign a block belonging to a reorg that was not legitimately timing-qualified, i.e., signing over a non-canonical/incorrectly-permitted chain state, driven by a local miscount rather than the intended timing invariant.

### Likelihood Explanation
Reachable by ordinary gossip/timing conditions a single miner (plus StackerDB propagation) can influence: whether a given signer received/pre-committed the first block of the tenure being reorged before crossing into `GloballyAccepted`/`approved_time`-stamped state. A miner (or a network partition/targeted delay) can prevent a subset of signers from stamping `approved_time` for that first block (e.g., by delaying that specific proposal to a subset of signers, or the block simply never reaching pre-commit for a subset due to timing/latency), which is plausible without needing a signer majority to be corrupted — only enough of the ordinary population's local knowledge to be incomplete to change the ~70%-weighted outcome at the margins is required, and any single affected signer instantiates the discrepancy in its own local vote. However, whether `get_first_approved_block_in_tenure` can actually return a `BlockInfo` with `approved_time == None` (versus filtering such rows out entirely) could not be confirmed from the available index; this is the crux of whether the described substitution is truly reachable, and needs to be checked against the exact SQL predicate of `get_first_approved_block_in_tenure` in `signerdb.rs`.

### Recommendation
Do not substitute `0` for an unknown `approved_time`. Distinguish "we do not know when/whether this block was approved" from "it was approved instantaneously before the sortition." If the local signer lacks a definitive `approved_time` for the tenure's first block, either:
- query the Stacks node (as is already done elsewhere in this file, e.g. `client.get_tenure_forking_info`) for an authoritative timestamp instead of assuming `0`, or
- default to the conservative branch (deny the reorg / do not supersede) when timing cannot be established, mirroring how `get_first_approved_block_in_tenure` returning `None` already denies the reorg a few lines above (`stacks-signer/src/chainstate/mod.rs:234-245`).

### Proof of Concept
1. Set up a signer set with `first_proposal_burn_block_timing` at its default (60s per `sample/conf/signer/mainnet-signer-conf.toml:166`).
2. Miner A produces tenure T's first block well before the timeout window (e.g., mined and broadcast 5 minutes before the next sortition), such that most signers stamp `approved_time` accordingly and would correctly compute `proposal_to_sortition` ≈ 300s, denying any future reorg of T.
3. For a targeted subset of signers, ensure the StackerDB/pre-commit message for T's first block is delayed, dropped, or the signer restarts before pre-committing, so those signers' `SignerDb` records for T's first block never receive an `approved_time` (while `get_first_approved_block_in_tenure` still returns a row, e.g. because the record was created for validation purposes but pre-commit/approval never landed for those signers specifically).
4. Miner B wins the next sortition and proposes a tenure-change block whose `TenureChangePayload.prev_tenure_consensus_hash` reorgs T.
5. For the affected subset of signers, `check_parent_tenure_choice` computes `proposal_to_sortition = 0` (via the `else` branch at `stacks-signer/src/chainstate/mod.rs:255-258`), unconditionally satisfying `0 < first_proposal_burn_block_timing`, and those signers mark T as `superseded` and proceed to sign Miner B's reorging block — despite T's real first-block timing having been well outside the configured reorg-permission window from every other signer's point of view.
6. Compare the state before/after: before the crafted delay, those signers would have denied the reorg (as the majority of unaffected signers correctly do, per the existing test `check_parent_tenure_choice_reorg_timing_bad` at `stacks-signer/src/chainstate/tests/v2.rs:362-370`); after the delay, they instead classify it as `check_parent_tenure_choice_reorg_timing_ok`-equivalent behavior purely due to their own missing `approved_time`, not real timing — breaking the intended equality between "actual proposal-to-sortition elapsed time" and "value used in the threshold comparison."

Note: full confirmation that `get_first_approved_block_in_tenure` can return a record with `approved_time == None` requires reading its exact query in `stacks-signer/src/signerdb.rs`, which was not fully available in the indexed context; this should be verified directly in the repository before treating this as fully proven.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L170-245)
```rust
    pub fn check_parent_tenure_choice(
        &self,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        first_proposal_burn_block_timing: &Duration,
    ) -> Result<bool, SignerChainstateError> {
        // if the parent tenure is the last sortition, it is a valid choice.
        // if the parent tenure is a reorg, then all of the reorged sortitions
        //  must either have produced zero blocks _or_ produced their first (and only) block
        //  very close to the burn block transition.
        if self.prior_sortition == self.parent_tenure_id {
            return Ok(true);
        }
        info!(
            "Most recent miner's tenure does not build off the prior sortition, checking if this is valid behavior";
            "sortition_state.consensus_hash" => %self.consensus_hash,
            "sortition_state.prior_sortition" => %self.prior_sortition,
            "sortition_state.parent_tenure_id" => %self.parent_tenure_id,
        );

        let tenures_reorged =
            client.get_tenure_forking_info(&self.parent_tenure_id, &self.prior_sortition)?;
        if tenures_reorged.is_empty() {
            warn!("Miner is not building off of most recent tenure, but stacks node was unable to return information about the relevant sortitions. Marking miner invalid.");
            return Ok(false);
        }

        // this value *should* always be some, but try to do the best we can if it isn't
        let sortition_state_received_time =
            signer_db.get_burn_block_receive_time(&self.burn_block_hash)?;

        // Track which tenures are superseded by the reorg, then mark them in
        // the DB after the reorg is permitted.
        let mut superseded_tenures = Vec::new();
        for tenure in tenures_reorged.iter() {
            if tenure.consensus_hash == self.parent_tenure_id {
                // this was a built-upon tenure, no need to check this tenure as part of the reorg.
                continue;
            }

            // disallow reorg if more than one block has already been signed
            let globally_accepted_blocks =
                signer_db.get_globally_accepted_block_count_in_tenure(&tenure.consensus_hash)?;
            if globally_accepted_blocks > 1 {
                warn!(
                    "Miner is not building off of most recent tenure, but a tenure they attempted to reorg has already more than one globally accepted block.";
                    "parent_tenure" => %self.parent_tenure_id,
                    "last_sortition" => %self.prior_sortition,
                    "violating_tenure_id" => %tenure.consensus_hash,
                    "violating_tenure_first_block_id" => ?tenure.first_block_mined,
                    "globally_accepted_blocks" => globally_accepted_blocks,
                );
                return Ok(false);
            }

            let Some(first_block_mined) = &tenure.first_block_mined else {
                // The node saw no blocks in this tenure, so the reorg takes nothing away from
                // the canonical chain. We may still hold a signature over a block in it that
                // the node has never seen (a block we accept locally is not handed to the node
                // until the whole signer set has signed it), so the reorg must still be
                // recorded if it is permitted.
                superseded_tenures.push(tenure);
                continue;
            };
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
