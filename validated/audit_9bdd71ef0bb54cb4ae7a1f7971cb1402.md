### Title
Reorg-permit exclusion applies to any conflicting block, not only the tenure it was actually granted to, letting a signer double-sign siblings at the same height - (File: stacks-signer/src/v0/signer.rs, stacks-signer/src/signerdb.rs)

### Summary
The reorg-permit mechanism that lets a signature over a superseded tenure stop blocking its legitimate replacement is not bound to the specific replacing block. `reorg_permit_stands` only checks whether the *permitting sortition* is still canonical, never whether the block currently reaching the pre-commit threshold is the one that tenure was actually permitted to replace. Any other conflicting block at the same height can ride the same stale permit and get signed, producing two signer-signed blocks at the same Stacks height (a double-sign / equivocation).

### Finding Description
When a miner's new tenure `C` reorgs off an earlier point than the prior sortition, `SortitionData::check_parent_tenure_choice` decides whether that reorg is allowed and, if so, calls `SignerDb::mark_tenure_superseded(consensus_hash = A, superseded_by_consensus_hash = C, superseded_by_burn_block_hash = C's burn hash)` for every tenure `A` it reorgs away. [1](#0-0) 

This is recorded keyed only by the *superseded* tenure's consensus hash — it says nothing about which future proposal is entitled to use the exemption: [2](#0-1) [3](#0-2) 

Later, whenever *any* block reaches the pre-commit threshold, `get_signed_conflicts` looks up every previously-signed block at or above its height, in *every* tenure, and left-joins in the `superseded_tenures` row purely by `consensus_hash` — i.e. by the identity of the old conflicting tenure `A`, with no reference at all to the block currently being evaluated: [4](#0-3) 

The exclusion is then applied in `handle_block_pre_commit` via `reorg_permit_stands`, whose signature takes only the `conflict` (i.e. tenure `A`'s record) — never the block/tenure that is actually about to be signed: [5](#0-4) [6](#0-5) 

So the equality that should hold is: *"a conflict against tenure A is excluded only when the block being signed belongs to the specific tenure C that was sanctioned to replace A."* What is actually checked is only: *"is C's sortition still canonical?"* — with no check that the block presently crossing the pre-commit threshold is `C`'s block at all. Once a legitimate permit for `A → C` exists (a normal, non-malicious outcome of the "poorly-timed tenure" exception in `check_parent_tenure_choice`) and `C`'s sortition remains canonical, the conflict against `A` is excluded for *every* future proposal at or above `A`'s height, including an entirely unrelated sibling tenure `B` that a miner (the same one-slot miner, or a colluding one) mines as a fork of `A`. The double-sign guard (`get_signed_conflicts` / `conflict_still_blocks`) exists precisely to stop the signer set from signing two siblings at the same height; this permit-reuse bypasses it for any conflict that happens to already have an outstanding, still-canonical `superseded_tenures` row.

### Impact Explanation
This breaks the anti-equivocation guarantee described in `docs/signer-flows.md` §5/§8: "the guard exists to stop us endorsing two blocks that could both end up in the chain." A signer can end up producing signatures over two conflicting (same-height, different-tenure) blocks, `A` and `B`, once a permit for an unrelated `A → C` reorg is outstanding — a signer signing a conflicting/non-canonical block, which is a Critical-severity outcome under this program's rubric (equivocation / double-sign).

### Likelihood Explanation
No majority of signers or another signer's key is required. The permit for `A → C` is created by the ordinary, documented reorg-timing exception (`check_parent_tenure_choice`), which a single miner can trigger by deliberately mining a "poorly timed" first block in tenure `A` just before losing the next sortition to itself or a colluding miner running tenure `C`. Once that legitimate permit exists and `C` remains canonical, the exploit only needs a second, ordinary sibling proposal `B` at `A`'s height to reach the 70% pre-commit threshold — something a one-slot miner (plus normal gossip of pre-commits) can arrange, with no cooperation from a signer majority.

### Recommendation
Bind the permit exclusion to the block actually being evaluated: `reorg_permit_stands` (or its caller) should additionally verify that the block/tenure currently reaching the pre-commit threshold is the one recorded in `superseded_by_consensus_hash` for that conflict (e.g. compare `block_info.block.header.consensus_hash` against `conflict.superseded_by.consensus_hash`), not merely that the permitting sortition remains canonical. Any conflict whose superseding tenure does not match the tenure of the block being signed must fall through to the normal freshness/`conflict_still_blocks` checks.

### Proof of Concept
1. Miner mines tenure `A`'s only block very close to the next sortition (satisfying `first_proposal_burn_block_timing`), and it gets locally/globally accepted; signer signs it (`signed_self` set).
2. The same or a colluding miner wins the next sortition as tenure `C`, deliberately builds off a point before `A` (a reorg). `check_parent_tenure_choice` permits it (`A`'s block was "poorly timed"), calling `mark_tenure_superseded(A, ..., superseded_by=C, ...)` (`stacks-signer/src/chainstate/mod.rs:259-273`, `stacks-signer/src/signerdb.rs:1642-1660`).
3. While `C`'s sortition is still canonical, an attacker (or the same miner) proposes an unrelated sibling block `B` at the same Stacks height as `A`, in a tenure that has nothing to do with `C`. `B`'s proposal is validated and pre-committed by the signer set.
4. When `B` crosses the 70% pre-commit threshold, `handle_block_pre_commit` calls `get_signed_conflicts`, which returns `A` as a conflict annotated with `superseded_by = C` (`signerdb.rs:1606-1625`, purely joined on `A`'s consensus hash).
5. `reorg_permit_stands(conflict=A)` only asks whether `C`'s sortition is canonical (`signer.rs:1222-1247`) — true — so the conflict against `A` is excluded even though `B` is not `C`. The signer proceeds to sign `B` (`signer.rs:1403-1421`), even though it already signed `A` at the same height, producing a double-signed pair `(A, B)`.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L200-233)
```rust

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

**File:** stacks-signer/src/v0/signer.rs (L1403-1421)
```rust
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
