### Title
`check_parent_tenure_choice` counts only globally-accepted blocks, letting a tenure with one global + one merely locally-accepted (signed) block be reorg-permitted and its signature silently excluded - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`SortitionData::check_parent_tenure_choice` guards reorgs with `globally_accepted_blocks > 1`, but its own inline comment says the intent is to disallow a reorg whenever "more than one block has already been signed." [1](#0-0) . Because the guard only counts blocks in `BlockState::GloballyAccepted`, a tenure that produced one globally-accepted block and a second, merely `LocallyAccepted` (i.e., signed by ≥70% weight but not yet handed to/observed by the node) block passes the guard, and the timing check that follows only inspects the *first* block's proposal-to-sortition timing via `get_first_approved_block_in_tenure` [2](#0-1) . If that first block's timing was close enough to the sortition boundary, the whole tenure — including the unaccounted-for second signed block — is recorded as superseded.

### Finding Description
The equality being violated: "a reorg is permitted only if the tenure has at most one *signed* block" is what the code comment promises, but the code actually evaluates "at most one *globally accepted* block" [3](#0-2) . `LocallyAccepted` blocks (signed by the required weight, including possibly this signer's own signature) are invisible to `get_globally_accepted_block_count_in_tenure`.

Exploit flow:
1. Attacker wins the mining slot for tenure A and proposes block B1 late in the tenure window (close to the next sortition), so `checked_proposal_timing` will later qualify it as "poorly timed, allowing the reorg" [4](#0-3) . B1 reaches global acceptance (it must, since `tenure.first_block_mined` is derived from the node's own view via `get_tenure_forking_info`).
2. Immediately after, the miner proposes B2 (chain_length B1+1) in the same tenure A. Signers reach the 70% pre-commit/signature threshold and mark it `LocallyAccepted`, but the `broadcast_signed_block` → node `NewBlock` event → `mark_globally_accepted` transition has not yet completed when the next Bitcoin block/sortition arrives.
3. A new miner (attacker again, or a colluding/next-slot miner) starts tenure B, building on the prior (pre-tenure-A) sortition instead of on tenure A, and proposes a tenure-change block. `check_parent_tenure_choice` runs: `globally_accepted_blocks` for tenure A is 1 (only B1), so the `> 1` guard does not fire; `get_first_approved_block_in_tenure` only reasons about B1's timing, which was engineered to qualify; the reorg is therefore permitted and `record_superseded_tenure` marks tenure A as superseded by consensus_hash [5](#0-4) .
4. `get_signed_conflicts` joins conflicts to `superseded_tenures` purely by `consensus_hash`, so *every* signed block in tenure A — including B2, which was never evaluated by the timing/permit logic — is annotated with a live `superseded_by` record [6](#0-5) .
5. When the victim signer later evaluates tenure B's competing proposal at a conflicting height, `reorg_permit_stands` sees the permitting sortition (tenure B itself) is canonical and excludes B2 as a conflict entirely [7](#0-6) , so the pre-commit backstop that would normally have refused to sign a second conflicting block never fires [8](#0-7) .

Existing guards that fail to catch this: the `DuplicateBlockFound` check in `validate_tenure_change_payload` only inspects the *proposed block's own* tenure, not the reorged tenure [9](#0-8) ; and the permit mechanism is deliberately blanket over the whole superseded tenure by design (intended for the "zero-block" and "single, late block" cases), but the >1 guard that is supposed to prevent this from applying to a multi-block tenure checks the wrong predicate (global acceptance, not "signed").

### Impact Explanation
This breaks the uniqueness/non-equivocation safety property the whole `get_signed_conflicts`/`conflict_still_blocks`/`reorg_permit_stands` machinery exists to enforce: the same signer's signature over block B2 (tenure A) is silently voided as a "conflict" even though it was never actually vetted for reorg-timing eligibility. If B2 or a sibling of it at the same height ever reaches the 70% global threshold independently (e.g., the network partition/delay heals), and the signer also signs a conflicting block in tenure B, the signer has produced two valid signatures over blocks at overlapping heights in two different tenures that could both be finalized — a cross-tenure double-signature, matching the Critical category ("a signature valid across chain/cycle/tenure boundaries").

### Likelihood Explanation
Preconditions: attacker needs only a single winning miner slot (to build tenure A) and the ability to craft/propose block timing so that B1 lands late in the tenure (attacker fully controls proposal timing) — no majority signer weight or privileged role is required. The harder-to-guarantee precondition is the race between B2 reaching local (70%) acceptance and its global acceptance being observed before the next sortition/tenure-change proposal arrives; this is a timing race rather than a network-tampering requirement, so it is plausible but not deterministic, and would need to be repeated per attempt. It is repeatable across cycles since it only depends on relative timing of burn blocks and block processing, not on any one-time secret or privileged capability.

### Recommendation
Change `check_parent_tenure_choice`'s guard to match its own documented intent: count all *signed* blocks (`LocallyAccepted` and `GloballyAccepted`) in the tenure via something like `get_signed_block_count_in_tenure`, not just `get_globally_accepted_block_count_in_tenure`. Additionally, when timing-qualifying a tenure for supersession, verify that the *last* signed block in the tenure (not just the first) satisfies the proximity-to-sortition timing, since a later signed block re-opens the exact conflict this rule is meant to close.

### Proof of Concept
Rust test plan in `stacks-signer/src/chainstate/tests/v2.rs` (or `mod.rs`):
1. Set up `signer_db` with tenure A containing two blocks: block B1 in state `GloballyAccepted` (via `insert_block` + `mark_globally_accepted`) with `approved_time` set close to a mock `sortition_state_received_time`, and block B2 in state `LocallyAccepted` (via `mark_locally_accepted`) at `chain_length` = B1+1, with `signed_self` set (simulating the signer's own signature).
2. Build a `TenureForkingInfo` for tenure A with `first_block_mined = Some(B1.block_id())`.
3. Call `cur_sortition.data.check_parent_tenure_choice(&mut signer_db, &client, &first_proposal_burn_block_timing)` with the mocked HTTP response for `get_tenure_forking_info`, and assert it returns `Ok(true)` and `signer_db.is_tenure_superseded(&tenure_a_ch)` is `true` — demonstrating the guard passes despite two signed blocks existing.
4. Then call `signer_db.get_signed_conflicts(B2.chain_length, &some_unrelated_hash)` and assert the returned `SignedConflictInfo` for B2 carries a non-`None` `superseded_by` pointing at the permitting tenure — demonstrating that B2's signature, never individually vetted, is excluded from future conflict checks.
5. Assert this combination (`result == true` for the reorg permit AND B2 excluded via `get_signed_conflicts`) constitutes the broken uniqueness guarantee: the signer's own signed block B2 no longer blocks a competing/conflicting proposal in the reorging tenure. [3](#0-2) [6](#0-5)

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L210-223)
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
```

**File:** stacks-signer/src/chainstate/mod.rs (L225-245)
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
```

**File:** stacks-signer/src/chainstate/mod.rs (L247-274)
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

**File:** stacks-signer/src/v0/signer.rs (L1383-1435)
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

        // No conflict is both fresh and still live. A conflict that no longer matters, i.e.
        // stale, or provably dead per `conflict_still_blocks`, cannot veto on its own. A
        // stale conflict in another tenure in particular no longer speaks for us: whether this
        // block may replace what another tenure built is settled by the chainstate checks above.
        // A stale conflict in this block's own tenure still blocks if the node already has that
        // tenure at or above the proposed height, since the proposal then duplicates state the
        // node has already built on. (The chainstate checks don't cover this for tenure-change
        // blocks: those check the parent tenure instead of their own.)
        // The permit check is deferred to here so that only same-tenure conflicts pay for it.
        if conflicts.iter().any(|conflict| {
            conflict.consensus_hash == block_info.block.header.consensus_hash
                && !self.reorg_permit_stands(stacks_client, conflict)
        }) {
```

**File:** stacks-signer/src/chainstate/v2.rs (L340-358)
```rust
        // We already confirmed in check miner activity that the current tenure is valid. So check we are not
        // reorging the tenure blocks. Only blocks we have signed (locally or globally accepted) count
        // here: a block we have merely pre-committed to carries no signature from us, so it is safe to
        // accept a competing tenure-start block in its place if it failed to reach consensus.
        let last_in_current_tenure = signer_db
            .get_last_signed_block(&block.header.consensus_hash)
            .map_err(|e| {
                SignerChainstateError::from(ClientError::InvalidResponse(e.to_string()))
            })?;
        if let Some(last_in_current_tenure) = last_in_current_tenure {
            warn!(
                "Miner block proposal contains a tenure change, but we've already signed a block in this tenure. Considering proposal invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "proposed_block_signer_signature_hash" => %block.header.signer_signature_hash(),
                "last_in_tenure_signer_signature_hash" => %last_in_current_tenure.block.header.signer_signature_hash(),
            );
            return Err(RejectReason::DuplicateBlockFound);
        }
        Ok(())
```
