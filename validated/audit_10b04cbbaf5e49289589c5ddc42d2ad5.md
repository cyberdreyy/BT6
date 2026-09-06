## Finding

### Title
Reorg permits are scoped to the *superseded tenure*, not the *specific replacement block* — a stale, block-content-agnostic permit lets a signer double-sign conflicting blocks at the same height - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
The BYONM bug's root cause was that a "containment" check (is this resolved path still inside the package's own directory?) was replaced by a much weaker check (does the path merely contain a `node_modules` component?), so a boundary meant to bind one specific resource ended up authorizing access far outside its intended scope. The stacks-signer reorg-permit mechanism has the same shape: a permit that is supposed to authorize *one specific replacement of one specific tenure* is recorded and later re-checked using a condition — "is the permitting sortition still canonical?" — that says nothing about which block eventually wins, and it is never re-scoped to the block that earned it. Once granted, the permit silently voids the anti-equivocation guard for every future proposal at that tenure's height, not just the one that triggered it.

### Finding Description
`SortitionData::check_parent_tenure_choice` (`stacks-signer/src/chainstate/mod.rs:170-291`) is invoked whenever a proposal's tenure-change payload builds off something other than the prior sortition. For each reorged tenure that "qualifies" (zero globally accepted blocks, or a first block proposed too close to the next sortition per `first_proposal_burn_block_timing`), the tenure is pushed onto `superseded_tenures` and, once the whole reorg clears the rules, `SignerDb::mark_tenure_superseded` is called [1](#0-0) . The record stores only `(consensus_hash, superseded_by_consensus_hash, superseded_by_burn_block_hash)` [2](#0-1)  — it binds the *tenure being replaced* to the *sortition that replaced it*, but never to the actual winning block.

