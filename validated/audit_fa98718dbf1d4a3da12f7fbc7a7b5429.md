Based on my analysis of the pre-commit conflict-veto logic in `stacks-signer/src/v0/signer.rs`, I found a genuine gap that matches the CSRF bug class ("missing check relied upon by another code path that doesn't actually cover the case").

### Title
Stale cross-tenure signed conflict is never re-checked before signing, letting a signer double-sign two blocks at the same height in different tenures - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`Signer::handle_block_pre_commit` is the only gate between crossing the 70% pre-commit threshold and actually producing a block signature. Before signing, it runs two conflict checks against `SignerDb::get_signed_conflicts` (which returns every block at or above the proposed height, in **any** tenure, that this signer or the observed group ever signed):

1. A "fresh and still live" veto that only fires if `conflict.last_endorsed > freshness_cutoff` [1](#0-0) .
2. A same-tenure-only veto that checks `conflict.consensus_hash == block_info.block.header.consensus_hash` and the node's tenure tip [2](#0-1) .

A signed conflict that is **stale** (older than `tenure_last_block_proposal_timeout`) **and** lives in a **different** tenure than the new proposal is caught by neither check. The code comment claims this case is "settled by the chainstate checks above" [3](#0-2) , but `check_block_against_signer_db_state`/`check_latest_block_in_tenure` only ever inspects **one** tenure — the new block's own tenure (or its parent, for a tenure-change block), "Never both" [4](#0-3) . It therefore cannot see a conflict that sits in a third, unrelated tenure.

### Finding Description
`get_signed_conflicts` deliberately spans **all** tenures because "two blocks at the same height are siblings no matter which tenure they belong to" [5](#0-4) . The pre-commit handler is supposed to use this to prevent double-signing. But the veto logic:

- Only vetoes a **fresh** conflict (`last_endorsed > freshness_cutoff`) that also passes `conflict_still_blocks` [1](#0-0) .
- Only re-checks the node's tenure tip for conflicts in the **same tenure** as the new proposal [2](#0-1) .

Once a signed conflict in a different tenure ages past `tenure_last_block_proposal_timeout`, it is excluded from consideration by rule (1) purely on a local wall-clock timestamp, without ever asking the node whether the conflicting block is still canonical (which `conflict_still_blocks` would otherwise do). Rule (2) never looks at it because it isn't in the same tenure. The `check_block_against_signer_db_state` re-check that the comment relies on to "settle" this is scoped to a single tenure and so provides no coverage either [6](#0-5) . The result: the signer proceeds straight to `mark_locally_accepted` / `handle_block_signature` and broadcasts an acceptance signature [7](#0-6)  for a block whose height duplicates a block it (or the group) already signed in another, still-canonical tenure — with no live check that the earlier tenure is actually dead.

### Impact Explanation
This breaks the "signer signs at most one block per height" invariant that the whole pre-commit/conflict machinery exists to protect (explicitly the rationale for scanning "ANY tenure"). A signer producing two valid signatures over two different blocks at the same height in different tenures is exactly the equivocation this subsystem is designed to prevent, and matches the Critical bar: "a signer signing an invalid, non-canonical, or conflicting block."

### Likelihood Explanation
No majority of signers or key material is required — only that enough real time elapses (past `tenure_last_block_proposal_timeout`, a value on the order of the tenure timeout, not long) between the earlier signature and the new proposal reaching the pre-commit threshold, and that a competing tenure's proposal at the same height reaches this signer without a recorded reorg permit (`mark_tenure_superseded`/`reorg_permit_stands`). A miner (or the natural passage of time across a stalled/handed-off tenure) is sufficient to trigger the timing window; no bitcoin-level majority is inherently needed to create two tenures/proposals at the same Stacks height.

### Recommendation
In `handle_block_pre_commit`, do not let staleness alone drop a **cross-tenure** conflict from consideration. Either (a) always run `conflict_still_blocks` (the node-derived liveness check) for cross-tenure conflicts regardless of `last_endorsed` freshness, only excluding it once `conflict_still_blocks` itself proves it dead, or (b) extend the same-tenure tip re-check to also query the tenure of any stale-but-potentially-live conflict before signing.

### Proof of Concept
1. Signer S signs/accepts block B_X at height h in tenure X (globally accepted, `last_endorsed` timestamp T0).
2. Time passes beyond `tenure_last_block_proposal_timeout` (S's local freshness cutoff advances past T0), with no `mark_tenure_superseded` permit recorded for tenure X.
3. A new tenure Y proposes a block B_Y at the same height h (e.g. a tenure-change block whose parent chain does not run through B_X). `check_proposal`/`check_block_against_signer_db_state` for B_Y only inspects tenure Y's own/parent tenure state, not tenure X, so it passes.
4. B_Y accumulates pre-commits to threshold. In `handle_block_pre_commit`, `get_signed_conflicts` returns B_X as a conflict at height ≥ h, but `conflict.last_endorsed <= freshness_cutoff`, so the first veto (lines 1403-1421) does not fire, and the second veto (lines 1432-1457) does not fire either since `conflict.consensus_hash != tenure Y`.
5. S signs B_Y, producing two group-valid signatures (over B_X and B_Y) at the same height in different tenures from the same signer set.

### Citations

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

**File:** stacks-signer/src/v0/signer.rs (L1423-1431)
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
```

**File:** stacks-signer/src/v0/signer.rs (L1432-1457)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L1458-1478)
```rust
        if !conflicts.is_empty() {
            info!(
                "{self}: Reached the pre-commit threshold for a block that conflicts with previously signed or accepted blocks, but none of those conflicts still blocks it. Signing the replacement.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "num_conflicts" => conflicts.len(),
            );
        }
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
        if let Err(e) = block_info.mark_locally_accepted(false) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}",);
            }
        }
        self.signer_db
            .insert_block(&block_info)
            .unwrap_or_else(|e| self.handle_insert_block_error(e));
        let accepted = self.create_block_acceptance(&block_info.block);
        // have to save the signature _after_ the block info
        self.handle_block_signature(stacks_client, sortition_state, &accepted);
        self.send_block_response(&block_info.block, accepted.into());
```

**File:** stacks-signer/src/v0/signer.rs (L1803-1850)
```rust
    fn check_block_against_signer_db_state(
        &mut self,
        stacks_client: &StacksClient,
        proposed_block: &NakamotoBlock,
    ) -> Option<BlockRejection> {
        let signer_signature_hash = proposed_block.header.signer_signature_hash();
        // If this is a tenure change block, ensure that it confirms the correct number of blocks from the parent tenure.
        if let Some(tenure_change) = proposed_block.get_tenure_change_tx_payload() {
            // Ensure that the tenure change block confirms the expected parent block
            match SortitionData::check_tenure_change_confirms_parent(
                tenure_change,
                proposed_block,
                &mut self.signer_db,
                stacks_client,
                self.proposal_config.tenure_last_block_proposal_timeout,
                self.proposal_config.reorg_attempts_activity_timeout,
            ) {
                Ok(true) => return None,
                Ok(false) => {
                    return Some(self.create_block_rejection(
                        RejectReason::SortitionViewMismatch,
                        proposed_block,
                    ))
                }
                Err(e) => {
                    warn!("{self}: Error checking block proposal: {e}";
                        "signer_signature_hash" => %signer_signature_hash,
                        "block_id" => %proposed_block.block_id()
                    );
                    return Some(self.create_block_rejection(
                        RejectReason::ConnectivityIssues(
                            "error checking block proposal".to_string(),
                        ),
                        proposed_block,
                    ));
                }
            }
        }

        // Ensure that the block is the last block in the chain of its current tenure.
        match SortitionData::check_latest_block_in_tenure(
            &proposed_block.header.consensus_hash,
            proposed_block,
            &mut self.signer_db,
            stacks_client,
            self.proposal_config.tenure_last_block_proposal_timeout,
            self.proposal_config.reorg_attempts_activity_timeout,
        ) {
```

**File:** docs/signer-flows.md (L391-398)
```markdown
`check_latest_block_in_tenure` answers "does this block confirm the tip we
expect?" and it runs in three places: at proposal arrival (inside
`check_proposal`), at validate-ok, and at the moment of signing. _Which_ tenure
it is asked about depends on the block: a tenure-change block is checked against
its **parent** tenure, every other block against its **own**. Never both. The
pivotal helper is `get_tenure_last_block_info`, which considers only blocks that
carry a signature (`get_last_signed_block`): a pre-commit never vetoes anything,
it only counts as miner activity.
```

**File:** stacks-signer/src/signerdb.rs (L1587-1599)
```rust
    /// Return every signed block at or above the given Stacks height, in ANY tenure, excluding
    /// the block with the given signer signature hash, ordered by height (highest first). A
    /// block is considered signed if a signature was ever put over it, ours (`signed_self`)
    /// or the observed group's (`signed_group`). Blocks that were only pre-committed carry no
    /// signature and are never returned. Each row carries the most recent endorsement time
    /// (`signed_self`/`signed_group`, whichever is later) so the caller can judge freshness per
    /// conflict.
    ///
    /// The search deliberately spans all tenures: two blocks at the same height are siblings
    /// no matter which tenure they belong to (e.g. a tenure-start block conflicts with the
    /// previous tenure's block at the same height), so a signature over either may conflict
    /// with a fresh signature over the other.
    ///
```
