## Analysis

The external report describes a class of bug where an authorization check verifies the *wrong entity* — the contract checks "is this the strict owner" (or, in reverse, "was *any* address ever approved") instead of checking that the specific actor in front of it is the one that was actually authorized for *this* action. The reachable analog in this repo is in the stacks-signer's reorg-permit / conflict-suppression logic: `reorg_permit_stands` verifies that *some* previously-recorded permitting sortition is still canonical, without ever checking that the permitting sortition is the *same* tenure as the block currently being signed.

### Root cause

`check_parent_tenure_choice` records a reorg permit keyed only by the **reorged** tenure's consensus hash, naming whichever tenure most recently justified overriding it: [1](#0-0) 

That permit is later consumed in the pre-commit signing path. `get_signed_conflicts` returns *every* signed block at or above the proposed height, across **any** tenure, annotated with whatever `superseded_by` record exists for its own tenure: [2](#0-1) 

`reorg_permit_stands` then decides whether to exclude that conflict purely by asking whether the *recorded permitting sortition* is still canonical — it never compares that permitting sortition to the tenure of the block actually being evaluated for a signature right now: [3](#0-2) 

And the call site applies this exclusion generically to whichever `block_info` is currently at the pre-commit threshold, with no re-binding to the permitting tenure: [4](#0-3) 

### The broken equality

The intended invariant (per the design doc) is: *"our signature must not block a replacement **we sanctioned**"* — i.e., the exclusion should only fire for the specific tenure that `check_parent_tenure_choice` actually approved to replace the conflicting tenure: [5](#0-4) 

But the code checks "is the sanctioned tenure X still canonical?" instead of "is X the same tenure as the one I'm about to sign for?" If a second, different tenure `Z` later also builds on top of the same reorged predecessor `Y` and reaches the pre-commit threshold for a conflicting block, `reorg_permit_stands` will still return `true` and silently exclude `Y`'s conflict from blocking — as long as the unrelated, previously-sanctioned tenure `X` (not `Z`) remains canonical. This is analogous to the ERC20 report's flaw: the guard authenticates "was *some* address approved," not "is *this* the approved address," letting an unauthorized/unvetted actor benefit from someone else's authorization.

### Title
Reorg-permit exclusion in `reorg_permit_stands` checks liveness of the recorded permitting tenure instead of binding it to the tenure currently being signed — ([File: stacks-signer/src/v0/signer.rs])

### Summary
`reorg_permit_stands` excludes a signed conflict from blocking a new signature whenever *any* previously-recorded reorg permit for that conflict's tenure is still canonical, without verifying that the permit was granted to the specific tenure whose block is currently at the pre-commit threshold.

### Finding Description
`check_parent_tenure_choice` records, per reorged tenure `Y`, only the identity of the tenure `X` that was vetted and sanctioned to replace it (`mark_tenure_superseded`, keyed by `Y`'s consensus hash). `get_signed_conflicts` surfaces this record for any query touching `Y`. `reorg_permit_stands` (`stacks-signer/src/v0/signer.rs:1222-1247`) is invoked from `handle_block_pre_commit`'s conflict scan (`stacks-signer/src/v0/signer.rs:1403-1421`) purely with the `conflict` (i.e. information about `Y`) — it never receives or checks the tenure of the block currently being evaluated for signature. It answers only "is `X`'s sortition still canonical?" If yes, it returns `true` and `Y`'s conflict is excluded from blocking *regardless of which tenure is currently being signed*. Consequently, a competing tenure `Z` that itself was never checked by `check_parent_tenure_choice` against `Y` (or that failed that check) can still benefit from `X`'s pre-existing, unrelated, still-canonical permit and get signed despite conflicting with a previously-signed block in `Y`.

### Impact Explanation
This weakens the equivocation/no-double-sign guard described in `stacks-signer/src/v0/signer.rs:1110-1136` and `docs/signer-flows.md:288-341`: a signer could produce a valid signature over a second, conflicting block at the same or overlapping height as a block it already signed, where the replacement was never actually vetted for *that specific* tenure. This falls under the "losing the equivocation guard" / "signing a conflicting block" impact categories.

### Likelihood Explanation
This requires a genuine competing-tenure situation (two different tenures both attempting to build past the same reorged predecessor), which can arise from ordinary Bitcoin/Stacks fork activity rather than requiring a signer majority or key compromise — each side is driven by an independent miner winning its own sortition slot. The signer-local logic itself does nothing to prevent the stale/unrelated permit from being reused across tenures once it exists.

### Recommendation
`reorg_permit_stands` (and the `SupersededBy` record) should carry/verify that the permitting tenure `X` is the ancestor (or the same tenure) of the block currently being pre-committed, not merely that `X`'s sortition remains canonical in isolation. The exclusion should be recomputed to require that the block currently being signed for actually descends from the sanctioned tenure `X`, not just that some permit exists and its sortition hasn't been orphaned.

### Proof of Concept
Not executed — this is a logic-path analysis of `reorg_permit_stands`/`get_signed_conflicts`/`check_parent_tenure_choice`; reproducing it requires orchestrating two competing tenures through the existing `run_cross_tenure_scenario` test harness in `stacks-signer/src/v0/tests.rs:1193-1263`, extended so the currently pre-committing block belongs to a tenure `Z` distinct from the recorded `superseded_by` tenure `X`, and confirming the signer still signs.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L297-315)
```rust
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

**File:** docs/signer-flows.md (L331-341)
```markdown
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
