### Title
Reorg-permit timing check silently flips "well-timed" into "poorly-timed" via `saturating_sub` underflow - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`SortitionData::check_parent_tenure_choice` decides whether a new miner is allowed to build off (i.e. reorg away) a prior tenure that produced a block, based on how much time elapsed between that block's local approval and the arrival of the reorging tenure's burn block. The elapsed time is computed with `saturating_sub`, which floors a negative delta to `0` instead of surfacing that the ordering was inverted. A `0` result is interpreted by the very next line as "extremely poor timing → permit the reorg," which is the opposite of what a negative/late `approved_at` actually means.

### Finding Description
In `check_parent_tenure_choice` [1](#0-0) :

```rust
let proposal_to_sortition = if let Some(approved_at) = local_block_info.approved_time {
    sortition_state_received_time.saturating_sub(approved_at)
} else { ... 0 };
if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
    // ... allow the reorg (mark tenure superseded)
}
```

`sortition_state_received_time` is when *this* signer recorded the new sortition's burn block; `approved_at` is when *this* signer locally approved/pre-committed the reorged tenure's first block. The intended semantics: if the prior tenure's block was approved only an instant before the new burn block arrived (a short, "poorly timed" window), the new miner is forgiven for not having seen it and is allowed to build over it.

The bug: if `approved_at` happens to be *greater* than `sortition_state_received_time` — i.e., the signer's local approval of the prior block lagged behind (arrived after) its own burn-block bookkeeping timestamp, which can happen purely from node validation latency, signer restart/catch-up, or asynchronous event ordering rather than any real "late proposal" — `saturating_sub` clamps the result to `0`. A `0` duration is always `< first_proposal_burn_block_timing`, so the code concludes the *opposite* of the truth: it treats a block that had no real timing problem as if it arrived essentially at the exact moment of the burn transition, and grants the reorg permit (`superseded_tenures.push(tenure)`), which subsequently causes the signer to treat a signature it already placed on that tenure's block as excluded from future conflict checks (`SignerDb::mark_tenure_superseded`, consumed by `get_signed_conflicts`/`reorg_permit_stands`).

This is structurally the same class of bug as `blockhash(block.number)` always returning `0`: a value about a relative ordering that "cannot" (or should not) come back as a trivial/default number is silently defaulted, and that default is then read as meaningful, positive evidence for a security-relevant branch (unlike a revert, this passes the "hash is not what's expected" style check and takes the permissive path).

### Impact Explanation
When the underflow occurs, the signer wrongly grants a reorg permit for a tenure whose first block was not actually poorly timed. This can lead a signer to place a *new* signature over a competing tenure-start block that conflicts with one it (and potentially the group) already signed, i.e. a signer participating in signing a conflicting/non-canonical block for a height that was already resolved — exactly the "signer signing a conflicting block" critical impact category, since `record_superseded_tenure`/`mark_tenure_superseded` is precisely the mechanism that suppresses the own-tenure and cross-tenure conflict guards in `get_signed_conflicts`/`reorg_permit_stands` (see `stacks-signer/src/signerdb.rs` `mark_tenure_superseded`, and `stacks-signer/src/v0/signer.rs` conflict-checking around lines 1345-1466).

### Likelihood Explanation
This does not require a majority of signers or any key material — it only requires that a single signer's local clock/processing order puts `approved_at` after `sortition_state_received_time` for the reorged tenure's block, which is plausible under normal node validation latency or signer restart/catch-up, and is more likely to be provoked by a miner who deliberately delays broadcasting its next tenure's block-commit reveal relative to how quickly the target signer's node processes the prior block. It is a logic/arithmetic defect in a documented, security-relevant code path (`check_parent_tenure_choice`), not a volumetric or majority-signer scenario.

### Recommendation
Do not use `saturating_sub` for this comparison. Explicitly detect the case where `approved_at >= sortition_state_received_time` and treat it as "not poorly timed" (i.e., do not permit the reorg on this basis), for example:
```rust
let proposal_to_sortition = sortition_state_received_time
    .checked_sub(approved_at)
    .unwrap_or(u64::MAX); // or explicitly reject the reorg in this branch
```
so that an inverted/underflowing ordering can never be silently read as "0 seconds, allow the reorg."

### Proof of Concept
1. Prior tenure T1 produces its first block B1; the signer receives/validates B1 late (e.g. node backlog) and records `approved_time = t2`.
2. The next Bitcoin block containing the new sortition arrives and is recorded by the signer at `sortition_state_received_time = t1`, where `t1 < t2` (burn-block bookkeeping timestamp precedes the delayed local approval of B1, even though B1 itself was known/broadcast well before the transition).
3. In `check_parent_tenure_choice`, `proposal_to_sortition = t1.saturating_sub(t2) = 0`.
4. `Duration::from_secs(0) < first_proposal_burn_block_timing` is `true`, so the loop pushes T1 into `superseded_tenures` and the reorg over B1 is permitted, even though B1 was not actually "poorly timed."
5. The signer subsequently signs the new tenure's conflicting block at/above B1's height, and the pre-existing signature over B1 is excluded from `get_signed_conflicts` checks via the superseded-tenure permit, producing two signer-endorsed, conflicting chains at the same height. [2](#0-1)

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L170-295)
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

            warn!(
                "Miner is not building off of most recent tenure, but a tenure they attempted to reorg has already mined blocks.";
                "parent_tenure" => %self.parent_tenure_id,
                "last_sortition" => %self.prior_sortition,
                "violating_tenure_id" => %tenure.consensus_hash,
                "violating_tenure_first_block_id" => %first_block_mined,
                "checked_proposal_timing" => checked_proposal_timing,
            );
            return Ok(false);
        }
        // Every reorged tenure cleared the rules, so the reorg is permitted.
        for tenure in superseded_tenures {
            self.record_superseded_tenure(signer_db, tenure);
        }
        Ok(true)
    }
```
