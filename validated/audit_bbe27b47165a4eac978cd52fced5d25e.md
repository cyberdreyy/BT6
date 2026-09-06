### Title
Signer honors a stale reorg permit for any competing tenure, not just the one it was granted to, allowing a double-sign on conflicting blocks - (File: stacks-signer/src/signerdb.rs, stacks-signer/src/v0/signer.rs)

### Summary
The wger advisory shows a permission flag (`trainer.identity`) that is set for one narrow, legitimate action but is later trusted, without re-validating the original justification or scoping it to the specific action it was granted for, to authorize a completely different, unauthorized action. The stacks-signer's "reorg permit" mechanism has the same structural flaw: a permit granted to let one specific successor tenure (`B`) replace a superseded tenure (`A`) is recorded keyed only by `A`'s `consensus_hash`, and is later honored for *any* block that conflicts with `A`, not just the specific successor tenure `B` for which the reorg-timing rules were actually evaluated.

### Finding Description
When a miner proposes a tenure-change block that legitimately reorgs a prior tenure under the reorg-timing rules (`first_proposal_burn_block_timing`), the signer records the superseded tenure via `SignerDb::mark_tenure_superseded`, keyed only by the superseded tenure's `consensus_hash`: [1](#0-0) 

This uses `INSERT OR REPLACE`, so the record for tenure `A` stores exactly one `superseded_by_consensus_hash`/`superseded_by_burn_block_hash` pair (the sortition of whichever tenure most recently earned the permit).

At pre-commit signing time, `get_signed_conflicts` looks up all signed blocks at or above a height, across *any* tenure, and left-joins in the superseded-tenure record purely by the conflicting block's own `consensus_hash`: [2](#0-1) 

