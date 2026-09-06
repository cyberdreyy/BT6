## Analog Found: `check_parent_tenure_choice`'s "empty tenure" branch trusts the node's resolved view instead of the signer's own raw signed-block record

### Title
Signer permits and permanently supersedes a reorged tenure based on the node's (possibly stale) view of "no blocks seen," even when the signer itself already signed a block there — ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`SortitionData::check_parent_tenure_choice` decides whether a miner's new tenure is allowed to reorg a previous one. For each reorged tenure it asks the stacks-node (`client.get_tenure_forking_info`) whether that tenure has a `first_block_mined`. If the node reports `None`, the code unconditionally treats the tenure as empty, allows the reorg, and queues it for `mark_tenure_superseded`, which later voids the signer's own signature over any block in that tenure as a conflict: [1](#0-0) 

The node's belief about "no blocks mined" is exactly the kind of resolved/indirect fact that Hugo's regression trusted (`Stat` following a symlink) instead of checking the raw, local, directly-observed fact — here, whether *this signer* already signed a block in that tenure. The code comment even documents the blind spot without treating it as unsafe: [2](#0-1) 

### Finding Description
`check_parent_tenure_choice` is the sole gatekeeper for whether a miner may build a new tenure on top of something other than the prior sortition (a reorg): [3](#0-2) 

For every reorged tenure other than the one actually built upon, the function branches on `tenure.first_block_mined`, a field populated purely from the stacks-node's `get_tenure_forking_info` RPC response — i.e., what the *node* has processed: [4](#0-3) 

When `first_block_mined` is `None`, the tenure is pushed into `superseded_tenures` and, once the whole reorg clears, is recorded via `mark_tenure_superseded`. Per the design doc, this record causes the signer's *own* future signature checks to exclude the superseded tenure from `get_signed_conflicts`, i.e., the signer will no longer treat a block it already signed in that tenure as a conflict: [5](#0-4) [6](#0-5) 

The problem: the signer's own signerdb *does* track locally- and globally-accepted (signed) blocks per tenure (`get_last_signed_block`, `get_globally_accepted_block_count_in_tenure`, `get_first_approved_block_in_tenure`), and the "more than one globally accepted block" branch just above this one *does* consult local signer state: [7](#0-6) 

But the `first_block_mined == None` branch skips all of that and defers entirely to the node's (possibly stale, not-yet-caught-up) view. A block that a signer set has already collectively signed (crossed the 70% pre-commit/signature threshold, per section 5/6 of the design doc) is deliberately **not** pushed to the node until the aggregate signature is assembled and broadcast — a normal, expected latency window documented elsewhere in the same file: [8](#0-7) 

So during that window the node legitimately reports `first_block_mined: None` for a tenure the signer already signed a block in. If the miner (a single slot) races a competing tenure that reorgs this one before the signed block is pushed, every signer independently evaluates `check_parent_tenure_choice`, sees `first_block_mined == None` from the node, and both (a) permits the reorg and (b) marks the old tenure superseded — nullifying its own earlier signature as a future conflict. The signer then proceeds to sign the new tenure's conflicting block.

### Impact Explanation
This breaks the "signed vs validated"/one-per-height equality the whole pre-commit and conflict-guard design (sections 5, 7, 8 of `docs/signer-flows.md`) exists to protect: a signer can end up placing valid signatures over two blocks that conflict (same or overlapping height across sibling tenures), because the very state (`mark_tenure_superseded`) meant to *sanction* a reorg gets set even when the signer itself, not just the miner, has unfinished business (an un-pushed but already-signed block) in the tenure being reorged. Since every honest signer runs the identical logic against the identical node-lag condition, this is not a one-off local mistake — the whole signer set can independently reach the same wrong "permit + supersede" verdict, enabling the network to actually finish two conflicting signature sets. That is a "signer signing … a conflicting block" — a Critical-class equivocation/safety break per the scoped impact categories.

### Likelihood Explanation
This requires only a single miner (the "one-slot miner (plus gossip)" scope) racing two tenures around a narrow but realistic window: the time between a block crossing the 70% signature threshold locally and that block actually being pushed to and processed by the stacks-node. That window is explicitly acknowledged as real and non-trivial in the code's own comments (`docs/signer-flows.md` lines 310-320, and the code comment at `chainstate/mod.rs` lines 225-232), so no unusual timing assumptions or majority-signer collusion is needed — just an adversarial miner timing its next block-commit/tenure-change to land while the previous tenure's signed block is still in flight to the node.

### Recommendation
In the `first_block_mined == None` branch of `check_parent_tenure_choice`, do not treat "node saw no blocks" as sufficient. Consult the signer's own local record (e.g. `get_last_signed_block`/`get_first_approved_block_in_tenure`) for the tenure being reorged; if the signer itself has already signed (locally or globally accepted) a block there, refuse the reorg (or at least withhold `mark_tenure_superseded`) regardless of what the node currently reports, mirroring the stricter handling already used for the ">1 globally accepted block" branch just above it.

### Proof of Concept
1. A single miner wins sortition for tenure T1 and proposes block A. The signer set validates, pre-commits, and crosses the 70% signature threshold on A (mark_locally_accepted with group signature) — but A has not yet been pushed to/processed by the stacks-node (network delay/miner withholding the aggregate signature).
2. The same miner immediately wins the next sortition and proposes a tenure-change block B for tenure T2 whose `prev_tenure_consensus_hash` builds off T0 (the tenure before T1), i.e., a reorg of T1.
3. Each signer runs `check_parent_tenure_choice` for T1: `client.get_tenure_forking_info` reports `first_block_mined: None` for T1 (the node has not ingested A yet), so per `stacks-signer/src/chainstate/mod.rs` lines 225-233 the signer marks T1 as `superseded_tenures` and, since it's the only reorged tenure, returns `Ok(true)` and calls `mark_tenure_superseded(T1)`.
4. The signer now excludes T1 from `get_signed_conflicts` checks (`reorg_permit_stands`), so its earlier signature over A no longer blocks it from signing B in T2.
5. If enough signers hit the same race (all running identical code against the same node-lag condition), both A's and B's signature sets can independently reach the 70% threshold — the signer set has now signed two conflicting blocks.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L163-169)
```rust
    /// A permitted reorg is recorded once the whole reorg is permitted: each tenure whose
    /// blocks this one is allowed to replace is marked superseded (see
    /// [`SignerDb::mark_tenure_superseded`]), so a signature we already placed on one of those
    /// blocks does not later block the replacement. The record carries this tenure's sortition
    /// as the permitting one, so the permit stops applying if a burnchain fork later orphans
    /// it. Nothing is recorded for a refused reorg, even for the tenures in it that
    /// individually qualified.
```

**File:** stacks-signer/src/chainstate/mod.rs (L170-196)
```rust
    pub fn check_parent_tenure_choice(
        &self,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        first_proposal_burn_block_timing: &Duration,
    ) -> Result<bool, SignerChainstateError> {
        // if the parent tenure is the last sortition, it is a valid choice.
        // if the parent tenure is a reorg, then all of the reorged sortitions
        //  must either have produced zero blocks _or_ produced their first (and only) block
        //  very close to the burn block transition.
        if self.prior_sortition == self.parent_tenure_id {
            return Ok(true);
        }
        info!(
            "Most recent miner's tenure does not build off the prior sortition, checking if this is valid behavior";
            "sortition_state.consensus_hash" => %self.consensus_hash,
            "sortition_state.prior_sortition" => %self.prior_sortition,
            "sortition_state.parent_tenure_id" => %self.parent_tenure_id,
        );

        let tenures_reorged =
            client.get_tenure_forking_info(&self.parent_tenure_id, &self.prior_sortition)?;
        if tenures_reorged.is_empty() {
            warn!("Miner is not building off of most recent tenure, but stacks node was unable to return information about the relevant sortitions. Marking miner invalid.");
            return Ok(false);
        }

```

**File:** stacks-signer/src/chainstate/mod.rs (L204-233)
```rust
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

**File:** docs/signer-flows.md (L251-252)
```markdown
    CONF --> PERM{"covered by a reorg permit whose<br/>permitting sortition is still canonical?<br/>reorg_permit_stands"}
    PERM -- yes --> EXCL(["excluded — our signature must not<br/>block a replacement we sanctioned"]):::good
```

**File:** docs/signer-flows.md (L310-320)
```markdown
2. **Does the node's canonical Stacks chain still reach the block itself?**
   - **it does** — real chain state; keep blocking;
   - **it does not, and the block was globally accepted** — the node once _did_
     have it, so a reorg moved past it. That is proof it is dead;
   - **it does not, and the block was never globally accepted** — a block is
     not handed to the node until the whole signer set has signed it, so this
     may mean "not yet seen" rather than "dead". A sibling at the same height
     therefore keeps blocking, since signing both would be the double-sign this
     guard exists for; a block _above_ the proposal does not, because it is no
     sibling and abandoning an unconfirmed block to restart beneath it is a
     reorg, not an equivocation.
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
