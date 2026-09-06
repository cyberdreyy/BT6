## Analysis

Mapping the CSRF bug class ("failing to regenerate a security token when the security context/identity changes, letting the old token remain valid across the switch") onto this codebase's equivalent security token — the **equivocation/conflict guard** that stops a signer from putting a second signature over a sibling block at the same height — the closest reachable analog is in the **reorg-permit** mechanism (`mark_tenure_superseded` / `reorg_permit_stands`), which grants that permit merely on **proposal receipt**, not on the replacement tenure actually being signed.

### Root cause

`SortitionData::check_parent_tenure_choice` decides whether a new tenure is allowed to build off something other than the immediately-prior sortition. If every reorged tenure produced at most one globally-accepted block whose proposal time was close enough to the sortition transition (`first_proposal_burn_block_timing`), the reorg is judged legitimate and the code immediately records each reorged tenure as **superseded**: [1](#0-0) [2](#0-1) [3](#0-2) 

This permit means: our own signature over the "superseded" tenure's block **no longer counts as a conflict** — as long as the *permitting* sortition (the new tenure that triggered the check) is still canonical on the burnchain: [4](#0-3) 

Crucially, `check_parent_tenure_choice` — and therefore `mark_tenure_superseded` — fires at **block-proposal arrival**, inside `check_proposal`, before the stacks-node even validates the block and long before any signature threshold is reached: [5](#0-4) 

The permit's continued validity (`reorg_permit_stands`) only re-checks that the *permitting tenure's sortition* is still canonical (`get_sortition_by_burn_hash`) — it never re-checks that the permitting tenure actually went on to produce a signed replacement block: [6](#0-5) 

So the "identity" that gets checked when the guard is later consulted (a canonical *sortition*) is weaker than the "identity" that justified voiding the guard in the first place (an actual *replacement block*, which was only assumed, not verified, to be forthcoming) — the equivalent of the CSRF token being regenerated for the wrong scope: the guard is released on a context (a proposal / winning a slot) that is a strict subset of the context it is supposed to represent (a completed, signed reorg).

### Impact

A one-slot miner who wins a single sortition can, merely by **broadcasting a tenure-change block proposal** (no signature, no threshold needed — every signer runs `check_proposal` independently on proposal receipt), permanently strip the conflict protection every signer holds over the tenures it reorgs, as long as those tenures happen to satisfy the (fairly common) "one block, mined close to the sortition boundary" condition. If the miner's own tenure then stalls or is abandoned (never gets signed), the network's canonical chain remains on the old, "superseded" tenure's block — but the signer set has already discarded that block's status as a conflict. A subsequent, unrelated tenure that also tries to reorg the same ancestor can then be signed by signers who believe the previous conflict guard doesn't apply to it (since `reorg_permit_stands` only checks the *original* permitting sortition's canonicity, not whether that sortition's tenure ever replaced anything), materially weakening the double-sign/equivocation guard the flow explicitly says exists "to stop us endorsing two blocks that could both end up in the chain."

### Recommendation
`reorg_permit_stands` (or the point where the permit is recorded) should additionally verify that the permitting tenure actually produced a *signed* replacement (e.g., via `has_signed_block_in_tenure`/`get_last_signed_block` for the permitting tenure) before excluding a conflict, not merely that its sortition remains canonical. Alternatively, defer recording the permit until the permitting tenure's own block has reached the pre-commit/signature stage, rather than at bare proposal-validation time.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L159-204)
```rust
impl SortitionData {
    /// Check if the tenure defined by `sortition_state` is building off of an
    ///  appropriate tenure.
    ///
    /// A permitted reorg is recorded once the whole reorg is permitted: each tenure whose
    /// blocks this one is allowed to replace is marked superseded (see
    /// [`SignerDb::mark_tenure_superseded`]), so a signature we already placed on one of those
    /// blocks does not later block the replacement. The record carries this tenure's sortition
    /// as the permitting one, so the permit stops applying if a burnchain fork later orphans
    /// it. Nothing is recorded for a refused reorg, even for the tenures in it that
    /// individually qualified.
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

**File:** stacks-signer/src/signerdb.rs (L1627-1660)
```rust
    /// Record that we permitted the tenure identified by `superseded_by_*` to reorg this one
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

**File:** stacks-signer/src/v0/signer.rs (L1208-1247)
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
```

**File:** stacks-signer/src/chainstate/v1.rs (L164-202)
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
```