`Signer::reorg_permit_stands` then decides whether to exclude that conflict, based solely on whether the *permitting* sortition (`superseded_by.burn_block_hash`) is still canonical — it never checks whether the block currently being evaluated for signing is actually part of that permitting tenure `B`: [3](#0-2) 

The pre-commit signing path filters out any conflict for which `reorg_permit_stands` returns true, before running `conflict_still_blocks`: [4](#0-3) 

Consequence: once tenure `A` has been legitimately superseded by tenure `B` (a one-time, narrowly-scoped decision made by `check_parent_tenure_choice`), the exclusion is not scoped to blocks belonging to `B`. Any later, unrelated proposal `C` — from a different tenure/miner slot, submitted without ever satisfying the reorg-timing rules itself — that happens to conflict with an already-signed block in `A` at the same or higher height will also have that conflict silently waved away by `reorg_permit_stands`, as long as `B`'s permitting sortition remains canonical. The signer therefore treats a permit granted for a specific, vetted reorg (`A→B`) as a blanket authorization to sign over `A`'s history for *any* subsequent chain (`A→C`), exactly mirroring the wger flaw where a session flag set for one narrow trainer-login hop was accepted as authorization for an entirely different, unvetted hop.

This breaks the "one-per-height" / conflicting-signature equality the pre-commit conflict guard exists to enforce (see `docs/signer-flows.md` sections 5 and 8, which explicitly document that the guard exists "to stop us endorsing two blocks that could both end up in the chain"): [5](#0-4) [6](#0-5) 

### Impact Explanation
A signer can end up placing signatures over two mutually-conflicting blocks at the same Stacks height in different, unrelated tenures — a double-sign/equivocation — because a stale, mis-scoped permit intended for one sanctioned reorg is honored for a different competing chain. This is a Critical-class break (a signer signing a conflicting block), matching the rules' definition of "a signer signing an invalid, non-canonical, or conflicting block."

### Likelihood Explanation
Reachable by a single miner/proposer plus ordinary gossip: any miner can legitimately trigger `check_parent_tenure_choice`'s reorg-timing rules once (creating tenure `B` that supersedes `A`), and then — or a different miner in a later slot — can propose a competing tenure `C` off the same reorg point. No majority collusion, no other signer's key, and no node-side change is required; the flaw is purely in how the signer's local conflict-exclusion logic keys and re-validates the permit.

### Recommendation
Scope the reorg-permit exclusion to the specific permitting tenure, not just the superseded tenure's identity: `get_signed_conflicts`/`reorg_permit_stands` should only exclude a conflict in tenure `A` when the block currently being evaluated for signing actually descends from (or is) the recorded `superseded_by_consensus_hash` tenure `B`. If a different tenure `C` conflicts with `A`, its own eligibility to reorg `A` must be independently evaluated against `check_parent_tenure_choice`'s reorg-timing rules rather than inheriting `B`'s previously-granted permit.

### Proof of Concept
1. Miner wins tenure `A`, mines and gets globally accepted at height `h` a tenure-start block, satisfying "at most one globally accepted block" and produced close enough to the next sortition per `first_proposal_burn_block_timing`.
2. Miner (or a new miner) wins the next sortition and proposes tenure `B`'s tenure-change block reorging `A`. `check_parent_tenure_choice` sanctions this, and `SignerDb::mark_tenure_superseded(A, ..., superseded_by=B, ...)` is recorded.
3. Signers sign `B`'s block at height `h`; `A`'s conflicting block is now excluded from `get_signed_conflicts` filtering via `reorg_permit_stands` as long as `B`'s sortition stays canonical.
4. Before `B`'s permitting sortition is itself invalidated, a different, unrelated tenure `C` (from another miner slot, gossip-relayed) proposes a competing tenure-change block also reorging `A` at height `h`, but without itself satisfying `first_proposal_burn_block_timing` (e.g., built long after the cutoff).
5. During `C`'s pre-commit evaluation, `get_signed_conflicts` still finds `A`'s signed block as a conflict at height `h`, and `reorg_permit_stands(conflict_in_A)` still returns `true` (because `B`'s sortition, not `C`'s legitimacy, is what's checked) — the conflict is excluded and the signer proceeds to sign `C`'s block, producing a second signature at height `h` in a chain the reorg-timing rules never actually sanctioned.

Note: I was unable to directly inspect `check_parent_tenure_choice`'s full implementation (chainstate/v1.rs / v2.rs) in this session to confirm the exact call site invoking `mark_tenure_superseded`, so the precise trigger conditions for step 2 should be verified against that function before finalizing a fix.

### Citations

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

**File:** stacks-signer/src/v0/signer.rs (L1368-1403)
```rust
        // A pre-commit may be superseded by a competing proposal at the same height (e.g. a
        // re-proposed tenure-start block after the first failed to reach consensus), but a
        // signature must not be superseded while it's still "fresh". A signed block at the
        // same or higher height in ANY tenure is a conflict: two blocks at the same height are
        // siblings no matter which tenure they belong to (e.g. the next tenure's tenure-start
        // block conflicts with the current tenure's block at the same height). Blocks in
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

**File:** docs/signer-flows.md (L496-511)
```markdown
One decision does have to be recorded, because it is ours rather than the
node's. When a miner builds off something other than the prior sortition,
`check_parent_tenure_choice` decides whether the reorg is allowed: it is, if
every tenure being reorged has at most one globally accepted block and produced
its first block too close to the next sortition to count
(`first_proposal_burn_block_timing`). Having sanctioned that replacement, the
signer records those tenures as **superseded** (`mark_tenure_superseded`), so its
own signature over what they built does not then block the replacement it just
permitted — the node cannot answer this one at signing time, since it still
serves the reorged tenure as fully live until the replacement lands. What _is_
still derived from the node is the permit's own validity: the record carries the
permitting tenure's sortition, and it only excludes conflicts while that
sortition remains canonical (section 5, `reorg_permit_stands`), so a burnchain
fork that orphans the permitting tenure automatically voids the permit. A record
more than `MAX_FORK_DEPTH` (100) burn blocks below the tip is dropped; a fork
that deep would cause far bigger problems than a stale conflict.
```

**File:** docs/signer-flows.md (L1108-1136)
```markdown

```
