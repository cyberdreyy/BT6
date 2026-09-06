## Finding

The vulnerability is confirmed by tracing the exact code paths.

### Title
Tenure-level `mark_tenure_superseded` record voids a signer's own fresh, locally-accepted sibling signature that was never counted by the `globally_accepted_blocks > 1` reorg gate - ([File: stacks-signer/src/chainstate/mod.rs], [stacks-signer/src/signerdb.rs])

### Summary
`SortitionData::check_parent_tenure_choice` decides whether to permit a tenure reorg using a count that only includes `GloballyAccepted` blocks, but `record_superseded_tenure`/`mark_tenure_superseded` and the `get_signed_conflicts` exclusion operate at whole-tenure (`consensus_hash`) granularity. A signer that has locally (but not globally) signed a second, conflicting sibling block in the same tenure has that signature's conflict-guard silently stripped once the tenure is superseded, even though the reorg rule never examined that block.

### Finding Description
The gate is at [1](#0-0) , using `get_globally_accepted_block_count_in_tenure`, which is implemented as: [2](#0-1) 

This query filters `WHERE ... state = 'GloballyAccepted'` only - a `LocallyAccepted` sibling block is invisible to it, as the repo's own test confirms ("locally accepted still returns 0") at [3](#0-2) .

When the reorg is permitted, every reorged tenure is recorded wholesale via `record_superseded_tenure` → `mark_tenure_superseded`, keyed only by the tenure's `consensus_hash`: [4](#0-3) [5](#0-4) 

The `superseded_tenures` table has `consensus_hash TEXT PRIMARY KEY` - one row per tenure, not per block: [6](#0-5) .

`get_signed_conflicts` then joins on that same `consensus_hash`, so *every* block in the superseded tenure - not just the one `GloballyAccepted` block the ≤1 rule bounded - is annotated as excludable: [7](#0-6) 

Finally, in the pre-commit signing path, a *fresh* conflict is only enforced as a blocker when `reorg_permit_stands` is false: [8](#0-7) 

Exploit flow: the attacker (a single miner slot) proposes block B1 and block B2 at the same `stacks_height` in tenure T1. Due to the existing, intentional staleness relaxation (`tenure_last_block_proposal_timeout`), the target signer can end up having locally signed B2 first, then - after B2's conflict goes past the freshness window without reaching global acceptance - locally/globally sign B1, which reaches `GloballyAccepted`. T1 now has exactly one `GloballyAccepted` block (B1) and one `LocallyAccepted` sibling (B2), both signed by the same victim signer. The attacker then mines T2 reorging T1. `check_parent_tenure_choice` sees `globally_accepted_blocks == 1` (B2 doesn't count) and, if the first-block timing check also passes, permits the reorg and calls `mark_tenure_superseded(T1, ...)`. This single tenure-level record now also covers B2. If B2 was still fresh (recently re-signed, e.g. via a repeat proposal or timer reset) when a further conflicting proposal D arrives at the same height, the `reorg_permit_stands` check short-circuits the fresh-conflict guard at signer.rs:1403-1411, and the signer can be led to sign D without the guard recognizing that B2's fresh signature was never actually vetted by the ≤1 bound the reorg permission relied on.

### Impact Explanation
This breaks the signer's own equivocation-prevention invariant (chain safety / uniqueness): a fresh signature this signer placed on a conflicting sibling block can be swept out of consideration by a supersession record whose safety rationale (`globally_accepted_blocks <= 1`) never accounted for that block. This matches the Critical category: "a signer signing an invalid, non-canonical, or conflicting block (chain safety)" / "losing the equivocation guard." It is repeatable per tenure and requires only the attacker's own miner slot plus normal block-proposal gossip - no majority-signer collusion or privileged access.

### Likelihood Explanation
Requires: (1) the attacker win a miner slot to build T1; (2) get the victim signer to locally sign two distinct blocks at the same height in T1 across the `tenure_last_block_proposal_timeout` freshness boundary (an ordinary, documented mechanism, not privileged); (3) win the next miner slot to build T2 and satisfy the existing `first_proposal_burn_block_timing`/empty-tenure conditions in `check_parent_tenure_choice`. All of these are achievable with a single attacker-controlled miner slot and standard gossip of `BlockProposal`s; no majority signer weight or auth token is needed. The timing window needed to keep B2 within "fresh" while also getting it superseded requires careful but plausible sequencing.

### Recommendation
Make the exclusion granularity match the granularity of the safety bound: either (a) record the supersession per specific block (`signer_signature_hash`/`stacks_height`) rather than per tenure `consensus_hash`, so only the block(s) actually counted by `get_globally_accepted_block_count_in_tenure` are excluded from `get_signed_conflicts`, or (b) change `get_globally_accepted_block_count_in_tenure` (or add a companion check) to also count `LocallyAccepted`/signed-but-not-global siblings, and refuse to mark a tenure superseded whenever more than one distinct signed block exists in it, regardless of acceptance state.

### Proof of Concept
Rust test plan (extend `stacks-signer/src/chainstate/tests/v2.rs`, based on `check_parent_tenure_choice_reorg_timing_ok`):
1. Seed `signer_db` for tenure T1 with two blocks at the same `stacks_height`: block A marked `GloballyAccepted` (fresh `signed_group`), block B marked `LocallyAccepted` (fresh `signed_self`, distinct `signer_signature_hash`).
2. Call `cur_sortition.data.check_parent_tenure_choice(...)` with `tenures_reorged` including T1, timed to satisfy the "poorly-timed first block" branch (as in `check_parent_tenure_choice_reorg_timing_ok`).
3. Assert `result.unwrap() == true` (reorg permitted) and `signer_db.is_tenure_superseded(&T1) == true`.
4. Call `signer_db.get_signed_conflicts(height_of_A_and_B, &some_other_hash)` and assert that the returned `SignedConflictInfo` for block B (the `LocallyAccepted` one, never counted by `get_globally_accepted_block_count_in_tenure`) also carries `superseded_by.is_some()` - demonstrating that a signature outside the vetted ≤1 count is nonetheless excluded from future conflict checks.

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

**File:** stacks-signer/src/signerdb.rs (L757-776)
```rust
static CREATE_SUPERSEDED_TENURES_TABLE: &str = r#"
CREATE TABLE IF NOT EXISTS superseded_tenures (
    -- consensus hash of a tenure that a later tenure was permitted to reorg. Its sortition is
    -- still canonical -- unlike an orphaned tenure -- but the reorg rules
    -- (`first_proposal_burn_block_timing`) sanctioned replacing the blocks it built, so a
    -- signature we put over one of them must not stand in the way of that replacement.
    consensus_hash TEXT PRIMARY KEY,
    -- burn block height of the superseded tenure's sortition, used to age the record out
    burn_block_height INTEGER NOT NULL,
    -- consensus hash of the tenure that was permitted to do the reorg. The permit only means
    -- anything while this tenure's sortition is still canonical: if a burnchain fork orphans
    -- it, the reorg we sanctioned can no longer happen and the record stops excluding the
    -- superseded tenure's blocks from conflict checks.
    superseded_by_consensus_hash TEXT NOT NULL,
    -- burn block hash of the permitting tenure's sortition, used to ask the node whether that
    -- sortition is still canonical
    superseded_by_burn_block_hash TEXT NOT NULL,
    -- epoch seconds at which we permitted the reorg
    superseded_at INTEGER NOT NULL
) STRICT;"#;
```

**File:** stacks-signer/src/signerdb.rs (L1530-1541)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L1398-1421)
```rust
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
