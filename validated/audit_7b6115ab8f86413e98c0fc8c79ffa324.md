### Title
`check_parent_tenure_choice` permanently voids the conflict guard for an already globally-accepted block by trusting a stale/racy node view over local signerdb state - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
`SortitionData::check_parent_tenure_choice` decides whether a miner is allowed to reorg away a prior tenure, and — once it decides yes — permanently records that tenure as superseded via `SignerDb::mark_tenure_superseded`, which causes the signer's own previously-placed signature over that tenure's block to stop counting as a conflict (`get_signed_conflicts`). The eligibility check for "this tenure produced no more than one block" mixes two different, non-atomic data sources: the signer's own authoritative `signerdb` count of globally-accepted blocks, and the stacks-node's possibly-stale `first_block_mined` field from `get_tenure_forking_info`. When the node's view lags behind the signer's own already-globally-accepted knowledge, the function treats a tenure that in fact has a signed, globally-accepted block as if it produced zero blocks, and unconditionally permits (and permanently records) the reorg.

### Finding Description
In `stacks-signer/src/chainstate/mod.rs`, `check_parent_tenure_choice` runs this sequence for each tenure being reorged: [1](#0-0) 

```
// disallow reorg if more than one block has already been signed
let globally_accepted_blocks =
    signer_db.get_globally_accepted_block_count_in_tenure(&tenure.consensus_hash)?;
if globally_accepted_blocks > 1 { ... return Ok(false); }

let Some(first_block_mined) = &tenure.first_block_mined else {
    // The node saw no blocks in this tenure, so the reorg takes nothing away from
    // the canonical chain. ...
    superseded_tenures.push(tenure);
    continue;
};
```

`globally_accepted_blocks` is read from the signer's own `signerdb`, the same ground-truth source the whole codebase treats as authoritative for "did we ever put a signature on this" (see the state-machine invariants: `BlockState::GloballyAccepted` is terminal, and `get_signed_conflicts` treats any block a signature was ever placed over as a conflict regardless of subsequent rejection). `tenure.first_block_mined`, by contrast, comes from `client.get_tenure_forking_info`, i.e. the node's transient view of what block commits/blocks it has processed for that tenure — a value that legitimately lags reality (the same lag the rest of the codebase is careful about, e.g. the `SORT -- "404... a fork orphaned the tenure"` vs `"could not ask"` distinction documented for `conflict_still_blocks`).

If the count check passes (0 or 1 globally-accepted block locally) but the node hasn't yet caught up to that block (so `first_block_mined` is `None`), the function unconditionally treats the tenure as if it produced *zero* blocks and pushes it into `superseded_tenures` — even though the signer itself already knows, one function call earlier, that it holds a globally-accepted block for that exact tenure. `check_parent_tenure_choice` then returns `Ok(true)`, and the caller calls `mark_tenure_superseded`: [2](#0-1) 

Once superseded, `get_signed_conflicts`'s caller (`handle_block_pre_commit`) treats that tenure's conflicting signature as excluded from the pre-commit conflict guard via `reorg_permit_stands`, as long as the permitting sortition remains canonical: [3](#0-2) [4](#0-3) 

This breaks the invariant documented throughout `docs/signer-flows.md`: "a rejection is a revocable opinion, while a signature is a bearer instrument... a block we signed binds us no matter what state it later fell to." The permit mechanism is meant to apply only to tenures that "have at most one globally accepted block and produced their first block too close to the next sortition to count" — but the buggy `None` branch bypasses the timing check (`first_proposal_burn_block_timing`) entirely and grants the permit purely because the node hasn't caught up, not because the timing rule was satisfied. [5](#0-4) 

### Impact Explanation
This lets a signer sign a second, competing block at a height it already holds a globally-accepted signature for, once the permit clears the conflict — i.e. a signer signing a conflicting/non-canonical block, breaking the "approved-parent vs canonical"/one-signature-per-conflict equality the whole pre-commit conflict-guard mechanism (section 5 of `docs/signer-flows.md`) exists to protect. Because every signer runs `check_parent_tenure_choice` independently against its own node's momentarily-lagging view, this can be triggered network-wide by ordinary node-sync lag rather than requiring a majority of signers to collude — each signer independently mis-evaluates its own local state and gets a false permit, satisfying "Critical: a signer signing an invalid, non-canonical, or conflicting block."

### Likelihood Explanation
The trigger condition is a stacks-node whose `get_tenure_forking_info` response for `first_block_mined` has not yet caught up to a tenure the local signer already globally-accepted a block for — a race that a miner can encourage by proposing the reorging tenure-change block quickly (immediately after a new sortition, before the node's block-processing pipeline reflects the previous tenure's already-signed block). No majority collusion or key access is needed; it is a single-miner-timed proposal exploiting a data-source mismatch inside one node's own signer process.

### Recommendation
In `check_parent_tenure_choice`, do not treat `tenure.first_block_mined.is_none()` as proof of "zero blocks produced" when `signerdb.get_globally_accepted_block_count_in_tenure` already reports ≥1 for that tenure. The `None` branch should only be taken when the local `signerdb` also has no record of an accepted block in that tenure; otherwise it must fall through to the timing-based path so that the `first_proposal_burn_block_timing` rule is honestly evaluated (or reject the reorg, as when `local_block_info` is missing).

### Proof of Concept
1. Signer S has signed and locally recorded a globally-accepted block B1 in tenure T1 (`get_globally_accepted_block_count_in_tenure(T1) == 1`).
2. A miner immediately produces a new sortition and proposes a tenure-change block for tenure T2 whose parent tenure is not T1's prior sortition (a reorg), timed such that S's own stacks-node has not yet processed/committed B1 into its view (so `client.get_tenure_forking_info` returns `first_block_mined: None` for T1).
3. `check_parent_tenure_choice` passes the `globally_accepted_blocks > 1` guard (count is 1), then hits the `first_block_mined == None` branch and pushes T1 into `superseded_tenures`, returning `Ok(true)` without ever checking `first_proposal_burn_block_timing`.
4. `mark_tenure_superseded` permanently (until `MAX_FORK_DEPTH`) records T1 as superseded by T2's sortition.
5. When B2 (competing with B1 at the same height, built in T2) reaches the pre-commit threshold, `reorg_permit_stands` finds the permit valid (T2's sortition is canonical) and excludes B1 from `get_signed_conflicts`, so signer S signs B2 despite already holding a signature over B1 at the same/related height.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L210-233)
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

**File:** stacks-signer/src/signerdb.rs (L1627-1642)
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

**File:** stacks-signer/src/v0/signer.rs (L1373-1392)
```rust
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
