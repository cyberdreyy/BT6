### Title
`check_parent_tenure_choice` counts only globally-accepted blocks, permitting a reorg over a tenure the signer already signed 2+ times locally - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`SortitionData::check_parent_tenure_choice` guards against reorging a tenure that "has already more than one globally accepted block" by calling `SignerDb::get_globally_accepted_block_count_in_tenure`, which only counts rows with `state = GloballyAccepted` [1](#0-0) [2](#0-1) . A tenure that has 2+ blocks the signer itself locally accepted and signed, but that never reached global acceptance (never pushed to the node), passes this guard with a count of 0, and — because the node then also has no knowledge of any block in that tenure — the code takes the "no blocks mined" shortcut and unconditionally marks the tenure superseded with no timing check at all [3](#0-2) .

### Finding Description
The guard comment states the intent as "disallow reorg if more than one block has already been signed," but the implementation measures global acceptance, not signing, via `get_globally_accepted_block_count_in_tenure` [1](#0-0) . The correct predicate for "has this signer already signed/accepted blocks in this tenure" is `get_last_signed_block`/`has_signed_block_in_tenure`, which include `LocallyAccepted` state and are explicitly documented and used elsewhere in the codebase for this exact purpose (e.g. the v2 duplicate-tenure-change check) [4](#0-3) [5](#0-4) . The unit test `check_globally_signed_block_count` confirms the mismatch directly: a locally accepted block "still returns 0" for `get_globally_accepted_block_count_in_tenure` [6](#0-5) .

Exploit path: as the block-winning miner for tenure T, mine and gossip two sequential blocks (heights N, N+1) fast enough that this signer individually validates and signs both (`LocallyAccepted`, `signed_self` set) before the aggregate threshold is reached and before the coordinator pushes either block to the node (so the node never learns of tenure T's blocks at all). From this signer's perspective:
- `get_globally_accepted_block_count_in_tenure(T)` returns 0 (≤1, guard does not trip) [7](#0-6) .
- `tenure.first_block_mined` (from the node's `get_tenure_forking_info`) is `None`, since the node never saw any block from T; this branch unconditionally pushes T onto `superseded_tenures` with **no** timing check whatsoever [3](#0-2) .
- `check_parent_tenure_choice` returns `Ok(true)` and `record_superseded_tenure`/`mark_tenure_superseded` is called for T [8](#0-7) [9](#0-8) .

Now the miner proposes a replacement tenure (call it T') that reorgs T and proposes a block at the same/overlapping stacks height as one of T's two locally-signed blocks. When this signer evaluates that block for signing, `get_signed_conflicts` correctly returns T's locally-signed blocks as conflicts (the query includes any row with `signed_self`/`signed_group` set, not just globally accepted ones) [10](#0-9) , but because T is now marked superseded, `reorg_permit_stands` reports the permit as standing (the permitting sortition, T', is canonical) and the conflict is excluded, allowing the signer to sign the new, conflicting block [11](#0-10) [12](#0-11) . The signer thus ends up having signed two mutually-conflicting blocks at the same/overlapping height — an equivocation the reorg-timing guard exists specifically to prevent.

No other check catches this: the "duplicate block found" check in `validate_tenure_change_payload` only checks the current/parent tenure being confirmed, not the reorged tenures under consideration [13](#0-12) ; `has_signed_block_in_tenure` (the correct commitment predicate) is not consulted by `check_parent_tenure_choice` at all.

### Impact Explanation
This breaks the safety property that a signer never places its signature over two conflicting blocks (equivocation). Because `check_parent_tenure_choice`/`reorg_permit_stands` run independently per signer against locally observed state, this is exploitable against any individual signer without needing signer majority — matching the Critical category "a signer signing an invalid, non-canonical, or conflicting block (chain safety)." It is repeatable: any tenure where the miner can pipeline two blocks past a signer's local acceptance without global push is exploitable the same way.

### Likelihood Explanation
Preconditions are attacker-controllable with only a single miner slot plus normal gossip: win tenure T, mine and propose two blocks quickly enough for at least one target signer to individually validate/sign both before either reaches the 70% global-acceptance threshold and gets pushed to the node, then produce a competing tenure-change proposal reorging T. No majority-signer collusion, node access, or auth token is required — only the ability to control mining/proposal timing, which is within the unprivileged miner's normal capabilities. The "first_block_mined is None" shortcut in `check_parent_tenure_choice` makes this the easiest variant (zero global accepts needed), but a variant with 1 global + 1 local accepted block (still counted as ≤1) is also viable.

### Recommendation
Change the guard in `check_parent_tenure_choice` to count blocks this signer has signed (locally or globally accepted), consistent with `SignerDb::get_last_signed_block`/`has_signed_block_in_tenure`, rather than `get_globally_accepted_block_count_in_tenure`. Concretely, replace the `get_globally_accepted_block_count_in_tenure` call (and the "first_block_mined is None" shortcut, which should also account for locally-known-but-unpushed blocks) with a count derived from `signed_self`/`signed_group` presence in `signerdb`, so that any tenure where this signer has already signed 2+ blocks is disqualified from a permitted reorg regardless of whether the node ever saw them.

### Proof of Concept
Rust test in `stacks-signer/src/chainstate/tests/v2.rs`:
1. Build a `SignerDb` and insert two `BlockInfo`s in tenure T at consecutive `chain_length`s, call `mark_locally_accepted(false)` on each (do **not** call `mark_globally_accepted`), and `insert_block` both.
2. Assert `signer_db.get_globally_accepted_block_count_in_tenure(&T) == 0` and `signer_db.has_signed_block_in_tenure(&T) == true` (demonstrating the equality break: the guard's count is 0 while the signer has actually signed 2 blocks).
3. Construct a `cur_sortition` whose `parent_tenure_id` differs from `prior_sortition`, mock the stacks client's `get_tenure_forking_info` response to return T with `first_block_mined: None` (as the node never saw T's blocks) followed by the built-upon parent tenure entry.
4. Call `cur_sortition.data.check_parent_tenure_choice(&mut signer_db, &client, &first_proposal_burn_block_timing)` and assert it wrongly returns `Ok(true)`, and that `signer_db.is_tenure_superseded(&T)` is `true` afterward — despite T having 2 signer-signed blocks.
5. (End-to-end variant) Follow up with a `get_signed_conflicts`/`reorg_permit_stands` check proving that a subsequent block proposal in the reorging tenure at T's block height is no longer blocked, i.e., the signer would sign a conflicting block.

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

**File:** stacks-signer/src/chainstate/mod.rs (L225-233)
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

**File:** stacks-signer/src/signerdb.rs (L1511-1516)
```rust
    pub fn has_signed_block_in_tenure(&self, tenure: &ConsensusHash) -> Result<bool, DBError> {
        let query = "SELECT 1 FROM blocks WHERE consensus_hash = ? AND (signed_self IS NOT NULL OR signed_group IS NOT NULL) LIMIT 1;";
        let result: Option<u64> = query_row(&self.db, query, [tenure])?;

        Ok(result.is_some())
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

**File:** stacks-signer/src/signerdb.rs (L1564-1572)
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

**File:** stacks-signer/src/signerdb.rs (L4009-4018)
```rust
        // locally accepted still returns 0
        block_info.mark_locally_accepted(false).unwrap();
        block_info.block.header.chain_length = 1;
        db.insert_block(&block_info).unwrap();

        assert_eq!(
            db.get_globally_accepted_block_count_in_tenure(&consensus_hash_1)
                .unwrap(),
            0
        );
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

**File:** stacks-signer/src/v0/signer.rs (L1374-1403)
```rust
        // tenures whose reorg we sanctioned under the reorg-timing rules are excluded, but
        // only while the sortition the permit was granted to is still canonical
        // (`check_parent_tenure_choice` records the permit, `reorg_permit_stands` re-derives
        // its validity from the node); every other question about whether a conflict is
        // still live is derived from the node in `conflict_still_blocks`.
        //
        // Unlike the chainstate check above, a refusal here is "for now" rather than a
        // broadcast rejection: a later pre-commit re-evaluation may still sign the block once
        // the conflicting signature has gone stale.
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
```

**File:** stacks-signer/src/chainstate/v1.rs (L496-518)
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
        let last_in_current_tenure = signer_db
            .get_last_globally_accepted_block(&block.header.consensus_hash)
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
```
