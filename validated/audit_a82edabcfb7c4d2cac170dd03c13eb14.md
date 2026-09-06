### Title
Reorg-permit "at most one signed block" check counts only node-confirmed (`GloballyAccepted`) blocks, letting a miner get a signer to sign a conflicting replacement over blocks the signer set already fully signed - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`check_parent_tenure_choice` is meant to forbid a miner from reorging away a tenure that already has meaningful signed history, allowing it only when the reorged tenure produced "at most one already-signed block" or was poorly timed. The actual guard, however, is implemented against `get_globally_accepted_block_count_in_tenure`, which only counts blocks whose state has advanced to `GloballyAccepted` (i.e. the *node* has already processed/announced them) — not blocks that the signer set has already fully signed (`signed_group`) but that are still sitting at `LocallyAccepted`/`PreCommitted` because the node hasn't caught up yet. This node-processing lag is a normal, single-miner-controllable window (block push/broadcast/processing time), during which a competing tenure can be granted a "reorg permit" that voids the conflict-guard over blocks that were, in reality, already signed by the whole signer set — leading this signer to later sign a conflicting replacement block.

### Finding Description
The relevant guard lives in `SortitionData::check_parent_tenure_choice`: [1](#0-0) 

It relies on `signer_db.get_globally_accepted_block_count_in_tenure`, and only refuses the reorg `if globally_accepted_blocks > 1`. If the tenure's `first_block_mined` (as reported by the *node*, via `client.get_tenure_forking_info`) is `None`, the branch below treats the tenure as if it produced nothing worth protecting and marks it superseded outright: [2](#0-1) 

But "globally accepted" is a strictly narrower notion than "signed." The signer's own test suite makes this explicit — `mark_locally_accepted` (which fires as soon as this signer places its own signature, or once the 70% signature threshold is reached across the whole set via `signed_group`) does **not** increase the globally-accepted count; only `mark_globally_accepted`, which requires the *node* to have actually processed the block (via a `NewBlock` event or `check_latest_block_in_tenure` confirming the tip), does: [3](#0-2) 

This mirrors the state machine documented in `docs/signer-flows.md`: reaching the 70% threshold only marks a block `LocallyAccepted` (with `signed_group`) — "global acceptance waits for the node to adopt it": [4](#0-3) 

The consequence of a permitted reorg is severe: once `check_parent_tenure_choice` approves it, the whole reorged tenure is recorded as superseded via `mark_tenure_superseded`, and **every** block previously signed in that tenure stops counting as a conflict for this signer, regardless of how many blocks were actually signed: [5](#0-4) 

`get_signed_conflicts` looks this record up per conflicting block, and `reorg_permit_stands` only re-validates that the *permitting* sortition is still canonical — it never re-derives whether the superseded tenure actually had only 0-1 signed blocks: [6](#0-5) 

Once a conflict is excluded via a standing permit, the pre-commit threshold path proceeds straight to signing the replacement, bypassing the very guard designed to stop a signer from placing two signatures over conflicting blocks at overlapping heights: [7](#0-6) 

**Attack path (single miner, no other signer cooperation needed):** A miner wins a tenure T and rapidly proposes several sequential blocks. Because signing and broadcasting happen faster than the node processing/announcing each block as its canonical tip (or the miner deliberately withholds/delays pushing the fully-signed blocks to the node), the signer set can reach the 70% signature threshold on 2+ blocks in T while the signer's local node still reports `first_block_mined = None` for T (nothing observed yet) and this signer's local DB still shows 0 `GloballyAccepted` blocks for T. The miner then immediately starts a new tenure U on a different/competing sortition that does not build on T. `check_parent_tenure_choice` sees `globally_accepted_blocks == 0` and `first_block_mined == None` from the node's perspective, and grants the reorg permit, marking T superseded. This voids the conflict status of the already-signed (`signed_group`) blocks in T. When U's replacement block reaches the pre-commit threshold, `reorg_permit_stands` finds the permit still valid (U's sortition is canonical) and the signer signs U's block — producing two blocks with full-set signatures that conflict for overlapping chain history.

### Impact Explanation
This breaks the exact safety property the pre-commit conflict guard and freshness/liveness checks exist to preserve: "a signer must not place a second signature over a conflicting block once it (or the group) has already signed one." Here, the network can fully sign multiple blocks of a tenure, yet the local accounting used to grant reorg permits treats that tenure as effectively empty, causing the signer to sign a competing, conflicting block. This is a Critical-class outcome per the given rubric — a signer signing a conflicting block — achievable by a single miner exploiting the ordinary node-processing/broadcast lag, without needing a majority of signers, another signer's key, or the auth_token.

### Likelihood Explanation
The node-processing lag between "signed_group reached" and "GloballyAccepted" is a routine, always-present timing window (block push + node validation + tip advancement), not a rare edge case — the same lag is explicitly exploited elsewhere in this same codebase's sibling-race tests (`stacks-signer/src/v0/tests.rs`, `async_sibling_validation` module) to demonstrate analogous timing gaps. A miner fully controls block production pace within its own tenure and can trivially widen this window (e.g., by not pushing the block for upload immediately, or by producing several blocks back-to-back before the node/other signers have finished processing the first).

### Recommendation
Change `check_parent_tenure_choice`'s "at most one already-produced block" rule to count blocks the signer set has actually *signed* (e.g., `signed_self`/`signed_group` set, or `has_signed_block_in_tenure`-style query) rather than only `GloballyAccepted` blocks, and cross-check this local signed count against the node-reported `first_block_mined`/fork info so that a locally-known signed block that the node simply hasn't caught up on is not silently treated as "no block ever produced." The reorg permit should never be granted for a tenure this signer (or the group) has already placed 2+ signatures over, regardless of node confirmation status.

### Proof of Concept
1. Miner wins sortition for tenure T; proposes block T1, gets it signed by the full signer set (`signed_group` set, state `LocallyAccepted`), but withholds/delays the corresponding node push so the stacks-node never processes T1 as its tip.
2. Miner proposes a second block T2 in T (builds on T1); it is likewise fully signed (`signed_group`), still withheld from the node.
3. At this point, `get_globally_accepted_block_count_in_tenure(T) == 0` and, from the node's viewpoint via `get_tenure_forking_info`, `first_block_mined == None` for T (see `stacks-signer/src/chainstate/mod.rs` lines 205-233).
4. Miner abandons T and starts tenure U on a sibling/competing sortition not building on T.
5. `check_parent_tenure_choice` evaluates U's proposal, finds no globally-accepted blocks and no node-known mined blocks for T, and calls `signer_db.mark_tenure_superseded(T, ..., superseded_by=U, ...)` (`stacks-signer/src/signerdb.rs` lines 1642-1660).
6. U's block reaches the pre-commit threshold; `get_signed_conflicts` returns T1/T2 as conflicts, but `reorg_permit_stands` finds the permit valid (U's sortition canonical) and excludes them (`stacks-signer/src/v0/signer.rs` lines 1208-1403).
7. The signer proceeds to sign U's block, producing a signer-set-level double-signature scenario: T1/T2 (fully signed by the whole set) and U's block (also signed to threshold) both carry valid signatures for conflicting chain histories.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L205-223)
```rust
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

**File:** stacks-signer/src/signerdb.rs (L3994-4053)
```rust
    #[test]
    fn check_globally_signed_block_count() {
        let db_path = tmp_db_path();
        let consensus_hash_1 = ConsensusHash([0x01; 20]);
        let mut db = SignerDb::new(db_path).expect("Failed to create signer db");
        let (mut block_info, _) = create_block_override(|b| {
            b.block.header.consensus_hash = consensus_hash_1.clone();
        });

        assert!(matches!(
            db.get_globally_accepted_block_count_in_tenure(&consensus_hash_1)
                .unwrap(),
            0
        ));

        // locally accepted still returns 0
        block_info.mark_locally_accepted(false).unwrap();
        block_info.block.header.chain_length = 1;
        db.insert_block(&block_info).unwrap();

        assert_eq!(
            db.get_globally_accepted_block_count_in_tenure(&consensus_hash_1)
                .unwrap(),
            0
        );

        block_info.mark_globally_accepted().unwrap();
        block_info.block.header.chain_length = 2;
        db.insert_block(&block_info).unwrap();

        block_info.block.header.chain_length = 3;
        db.insert_block(&block_info).unwrap();

        assert_eq!(
            db.get_globally_accepted_block_count_in_tenure(&consensus_hash_1)
                .unwrap(),
            2
        );

        // add an unsigned block
        block_info.signed_group = None;
        block_info.block.header.chain_length = 4;
        db.insert_block(&block_info).unwrap();

        assert_eq!(
            db.get_globally_accepted_block_count_in_tenure(&consensus_hash_1)
                .unwrap(),
            3
        );

        // add a locally signed block
        block_info.state = BlockState::LocallyAccepted;
        block_info.block.header.chain_length = 5;
        db.insert_block(&block_info).unwrap();

        assert_eq!(
            db.get_globally_accepted_block_count_in_tenure(&consensus_hash_1)
                .unwrap(),
            3
        );
```

**File:** docs/signer-flows.md (L365-372)
```markdown
    GRP -- no --> TALLY{"signature weight ≥ 70%?"}
    TALLY -- no --> N2(["wait for more"])
    TALLY -- yes --> BCAST["mark_locally_accepted(group),<br/>broadcast_signed_block →<br/>handle_post_block (push to node)"]:::good
    KIND -- "Rejected" --> HBR["handle_block_rejection:<br/>verify, store via<br/>add_block_rejection_signer_addr"]
    HBR --> RT{"rejection weight makes<br/>70% approval impossible?"}
    RT -- no --> N3(["wait"])
    RT -- yes --> GREJ["mark_globally_rejected;<br/>pre-global-state versions also<br/>update miner status"]:::bad
    BCAST --> NB["node processes block →<br/>NewBlock event →<br/>mark_globally_accepted"]:::good
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