That record is later consumed by `get_signed_conflicts`, which LEFT JOINs `superseded_tenures` purely on `consensus_hash` [3](#0-2) . Every block ever signed in the superseded tenure (not just the one whose proposal triggered the permit) is annotated with the permit, and `Signer::reorg_permit_stands` — invoked from the pre-commit conflict guard in `handle_block_pre_commit` — treats the conflict as excluded "while [the permitting] sortition remains canonical" [4](#0-3)  and [5](#0-4) . The permit thus persists (bounded only by `MAX_FORK_DEPTH` = 100 burn blocks, via `prune_superseded_tenures`) and is checked solely against "is sortition S still on the canonical burn chain?", never against "is the block currently reaching pre-commit threshold the one the permit was for?"

**Before vs after:** Before a permit exists, `get_signed_conflicts` correctly forces the signer to refuse a second, conflicting signature at the same height (the one-per-height / equivocation guard, tested explicitly by `signer_refuses_to_sign_second_sibling_tenure_start` in `stacks-signer/src/v0/tests.rs:770-789`). After a permit is granted for tenure A (because some sortition S legitimately, or by design-exploitable timing, qualified as a reorg of A), *any* later, unrelated proposal at A's height — in any tenure, proposed at any later time while S remains canonical — is no longer blocked by the signer's own earlier signature over A's block, because `reorg_permit_stands` unconditionally excludes it. The guard is supposed to answer "was this specific replacement sanctioned?" but instead answers the much broader question "did some sortition once qualify to replace this tenure?"

### Impact Explanation
This breaks the safety invariant enforced by section 5 of the signer state machine (`docs/signer-flows.md:229-347`): a signer must never place a second signature over a conflicting block at a height where it already signed, unless the specific replacement was sanctioned. Because the permit is content-agnostic and persists across unrelated future proposals, a single miner can cause a signer to sign two mutually-exclusive blocks at the same height — the classic signer-equivocation outcome this guard exists to prevent. If enough signers are fooled the same way, two conflicting blocks can each independently gather signer weight, directly matching the "Critical: a signer signing an invalid, non-canonical, or conflicting block" impact category.

### Likelihood Explanation
The precondition — a legitimately "poorly-timed" tenure reorg — is a normal, miner-triggerable event that requires no majority of signers, no other signer's key, and no node access: any single miner that wins a sortition shortly after another tenure's first (and only) block was proposed can cause every signer, independently and locally, to mark that tenure superseded via `check_parent_tenure_choice`. The permit is granted as a side effect of evaluating the reorg's *tenure choice*, not conditioned on the replacement block ultimately reaching consensus, so it can be earned even by a proposal that itself never gets signed. Once earned, exploiting it only requires a later, unrelated conflicting proposal at the same height while the permitting sortition (which already happened) remains canonical — a condition satisfied for up to 100 burn blocks by default.

### Recommendation
Scope the permit to the specific replacement, not merely to the superseded tenure and the permitting sortition's canonicity. `mark_tenure_superseded`/`get_signed_conflicts`/`reorg_permit_stands` should additionally verify that the block now reaching threshold (or already signed) at the superseded tenure's height is actually reachable from (or is) the sanctioned replacement chain — e.g., by checking that the proposal's ancestry passes through the specific sortition/tenure that was granted the permit — rather than accepting any conflicting proposal merely because that sortition still exists on the canonical burn chain.

### Proof of Concept
Conceptual reproduction, following the pattern of the existing sibling-conflict tests in `stacks-signer/src/v0/tests.rs`:
1. Tenure A mines and gets one block A1 globally accepted.
2. Sortition S occurs "poorly timed" relative to A1 (within `first_proposal_burn_block_timing`); a tenure-change block B1 for tenure B is proposed building off an ancestor other than A1. Each signer's `check_parent_tenure_choice` marks tenure A superseded-by-S and signs B1 (this exactly mirrors `check_parent_tenure_choice_reorg_timing_ok` in `stacks-signer/src/chainstate/tests/v2.rs:372-380`, which asserts the permit is recorded).
3. Later, while sortition S is still canonical, an unrelated miner proposes a different block B2 at A1's height (a different tenure than A or B, or a sibling in B unrelated to the sanctioned replacement).
4. `handle_block_pre_commit`'s conflict check calls `get_signed_conflicts`, which finds A1 annotated with the still-standing permit (`superseded_by_consensus_hash = S`); `reorg_permit_stands` returns true, excluding A1 as a conflict.
5. The signer, having already signed A1, now also signs B2 — a conflicting block at the same height with no sanctioned relationship to B2 — reproducing the equivocation the guard exists to prevent.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L200-232)
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
```

**File:** stacks-signer/src/signerdb.rs (L1611-1619)
```rust
        let query = "SELECT b.consensus_hash, b.signer_signature_hash, b.stacks_height, b.state,
                MAX(COALESCE(b.signed_self, 0), COALESCE(b.signed_group, 0)) AS last_endorsed,
                st.superseded_by_consensus_hash, st.superseded_by_burn_block_hash
            FROM blocks b
            LEFT JOIN superseded_tenures st ON st.consensus_hash = b.consensus_hash
            WHERE (b.signed_self IS NOT NULL OR b.signed_group IS NOT NULL)
                AND b.stacks_height >= ?1
                AND b.signer_signature_hash != ?2
            ORDER BY b.stacks_height DESC";
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

**File:** docs/signer-flows.md (L330-341)
```markdown
the replacement until the signature goes stale, whereas wrongly signing cannot be
taken back. The one recorded exception is a tenure whose reorg we sanctioned
under the reorg-timing rules (section 8): there the node still serves the
conflict as fully live — replacing it is only legitimate because we permitted it
— so no question asked of the node about the _conflict_ could clear it. Instead
the record carries the permitting tenure's sortition, and `reorg_permit_stands`
asks the node whether that sortition is still canonical: while it is, the
conflict is excluded outright; if a burnchain fork orphaned it, the reorg we
sanctioned can no longer happen and the conflict gets its voice back. A false
404 there needs no tip-height guard — it merely restores a conflict, which at
worst delays the replacement. For the own-tenure question below, an unreachable
node is instead treated as unconfirmed and the signature goes out.
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
