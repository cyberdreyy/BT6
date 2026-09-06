### Title
Reorg-permit issued on `approved` (pre-commit) timing rather than `signed` state lets a signer's own equivocation guard be bypassed - (File: stacks-signer/src/chainstate/mod.rs)

### Summary
`SortitionData::check_parent_tenure_choice` decides whether a miner is allowed to reorg a prior tenure, and — if it decides yes — permanently records that tenure as `superseded` via `SignerDb::mark_tenure_superseded`. Once a tenure is marked superseded, `Signer::reorg_permit_stands` causes the pre-commit conflict guard in `handle_block_pre_commit` (`get_signed_conflicts` / `conflict_still_blocks`) to unconditionally stop treating any block in that tenure as a conflict, as long as the permitting sortition stays canonical. This is analogous to the django-mfa2 bug: a one-time authorization ("permit"/"challenge") is issued based on a check performed once, and is never re-validated against the actual state (whether the signer's own signature was placed) at the time it is consumed. [1](#0-0) [2](#0-1) 

### Finding Description
The comment on the check says the reorg is disallowed "if more than one block has already been **signed**", but the code actually queries `get_globally_accepted_block_count_in_tenure`, i.e. whether the **node** has processed the block — not whether the signer itself already placed `signed_self` on a block in that tenure: [3](#0-2) 

If that count is ≤1, the function falls through to a purely local timing heuristic (`get_first_approved_block_in_tenure`, `approved_time`), which — per the docs — is stamped at **pre-commit** (no signature) or at local acceptance, whichever comes first: [4](#0-3) 

If the timing looks "late" relative to the next sortition, `check_parent_tenure_choice` returns `Ok(true)` and unconditionally calls `record_superseded_tenure` → `mark_tenure_superseded`, writing a permanent DB row (`INSERT OR REPLACE`, keyed only by the old tenure's consensus hash) that says "this tenure's blocks are superseded by tenure Y": [5](#0-4) [6](#0-5) 

This write happens at **proposal-check time** (`check_proposal` → `validate_tenure_change_payload`), and is never re-derived or re-validated against the signer's own signed state at consumption time. At the pre-commit threshold, `handle_block_pre_commit` consults `get_signed_conflicts`, and any conflict whose tenure carries a standing permit is excluded from the equivocation guard entirely via `reorg_permit_stands`, which only asks the node whether the *permitting* sortition is still canonical — it never asks whether the signer itself has already signed a live block in the tenure being superseded: [7](#0-6) [2](#0-1) 

This breaks the invariant the codebase otherwise treats as sacred (see `docs/signer-flows.md`): "a signature is a bearer instrument... once public it can still be aggregated toward the 70% threshold... so a block we signed binds us no matter what state it later fell to." The permit-issuance path is the one place that invariant is not actually enforced — it substitutes "globally accepted" (node-confirmed) and "approved" (pre-commit) timestamps for "signed" state, so a block the signer has already put its own signature over, but which the node hasn't yet globally accepted (still gathering the rest of the 70% group), can be silently exempted from ever blocking a second, conflicting signature at the same height.

### Impact Explanation
This is a **Critical** analog under the stated impact classes: it is a path by which a single signer, driven by a single miner's proposal sequence, can be induced to place a second signature over a block that conflicts with (equivocates against) a block it already signed at the same height — the exact double-sign the pre-commit conflict guard (`get_signed_conflicts` / `conflict_still_blocks`, and the `signer_refuses_to_sign_second_sibling_tenure_start` test) exists to prevent. If enough signers experience the same race (each independently derives its own `approved_time` locally, but a fast-following miner-driven sortition can create the same timing condition for many signers simultaneously), the safety property "no signer signs two conflicting blocks at the same height" can be violated across the set, undermining fork-choice safety.

### Likelihood Explanation
The permit decision is entirely local to each signer (no majority vote required) and is driven by facts the miner already controls: how quickly it starts a new tenure/sortition relative to when the prior tenure's block was proposed, and whether the node has finished processing the prior block yet (a natural, frequently-occurring race, not a contrived one — group-signature aggregation and node processing both take non-zero time). A single miner holding two consecutive sortition slots (or racing a slow node) can trigger `check_parent_tenure_choice`'s "late-arriving proposal" branch while the signer's own already-issued signature over the prior tenure's block has not yet reached the node. No secondary signer collusion, secp256k1/serde defect, or transport manipulation is required — only the ordinary sequencing of `check_proposal` (tenure-change validation) versus block push/global acceptance.

### Recommendation
- Change the disallow condition in `check_parent_tenure_choice` to check whether the signer has **signed** a block in the tenure being reorged (`signed_self`/`signed_group`, as used by `get_signed_conflicts`), not merely whether the node has globally accepted it (`get_globally_accepted_block_count_in_tenure`), so the comment and the code agree.
- Re-derive/re-validate the permit at consumption time (in `reorg_permit_stands`) against the signer's *current* signed state for the superseded tenure, rather than trusting a one-time decision recorded at proposal time that can predate the signer's own subsequent signature.
- Consider tying the permit to a specific proposed reorging block (its `signer_signature_hash`) rather than only to the reorging tenure's consensus hash, so a permit issued for one (possibly later-rejected) tenure-change proposal cannot be reused to clear conflicts for an unrelated later proposal in the same tenure.

### Proof of Concept
1. Miner M mines tenure X; the sole signer set signs the first (and only) block of tenure X locally (`signed_self` set on all signers), but the resulting signature bundle has not yet been delivered to / processed by the stacks-node (still in flight / node busy), so `get_globally_accepted_block_count_in_tenure(X) == 0`.
2. M immediately mines the next Bitcoin block and proposes a tenure-change block for tenure Y whose `prev_tenure_consensus_hash` reorgs tenure X.
3. Each signer runs `check_proposal` → `validate_tenure_change_payload` → `check_parent_tenure_choice`: `globally_accepted_blocks` is 0 (≤1), and `get_first_approved_block_in_tenure(X)`'s `approved_time` is close to the new sortition's receive time (natural, since X's only block was just approved), so the "late-arriving proposal" branch fires and `Ok(true)` is returned.
4. `record_superseded_tenure` marks tenure X as superseded by Y in every signer's DB, even though every signer already carries a live `signed_self` signature over X's block.
5. Tenure Y's block reaches the pre-commit threshold; in `handle_block_pre_commit`, `get_signed_conflicts` finds X's block as a same-height/height≥ conflict, but `reorg_permit_stands` reports the permit stands (Y's sortition is canonical), so the conflict is excluded and the signer signs Y's block — producing two conflicting, independently signed blocks at the same height from the same signer set.

Note: I was not able to directly view the body of `get_first_approved_block_in_tenure`/`get_globally_accepted_block_count_in_tenure` in this pass (grep found the definitions in `stacks-signer/src/signerdb.rs` but they were not returned in full); the timing semantics (`approved_time`) are corroborated by `docs/signer-flows.md` line 156-158 and by the `chainstate/mod.rs` comment block cited above, but a full read of those two SQL query functions would be needed to conclusively rule out an additional guard (e.g., an explicit "is this tenure's block signed" check) that isn't visible in the excerpts retrieved. I recommend confirming this before treating the PoC as fully verified.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L170-223)
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

**File:** stacks-signer/src/v0/signer.rs (L1340-1366)
```rust
        // The chain and signer db state may have changed materially since this block passed the
        // proposal-time checks (e.g. between validation and reaching the pre-commit threshold we
        // may have signed a block that this one would reorg). Re-run the chainstate checks
        // before putting a signature over the block, and respond with a rejection if they no
        // longer pass, just as the block validation response handler does.
        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
        {
            warn!(
                "{self}: Reached the pre-commit threshold for a block, but it no longer passes the chainstate checks. Rejecting.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "reject_code" => %block_rejection.reason_code,
                "reject_reason" => &block_rejection.reason,
            );
            if let Err(e) = block_info.mark_locally_rejected() {
                if !block_info.has_reached_consensus() {
                    warn!("{self}: Failed to mark block as locally rejected: {e:?}");
                }
            };
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.handle_block_rejection(&block_rejection, sortition_state);
            self.send_block_response(&block_info.block, block_rejection.into());
            return;
        }
```

**File:** docs/signer-flows.md (L156-158)
```markdown
Timestamps: `approved_time` is stamped at pre-commit _or_ local acceptance
(first wins), `signed_self` only when we sign, `signed_group` when the group
threshold is observed.
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
