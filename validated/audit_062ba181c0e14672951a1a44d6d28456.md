Based on the reorg-permit exemption logic in the signer's pre-commit signing path, there is a real analog to the Sentry bug class (a token/permit granted for one specific context being reusable in an unrelated context because the check that consumes it doesn't verify the caller is the intended party).

### Title
Reorg-permit exclusion in the pre-commit signing guard is not scoped to the tenure it was granted to, letting a stale permit exempt an unrelated conflicting block and cause a double-sign - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`reorg_permit_stands` only checks whether the *permitting* tenure's sortition (`conflict.superseded_by`) is still canonical. It never checks that the block currently being evaluated for signature (`block_info`) is actually that permitting tenure (or descends from it). Because `mark_tenure_superseded`/`get_signed_conflicts` record and surface the permit per *superseded* tenure only (not per specific replacement pair), any later, unrelated proposal that happens to conflict with the same superseded block can "borrow" the permit and get signed, producing two signatures over conflicting blocks at the same height.

### Finding Description
When a signer is about to place a signature after reaching the pre-commit threshold, it re-checks for signed conflicts at or above the proposed height across *any* tenure via `get_signed_conflicts`: [1](#0-0) 

Each conflict row is left-joined against `superseded_tenures` purely on the conflict's own `consensus_hash`, independent of who is asking: [2](#0-1) 

The permit itself is recorded when a reorg is sanctioned between exactly two tenures — the "permitting" one and the "superseded" one — via `check_parent_tenure_choice` / `record_superseded_tenure` / `mark_tenure_superseded`: [3](#0-2) [4](#0-3) 

But when the fresh-conflict guard runs at signing time, `reorg_permit_stands` validates only that the *permitting* sortition is still canonical — it never checks that `block_info` (the block about to be signed) actually belongs to, or descends from, that permitting tenure: [5](#0-4) 

This function is consumed inside the fresh-conflict-blocks-signing check with no such binding either: [6](#0-5) 

So the logic is: refuse to sign only if *some* conflict is fresh AND the permit does *not* stand AND the conflict is still live. If a permit was granted at some earlier point for tenure A (superseded by tenure B) and B's sortition is still canonical, then *any* later proposal — even one from a completely unrelated tenure C that has nothing to do with B — that conflicts with A's already-signed block at the same height sees that specific conflict entry excluded by `reorg_permit_stands`, regardless of whether C is B. The only other guard that references `conflict.consensus_hash` at all is the *same-tenure* branch further down, which only fires when `conflict.consensus_hash == block_info.block.header.consensus_hash` — i.e., it doesn't help when C ≠ A: [7](#0-6) 

The docs describe the intended design (the permit should only excuse the replacement it sanctioned) but the code enforces only "is the permitting sortition still canonical," not "is this the block that permit was granted for": [8](#0-7) 

### Impact Explanation
This breaks the "one-per-height" equivocation guard that `get_signed_conflicts`/`conflict_still_blocks`/`reorg_permit_stands` exist to enforce (per the docs' own framing, quoted above: "our signature must not stand in the way of a replacement we sanctioned" — but the code lets it stand in the way of *any* replacement, sanctioned or not, once B remains canonical). A single signer can end up placing `signed_self` over two conflicting blocks at the same Stacks height (one in tenure A, one in unrelated tenure C), which is exactly the double-sign scenario the whole pre-commit conflict-guard subsystem (sections 5/8 of `docs/signer-flows.md`) was built to prevent. This matches the "Critical" bucket in scope: a signer signing a conflicting block.

### Likelihood Explanation
Reaching this requires only naturally-occurring signer-set state plus a miner (with gossip) crafting a competing tenure/proposal at the right height while a previously-granted, still-live permit (from an earlier, unrelated reorg-timing decision) happens to cover a block at that same height. No majority of signers, no other signer's key, and no auth token are needed — a single winning miner who can trigger a sortition fork at the right time can arrange for this state to exist (a stale permit from a past benign reorg staying "canonical" for a while) and propose a conflicting block that piggybacks on it. This is a timing/state-dependent bug rather than a trivially always-on one, so likelihood is moderate but concretely reachable.

### Recommendation
`reorg_permit_stands` (and/or `get_signed_conflicts`) should verify that the block currently under evaluation is actually the permitting tenure (or a block that descends from it), not merely that the permitting tenure's sortition remains canonical. Concretely, compare `block_info.block.header.consensus_hash` (or its ancestry) against `conflict.superseded_by.consensus_hash` before allowing the permit to exclude a conflict, so a permit granted for tenure B replacing tenure A cannot be reused to let an unrelated tenure C also replace/ignore A's signature.

### Proof of Concept
1. Tenure A produces a block at height h; the signer set signs it (`signed_self`/`signed_group` set).
2. Tenure B later builds off a prior sortition in a way that reorgs A; `check_parent_tenure_choice` sanctions this (A's block was "poorly timed"), and `mark_tenure_superseded(A, ..., superseded_by=B)` is recorded.
3. B's sortition stays canonical for a while (normal chain progression keeps it valid), so `reorg_permit_stands` keeps returning `true` for any conflict whose `consensus_hash == A`.
4. Before A's signature times out (`freshness_cutoff`), a different tenure C (unrelated to B) proposes a block at height h that conflicts with A. When this reaches pre-commit threshold, `get_signed_conflicts` returns A's conflict with `superseded_by = B`; `reorg_permit_stands` returns `true` (B is canonical) even though the current proposal is from C, not B.
5. The fresh-conflict-blocks check in `handle_block_pre_commit` (lines 1403-1421) finds no blocking conflict and proceeds to `mark_locally_accepted`/sign C's block — the same signer now holds signatures over both A's and C's conflicting blocks at height h.

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

**File:** stacks-signer/src/v0/signer.rs (L1423-1457)
```rust
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
            match stacks_client.get_tenure_tip(&block_info.block.header.consensus_hash) {
                Ok(tip) => {
                    let tip_height = tip.anchored_header.height();
                    if tip_height >= block_info.block.header.chain_length {
                        warn!(
                            "{self}: Reached the pre-commit threshold for a block that conflicts with previously signed or accepted blocks, and the canonical tip of its tenure is already at or above the proposed height. Refusing to sign.";
                            "signer_signature_hash" => %block_hash,
                            "block_height" => block_info.block.header.chain_length,
                            "canonical_tip_height" => tip_height,
                        );
                        return;
                    }
                }
                Err(e) => {
                    warn!(
                        "{self}: Failed to fetch the canonical tip of the proposed block's tenure: {e:?}. Treating the tenure as unconfirmed.";
                        "signer_signature_hash" => %block_hash,
                        "consensus_hash" => %block_info.block.header.consensus_hash,
                    );
                }
            }
        }
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
