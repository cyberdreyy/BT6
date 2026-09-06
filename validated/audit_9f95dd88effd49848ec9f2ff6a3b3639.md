### Title
Miner-controlled proposal timing lets a single actor manufacture a "poorly-timed" verdict in `check_parent_tenure_choice`, permitting a sanctioned reorg of its own already-signed tenure - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`SortitionData::check_parent_tenure_choice` decides whether a new tenure is allowed to build off something other than the prior sortition (i.e., reorg an already-mined tenure) using a purely local, attacker-influenced timing heuristic: it compares `sortition_state_received_time` (when *this* signer received the new burn block) against `approved_time` of the reorged tenure's first block, and treats the reorg as legitimate ("poorly timed proposal, allowing the reorg") whenever `proposal_to_sortition < first_proposal_burn_block_timing` [1](#0-0) . This is the same "bring myself to exactly the edge, then trigger the crossing" pattern as the Perennial self-liquidation bug: a single miner who also controls (or colludes with, via gossip) the next tenure's proposer can deliberately delay finalizing/broadcasting its own tenure-start block until it is close to the next Bitcoin block, making the timing gap small enough to satisfy the "too close to the burn block transition" exception - the exact exception meant only for rare, unintentional fast-sortition races.

### Finding Description
The reorg-permission check only guards against reorging tenures with **more than one** globally accepted block; a tenure with exactly one globally accepted block is eligible for the timing exception [2](#0-1) . If the timing test passes, the tenure is pushed into `superseded_tenures` and, once the whole reorg clears, `record_superseded_tenure`/`mark_tenure_superseded` is called, permanently telling every signer's local DB that a signature already placed on the reorged tenure's block must stop counting as a conflict while the permitting sortition remains canonical [3](#0-2) [4](#0-3) .

The timing inputs are both attacker-adjacent:
- `approved_time` is stamped locally the first time a signer pre-commits/accepts a proposal - not a consensus-agreed value, and its lateness is directly a function of when the miner chooses to finish and broadcast its tenure-start block for signing.
- `sortition_state_received_time` is when *this* signer's node reported the new burn block; a miner that intentionally races its own block submission against the very end of its tenure window (right before the next Bitcoin block lands) can reliably push `proposal_to_sortition` under `first_proposal_burn_block_timing` for most/all signers.

Because `check_parent_tenure_choice` is exactly the same check used both in `is_tenure_valid`/`check_proposal` (deciding whether to accept a new tenure's blocks) and in `validate_tenure_change_payload` (validating a `TenureChange` payload) [5](#0-4) [6](#0-5) , a single miner (optionally colluding with the winner of the very next sortition, which is ordinary gossip-level coordination, not a signer majority) can engineer this edge case to have its *own* already-globally-accepted single-block tenure legitimately superseded. Once `mark_tenure_superseded` is recorded, `reorg_permit_stands`/`get_signed_conflicts`-based checks at the pre-commit/signature stage (section 5 of the flow) will *exclude* the old tenure's block as a conflict [7](#0-6) , so signers who already signed the original block will also sign the new, conflicting block at the same height in a different tenure.

This mirrors the Perennial pattern precisely: a check meant to be a rare-exception safety valve (fast successive Bitcoin blocks producing a legitimately unsigned/late tenure) is turned into a self-service switch by an actor who fully controls both sides of the compared timestamps, letting them "self-liquidate" (reorg away) their own already-accepted tenure at will and for cheap.

### Impact Explanation
This breaks the "approved-parent vs canonical" / "no signing of a conflicting block" equality: two blocks at the same height in different tenures can both end up carrying valid signer signatures, because the conflict-suppression record (`mark_tenure_superseded`) was obtained through manufactured timing rather than a genuine fast-sortition race. That is a Critical-class outcome per the rules ("a signer signing an invalid, non-canonical, or conflicting block").

### Likelihood Explanation
Requires only: (1) winning a tenure, (2) controlling exactly when its tenure-start block is finalized/broadcast for signing, and (3) either also winning the next sortition or gossip-coordinating with whoever does (no signer majority, no other signer's key, no node bug needed). Since block-proposal timing is entirely miner-controlled and the check only cares about the local receive/approve timestamp gap, this is reachable by a lone miner across two consecutive slots plus ordinary network gossip.

### Recommendation
Do not let the reorg-timing exception be satisfiable via a locally-observed, miner-influenced timestamp gap alone. Require either a globally-observable/consensus-derived signal that the burn block truly landed abnormally fast relative to the network's typical proposal-to-sortition timing (e.g., cross-checked against multiple signers' burn-block receipt times or the burnchain's actual inter-block time), or bound the exception so it cannot apply when the reorging party is the same miner (or colludes with the immediately following miner) that produced the tenure being reorged, closing the same "initial vs maintenance margin" gap that the referenced Perennial fix addressed by disallowing self-serving crossings of a borderline threshold.

### Proof of Concept
Conceptual replay of the existing test scaffolding (`check_parent_tenure_choice_reorg_timing_ok` in `stacks-signer/src/chainstate/tests/v2.rs`) demonstrates the mechanics already exercised in-repo [8](#0-7) : setting `sortition_timing_secs` just under `first_proposal_burn_block_timing_secs` flips the verdict from "reorg refused" to "reorg permitted, tenure marked superseded." A malicious miner reproduces this in production by deliberately delaying its own tenure-start block's broadcast/validation until just before the next Bitcoin block, then (itself or a colluding next-tenure miner) proposing a new tenure that reorgs the first one; signers that already signed the first tenure's sole block will, per `reorg_permit_stands`, no longer treat that as a conflict and will sign the reorging block too - producing two signed, conflicting blocks at the same chain height.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L210-245)
```rust
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

**File:** stacks-signer/src/chainstate/mod.rs (L290-315)
```rust
        // Every reorged tenure cleared the rules, so the reorg is permitted.
        for tenure in superseded_tenures {
            self.record_superseded_tenure(signer_db, tenure);
        }
        Ok(true)
    }

    /// Note that we have sanctioned `self`'s tenure replacing whatever `tenure` built, so a
    /// signature we already placed on one of its blocks must stop counting as a conflict while
    /// `self`'s sortition remains canonical.
    ///
    /// A failure to record only costs a delayed replacement -- the conflict keeps blocking until
    /// the signature goes stale -- so it is logged rather than propagated.
    fn record_superseded_tenure(&self, signer_db: &mut SignerDb, tenure: &TenureForkingInfo) {
        if let Err(e) = signer_db.mark_tenure_superseded(
            &tenure.consensus_hash,
            tenure.burn_block_height,
            &self.consensus_hash,
            &self.burn_block_hash,
        ) {
            warn!("Failed to record a tenure whose reorg we permitted: {e}";
                "superseded_tenure_id" => %tenure.consensus_hash,
                "superseded_by" => %self.consensus_hash,
            );
        }
    }
```

**File:** stacks-signer/src/signerdb.rs (L1628-1660)
```rust
    /// under the reorg-timing rules (`first_proposal_burn_block_timing`).
    ///
    /// Having sanctioned the replacement, our own signature over what this tenure built must not
    /// then block it: its blocks stop counting as conflicts (see
    /// [`SignerDb::get_signed_conflicts`]). Recorded when the reorg is permitted rather than
    /// derived at signing time, because by the time a replacement reaches the pre-commit
    /// threshold the sortition view that sanctioned the reorg may be long gone.
    ///
    /// The permit is only honored while the permitting tenure's sortition is still canonical
    /// (checked against the node when the record is applied): if a burnchain fork orphans it,
    /// the reorg we sanctioned can no longer happen, so the record must not keep suppressing
    /// this tenure's conflicts. A re-permit by a different tenure replaces the record, so the
    /// latest permitting sortition is the one checked. Records age out via
    /// [`SignerDb::prune_superseded_tenures`].
    pub fn mark_tenure_superseded(
        &mut self,
        consensus_hash: &ConsensusHash,
        burn_block_height: u64,
        superseded_by_consensus_hash: &ConsensusHash,
        superseded_by_burn_block_hash: &BurnchainHeaderHash,
    ) -> Result<(), DBError> {
        self.db.execute(
            "INSERT OR REPLACE INTO superseded_tenures (consensus_hash, burn_block_height, superseded_by_consensus_hash, superseded_by_burn_block_hash, superseded_at) VALUES (?1, ?2, ?3, ?4, ?5)",
            params![
                consensus_hash,
                u64_to_sql(burn_block_height)?,
                superseded_by_consensus_hash,
                superseded_by_burn_block_hash,
                u64_to_sql(get_epoch_time_secs())?
            ],
        )?;
        Ok(())
    }
```

**File:** stacks-signer/src/chainstate/v1.rs (L176-202)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L1208-1248)
```rust
    /// Whether a reorg permit recorded for this conflict's tenure still stands.
    ///
    /// `check_parent_tenure_choice` records a permit when the reorg-timing rules sanction a
    /// later tenure replacing what the conflict's tenure built (see
    /// [`SignerDb::mark_tenure_superseded`]). A standing permit excludes the conflict entirely:
    /// our signature must not stand in the way of a replacement we sanctioned. But the permit
    /// is only as alive as the sortition it was granted to: if a burnchain fork orphaned the
    /// permitting sortition, the reorg we sanctioned can no longer happen, and the record must
    /// not keep suppressing the conflict.
    ///
    /// A false 404 here (e.g. from a node still catching up) only restores a conflict the
    /// permit could have excluded, which at worst delays the replacement, so unlike
    /// `conflict_still_blocks` no tip-height guard is needed. A node error voids the permit for
    /// the same reason: blocking is the direction that can be taken back.
    fn reorg_permit_stands(
        &self,
        stacks_client: &StacksClient,
        conflict: &SignedConflictInfo,
    ) -> bool {
        let Some(superseded_by) = &conflict.superseded_by else {
            return false;
        };
        match stacks_client.get_sortition_by_burn_hash(&superseded_by.burn_block_hash) {
            Ok(_) => true,
            Err(ClientError::RequestFailure(reqwest::StatusCode::NOT_FOUND)) => {
                info!("{self}: The tenure we permitted to reorg a conflicting block's tenure was itself orphaned by a burnchain fork. The permit no longer excludes the conflict.";
                    "conflicting_consensus_hash" => %conflict.consensus_hash,
                    "superseded_by_consensus_hash" => %superseded_by.consensus_hash,
                    "superseded_by_burn_block_hash" => %superseded_by.burn_block_hash,
                );
                false
            }
            Err(e) => {
                warn!("{self}: Failed to check whether the sortition that permitted a reorg is still canonical: {e:?}. Treating the permit as void.";
                    "conflicting_consensus_hash" => %conflict.consensus_hash,
                    "superseded_by_consensus_hash" => %superseded_by.consensus_hash,
                );
                false
            }
        }
    }
```

**File:** stacks-signer/src/chainstate/tests/v2.rs (L372-380)
```rust
#[test]
fn check_parent_tenure_choice_reorg_timing_ok() {
    let (result, superseded) = reorg_timing_testing(function_name!(), 30, 29);
    assert!(result.unwrap(), "Tenure choice should be okay because the reorg occurred in a block whose proposed time was close to the sortition");
    assert!(
        superseded,
        "Having sanctioned the reorg, our signature over the reorged tenure's block must stop counting as a conflict"
    );
}
```
