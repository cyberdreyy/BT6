## Finding [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Title
Reorg-permit reuse lets a signer sign a never-sanctioned conflicting tenure - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`reorg_permit_stands` decides whether a previously-signed conflicting block should stop blocking a new signature. It only asks the node whether the *permitting* sortition (the tenure that was originally sanctioned to reorg the conflict) is still canonical — it never checks that the *currently proposed* block belongs to that same permitting tenure. Because the permit record is stored per superseded tenure (keyed only by the old tenure's `consensus_hash`) rather than per (superseded tenure, specific new tenure) pair, a permit legitimately granted for one reorg (tenure `A` → `C`) is silently honored for a later, unrelated, never-vetted conflicting tenure `D` that also conflicts with `A`, as long as `C` remains canonical.

### Finding Description
When a miner's tenure builds off something other than the prior sortition, `check_parent_tenure_choice` decides whether the reorg is allowed and, if so, calls `SignerDb::mark_tenure_superseded` for every reorged tenure, recording `(consensus_hash=A, superseded_by_consensus_hash=C, superseded_by_burn_block_hash=C's burn hash)` [4](#0-3) [5](#0-4) . This record only says "tenure A may be legitimately replaced because tenure C was sanctioned to reorg it" — it is not scoped to a specific candidate block being signed right now.

Later, when a different proposal `D` reaches the pre-commit threshold and conflicts with A's already-signed block (same or higher height, any tenure), the signer consults `get_signed_conflicts`, then for each fresh conflict calls `reorg_permit_stands(conflict)`:

```
fn reorg_permit_stands(&self, stacks_client: &StacksClient, conflict: &SignedConflictInfo) -> bool {
    let Some(superseded_by) = &conflict.superseded_by else { return false; };
    match stacks_client.get_sortition_by_burn_hash(&superseded_by.burn_block_hash) {
        Ok(_) => true,
        ...
    }
}
``` [2](#0-1) 

This function checks only "is C (the tenure that was originally permitted to reorg A) still canonical?" It never compares `superseded_by.consensus_hash` (== C) to `block_info.block.header.consensus_hash` (== D, the tenure actually being signed now). The call site applies the same blind check inside the "fresh conflict" filter used to decide whether to withhold the signature:

```
if let Some(conflict) = conflicts.iter().find(|conflict| {
    conflict.last_endorsed > freshness_cutoff
        && !self.reorg_permit_stands(stacks_client, conflict)
        && self.conflict_still_blocks(...)
}) { ... refuse ... }
``` [1](#0-0) 

Consequently, once any tenure `C` is ever sanctioned to reorg `A` (a normal, legitimate event — e.g. `A` produced zero blocks or its only block landed too close to the next sortition), `A`'s conflict entry is permanently excluded from blocking signatures as long as `C` stays canonical — regardless of which tenure is actually being proposed and signed. A completely different, later tenure `D` that also conflicts with `A` (e.g. `D` and `A` are siblings at the same height, or `D` also tries to build past `A` without ever going through `check_parent_tenure_choice`'s reorg-timing check against `A`) rides on `C`'s permit for free.

This breaks the "approved-parent tenure vs. canonical/actually-signed tenure" equality the pre-commit guard exists to enforce: the reorg-timing rules (`first_proposal_burn_block_timing`, "reorged tenure produced ≤1 block") are evaluated once, for one specific replacement (`C`), yet the resulting exemption is applied unconditionally to every future conflict against `A`, including replacements that were never evaluated against those rules at all.

### Impact Explanation
A one-slot miner (plus ordinary gossip) can exploit this to obtain the signer set's signature over two genuinely conflicting/non-canonical blocks at the same height once any legitimate reorg permit has ever been granted against a given tenure: first get `A` signed, then get a sanctioned reorg `C` recorded (satisfying the reorg-timing rules), then — while `C` remains canonical — propose and push through a further conflicting tenure `D` that was never itself checked by `check_parent_tenure_choice` against `A`. The signature guard that is supposed to prevent double-signing this height is bypassed via the stale/misscoped permit lookup, letting the signer place a real signature on a block whose parent-tenure choice was never approved. This is a Critical-class outcome per the rules: a signer signing a non-canonical/conflicting block due to a broken equality between "the tenure that was approved to reorg" and "the tenure actually being signed now."

### Likelihood Explanation
Reorg permits are a normal, expected occurrence (any tenure with zero blocks, or whose sole block landed close to a sortition transition, triggers `mark_tenure_superseded`), so the precondition ("some permit exists against the conflicting tenure, and its permitting sortition is still canonical") is common on live networks, not a rare edge case. Triggering the second, unrelated conflicting proposal only requires the current block-proposing miner (a single sortition winner) to propose a second/competing tenure at the same height while the earlier permit's sortition (`C`) has not yet been orphaned — well within a single miner's/gossip-only capability, no colluding majority of signers required.

### Recommendation
Scope the permit check to the specific proposal being evaluated: `reorg_permit_stands` (or its caller) must verify that `superseded_by.consensus_hash`/`superseded_by_burn_block_hash` matches the tenure of the block currently being signed (`block_info.block.header.consensus_hash`), not merely that some previously-permitted tenure is still canonical anywhere on the chain. Alternatively, store/consult superseded-tenure records keyed by the pair (superseded tenure, specific replacement tenure) and only honor the permit when the replacement tenure equals the one actually reaching the pre-commit threshold now.

### Proof of Concept
1. Miner wins sortition for tenure `A`; signer set validates and signs block `A` at height `h` (`mark_locally_accepted`).
2. `A` produces no further blocks. Miner wins the next sortition, tenure `C`, whose parent choice reorgs `A`. Because `A` qualifies under the reorg-timing rules (zero/near-zero blocks), `check_parent_tenure_choice` returns `true` and calls `mark_tenure_superseded(A, ..., superseded_by=C, ...)` [4](#0-3) . `C` remains canonical on the burn chain.
3. Separately (e.g. after a subsequent burn-chain reorg or a second competing tenure at the same slot), the miner proposes tenure `D` at height `h`, which also conflicts with `A` (same-height sibling), but `D`'s own parent-tenure choice was never checked against `A` by `check_parent_tenure_choice` (it built off a different prior sortition, or the timing rules for `D` vs `A` were never satisfied).
4. When `D` reaches the pre-commit threshold, `get_signed_conflicts` returns `A` as a fresh conflict; `reorg_permit_stands(conflict_A)` queries only whether `C`'s sortition is canonical — it is — so the function returns `true` and the conflict is excluded from the blocking check [1](#0-0) .
5. The signer proceeds to `mark_locally_accepted`/sign `D`, producing signer signatures over two conflicting blocks (`A` and `D`) at height `h`, even though only `C` — not `D` — was ever sanctioned to replace `A`.

### Citations

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

**File:** stacks-signer/src/signerdb.rs (L1628-1660)
```rust
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

**File:** stacks-signer/src/chainstate/mod.rs (L159-204)
```rust
impl SortitionData {
    /// Check if the tenure defined by `sortition_state` is building off of an
    ///  appropriate tenure.
    ///
    /// A permitted reorg is recorded once the whole reorg is permitted: each tenure whose
    /// blocks this one is allowed to replace is marked superseded (see
    /// [`SignerDb::mark_tenure_superseded`]), so a signature we already placed on one of those
    /// blocks does not later block the replacement. The record carries this tenure's sortition
    /// as the permitting one, so the permit stops applying if a burnchain fork later orphans
    /// it. Nothing is recorded for a refused reorg, even for the tenures in it that
    /// individually qualified.
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

        // this value *should* always be some, but try to do the best we can if it isn't
        let sortition_state_received_time =
            signer_db.get_burn_block_receive_time(&self.burn_block_hash)?;

        // Track which tenures are superseded by the reorg, then mark them in
        // the DB after the reorg is permitted.
        let mut superseded_tenures = Vec::new();
        for tenure in tenures_reorged.iter() {
```
