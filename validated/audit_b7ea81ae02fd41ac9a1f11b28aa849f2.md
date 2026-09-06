### Title
Reorg-permit check validates only the tenure's first signed block, letting a superseded tenure's later already-signed blocks be silently un-protected against equivocation - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
`check_parent_tenure_choice` decides whether a miner may reorg a prior tenure and, if so, records that tenure as "superseded" so a signer's own past signature over its blocks no longer blocks a replacement. The code's own documentation states this is safe only when a reorged tenure "produced zero blocks *or* produced their first (and only) block very close to the burn block transition." The enforced check, however, only verifies that the number of *globally accepted* blocks is ≤ 1 and only checks the *timing of the first* locally-known block. A tenure can have several blocks that are already signed (`LocallyAccepted`, including group-signed with ≥70% weight) without ever reaching `GloballyAccepted` (e.g. because the node has not yet processed/confirmed the push). Such a tenure passes the check based solely on its first block's timing, and the *entire* tenure - including all of its later, already-signed sibling blocks - is marked superseded. This decouples "signature was recorded" from "this specific signed block was ever checked/protected," breaking the equivocation guard for every block above the first.

### Finding Description
`check_parent_tenure_choice` in [1](#0-0)  guards a reorg by checking:

1. `globally_accepted_blocks = signer_db.get_globally_accepted_block_count_in_tenure(...)`, and rejects only if `> 1`.
2. Only the *first* approved/signed block's proposal timing, via `get_first_approved_block_in_tenure`, is checked against `first_proposal_burn_block_timing`: [2](#0-1) .

`get_globally_accepted_block_count_in_tenure` counts strictly `GloballyAccepted` blocks: [3](#0-2) . It does not count `LocallyAccepted` blocks, i.e. blocks the signer (or the whole signer set, via `signed_group`) has already put a signature over but which the node has not yet confirmed as its processed tip - a state the codebase elsewhere treats as fully "signed" for equivocation purposes: [4](#0-3) .

`get_first_approved_block_in_tenure` returns only the lowest-height approved block: [5](#0-4) ; no later block's timing or existence is examined.

If the (single) timing check on the first block passes, every reorged tenure - not just the first block - is recorded superseded: [6](#0-5) , persisted via `mark_tenure_superseded`: [7](#0-6) .

That record is then consumed at the exact place that is supposed to stop a signer from double-signing: `get_signed_conflicts` annotates every conflicting signed block in the superseded tenure (not just the first) with `superseded_by`: [8](#0-7) , and `reorg_permit_stands` unconditionally excludes any such conflict from blocking a new signature as long as the permitting sortition is canonical: [9](#0-8) , used directly in the pre-commit-to-signature path: [10](#0-9) .

Consequently: a tenure T1 can accumulate multiple already-signed (even group-signed, 70%-weight) blocks at heights h, h+1, h+2, ... none of which are globally accepted yet. As long as the *first* of them (height h) was proposed close enough to the next sortition, a subsequent tenure T2 that reorgs T1 is permitted, and the *whole* of T1 - including the un-checked, already-signed blocks at h+1, h+2, ... - is marked superseded. A signer that already signed T1's block at height h+1 (or observed the group sign it) will then be permitted to sign a *different*, conflicting T2 block at height h+1, because `get_signed_conflicts`/`reorg_permit_stands` no longer treat the old signature as a live conflict.

### Impact Explanation
This breaks the core equivocation guard the codebase repeatedly asserts is inviolable ("a block that could still end up in the chain... signing both would be the double-sign this guard exists for" - `docs/signer-flows.md` §5) and matches the report's exact bug class: a check that validates only "the first" instance of a value (here, the first block's timing / only globally-accepted count) is later relied upon to retroactively vouch for state (later already-signed blocks) that was never actually validated - directly analogous to `setCollectionData` letting a `collectionTotalSupply == 0` gate be exploited to decouple a stored signature from the state it was meant to certify. The result here is a signer producing a *second, conflicting signature* at a height where it (or the group) already signed a different block - i.e., a signer signing a conflicting block, which is explicitly listed as a Critical-severity impact for this scope.

### Likelihood Explanation
Reachable by a single winning miner across two consecutive tenures plus ordinary gossip, with no majority of colluding signers required: the miner only needs to (a) delay its first proposal in tenure T1 so it lands just inside `first_proposal_burn_block_timing` of the next sortition, (b) get several more T1 blocks locally/group-signed without letting the node observe them as its processed tip (achievable by controlling block-push timing or exploiting normal network/node lag), and (c) win the next sortition and build T2 off a different parent, triggering `check_parent_tenure_choice`. Signers behave exactly per protocol; the flaw is in the check itself.

### Recommendation
`check_parent_tenure_choice` must validate every block the tenure has had signed (locally or group-signed), not just globally-accepted count and the first block's timing. Concretely:
- Replace/augment `get_globally_accepted_block_count_in_tenure` with a count of all *signed* blocks (`LocallyAccepted` or `GloballyAccepted`, i.e. `signed_self`/`signed_group` set) in the tenure, and reject the reorg if more than one such block exists, matching the documented invariant ("first *and only*" block).
- If exactly one signed block exists, keep checking that one's timing as today; if more than one signed block exists in the reorged tenure, refuse the reorg (or at minimum, only supersede the specific already-verified block rather than the whole tenure) so `get_signed_conflicts`/`reorg_permit_stands` cannot silently unblock signatures over blocks that were never subjected to the timing check.

### Proof of Concept
Conceptual reproduction (matching the existing test harness style in `stacks-signer/src/chainstate/tests/v2.rs`, e.g. `check_parent_tenure_choice_reorg_timing_ok`):
1. Build tenure T1 with a first block B0 whose `approved_time` is set (locally accepted) and is within `first_proposal_burn_block_timing` of the mocked next sortition's `burn_header_timestamp` (as in `reorg_timing_testing(..., 30, 29)`).
2. Insert additional `BlockInfo`s for T1 at heights B0+1, B0+2 with `mark_locally_accepted(true)` (group-signed, i.e. `signed_group` set) but never `mark_globally_accepted`, so `get_globally_accepted_block_count_in_tenure(T1) == 0`.
3. Call `check_parent_tenure_choice` for a sortition whose `parent_tenure_id` differs from T1 (a T2 reorging away from T1) with the mocked `get_tenure_forking_info` reporting T1 with `first_block_mined = Some(B0)`.
4. Observe `check_parent_tenure_choice` returns `Ok(true)` and `signer_db.is_tenure_superseded(T1) == true` (as in the existing `check_parent_tenure_choice_reorg_timing_ok` assertion), even though B0+1/B0+2 were never checked for timing and are already signed by the group.
5. Show that `get_signed_conflicts(B0+1, ...)` now returns B0+1 annotated with `superseded_by = T2`, and `reorg_permit_stands` returns `true` for it, so a T2 proposal at height B0+1 will not be blocked by the earlier group-signed T1 block at the same height - i.e., the signer can now sign a second, conflicting block at that height.

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

**File:** stacks-signer/src/chainstate/mod.rs (L225-275)
```rust
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

**File:** stacks-signer/src/signerdb.rs (L1518-1527)
```rust
    /// Return the first approved/signed block in a tenure (identified by its consensus hash)
    pub fn get_first_approved_block_in_tenure(
        &self,
        tenure: &ConsensusHash,
    ) -> Result<Option<BlockInfo>, DBError> {
        let query = "SELECT block_info FROM blocks WHERE consensus_hash = ? AND (signed_self IS NOT NULL OR signed_group IS NOT NULL OR approved_time IS NOT NULL) ORDER BY stacks_height ASC LIMIT 1";
        let result: Option<String> = query_row(&self.db, query, [tenure])?;

        try_deserialize(result)
    }
```

**File:** stacks-signer/src/signerdb.rs (L1529-1541)
```rust
    /// Return the count of globally accepted blocks in a tenure (identified by its consensus hash)
    pub fn get_globally_accepted_block_count_in_tenure(
        &self,
        tenure: &ConsensusHash,
    ) -> Result<u64, DBError> {
        let query = "SELECT COALESCE((MAX(stacks_height) - MIN(stacks_height) + 1), 0) AS block_count FROM blocks WHERE consensus_hash = ?1 AND state = ?2";
        let args = params![tenure, &BlockState::GloballyAccepted.to_string()];
        let block_count_opt: Option<u64> = query_row(&self.db, query, args)?;
        match block_count_opt {
            Some(block_count) => Ok(block_count),
            None => Ok(0),
        }
    }
```

**File:** stacks-signer/src/signerdb.rs (L1564-1585)
```rust
    /// Return the last signed block in a tenure (identified by its consensus hash).
    /// A block is considered signed if it is locally or globally accepted. Blocks that
    /// have only been pre-committed are excluded, because a pre-commit does not put a
    /// signature over the block and may be safely superseded by a competing proposal.
    ///
    /// This answers "what is the tenure's signed tip?", a different question from
    /// [`SignerDb::has_signed_block_in_tenure`]'s "does a signature bind us to this tenure?",
    /// which is why the predicates deliberately differ on rejected blocks (see there).
    pub fn get_last_signed_block(
        &self,
        tenure: &ConsensusHash,
    ) -> Result<Option<BlockInfo>, DBError> {
        let query = "SELECT block_info FROM blocks WHERE consensus_hash = ?1 AND state IN (?2, ?3) ORDER BY stacks_height DESC LIMIT 1";
        let args = params![
            tenure,
            &BlockState::GloballyAccepted.to_string(),
            &BlockState::LocallyAccepted.to_string(),
        ];
        let result: Option<String> = query_row(&self.db, query, args)?;

        try_deserialize(result)
    }
```

**File:** stacks-signer/src/signerdb.rs (L1606-1625)
```rust
    pub fn get_signed_conflicts(
        &self,
        height: u64,
        excluded_signer_signature_hash: &Sha512Trunc256Sum,
    ) -> Result<Vec<SignedConflictInfo>, DBError> {
        let query = "SELECT b.consensus_hash, b.signer_signature_hash, b.stacks_height, b.state,
                MAX(COALESCE(b.signed_self, 0), COALESCE(b.signed_group, 0)) AS last_endorsed,
                st.superseded_by_consensus_hash, st.superseded_by_burn_block_hash
            FROM blocks b
            LEFT JOIN superseded_tenures st ON st.consensus_hash = b.consensus_hash
            WHERE (b.signed_self IS NOT NULL OR b.signed_group IS NOT NULL)
                AND b.stacks_height >= ?1
                AND b.signer_signature_hash != ?2
            ORDER BY b.stacks_height DESC";
        let args = params![
            u64_to_sql(height)?,
            excluded_signer_signature_hash.to_string(),
        ];
        query_rows(&self.db, query, args)
    }
```

**File:** stacks-signer/src/signerdb.rs (L1642-1660)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L1222-1247)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L1383-1421)
```rust
        let conflicts = match self
            .signer_db
            .get_signed_conflicts(block_info.block.header.chain_length, &block_hash)
        {
            Ok(conflicts) => conflicts,
            Err(e) => {
                warn!("{self}: Failed to query the signed blocks. Refusing to sign block {block_hash}: {e:?}");
                return;
            }
        };
        let freshness_cutoff = get_epoch_time_secs().saturating_sub(
            self.proposal_config
                .tenure_last_block_proposal_timeout
                .as_secs(),
        );
        // A fresh signature only blocks while the block it covers could still be part of the
        // chain: see `conflict_still_blocks`, which asks the node whether it is. Check
        // freshness first: it is a local timestamp comparison, while `reorg_permit_stands`
        // and `conflict_still_blocks` each query the node, so stale conflicts cost no
        // round-trips.
        if let Some(conflict) = conflicts.iter().find(|conflict| {
            conflict.last_endorsed > freshness_cutoff
                && !self.reorg_permit_stands(stacks_client, conflict)
                && self.conflict_still_blocks(
                    stacks_client,
                    conflict,
                    block_info.block.header.chain_length,
                )
        }) {
            warn!(
                "{self}: Reached the pre-commit threshold for a block, but we have recently signed or accepted a different block at the same or higher height. Refusing to sign.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "conflicting_signer_signature_hash" => %conflict.signer_signature_hash,
                "conflicting_block_height" => conflict.stacks_height,
                "conflicting_consensus_hash" => %conflict.consensus_hash,
            );
            return;
        }
```
