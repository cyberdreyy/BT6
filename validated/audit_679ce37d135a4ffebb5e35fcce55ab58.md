### Title
Timing-based reorg permit defaults to "poorly timed" (always-allow) when a signer never recorded `approved_time` for the reorged tenure's first block - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`SortitionData::check_parent_tenure_choice` is the only signer-side gate that decides whether a miner is allowed to build off (reorg) something other than the prior sortition's tenure. It permits the reorg only when the reorged tenure's first block was "poorly timed" — i.e., produced too close to the next sortition to give the network time to react (`first_proposal_burn_block_timing`). The elapsed time used for that decision is computed as `sortition_state_received_time - local_block_info.approved_time`. When this particular signer has no `approved_time` recorded for that tenure's first block (e.g. it never pre-committed/locally-accepted it directly, but the row still exists because `get_first_approved_block_in_tenure` also matches on `signed_group`), the code substitutes `0` for the elapsed time instead of treating the timing as unknown/unsafe. Since `0 < first_proposal_burn_block_timing` is true for any nonzero configured timeout, this branch **always** concludes the tenure was "poorly timed" and unconditionally permits the reorg — even for an established, long-running tenure — bypassing the entire safety purpose of the check for that signer.

### Finding Description
`check_parent_tenure_choice` (stacks-signer/src/chainstate/mod.rs:170-295) is invoked from `SortitionsView::check_proposal` (v1) and the analogous v2 path whenever a newly-elected miner's tenure does not build on the prior sortition. Its purpose, per its own doc comment, is: allow the reorg only if every reorged tenure "produced zero blocks _or_ produced their first (and only) block very close to the burn block transition."

For a reorged tenure that did produce a block, the code fetches:
```
let Some(local_block_info) =
    signer_db.get_first_approved_block_in_tenure(&tenure.consensus_hash)?
```
`get_first_approved_block_in_tenure` (stacks-signer/src/signerdb.rs:1518-1527) matches rows where `signed_self IS NOT NULL OR signed_group IS NOT NULL OR approved_time IS NOT NULL`. `signed_group` can become set — via `store_and_process_block_signature`/`mark_locally_accepted(true)` when the group threshold of *peer* signatures is observed — for a signer that itself never pre-committed to or locally accepted this specific block (e.g. it was still awaiting its own node's validation, was processing other proposals, or simply raced the group's own 70% threshold before completing its own evaluation). In that situation `approved_time`, which is only stamped "at pre-commit or local acceptance (first wins)" for *this* signer, can remain `None` even though `local_block_info` is `Some`.

The timing computation then does:
```rust
let proposal_to_sortition = if let Some(approved_at) = local_block_info.approved_time {
    sortition_state_received_time.saturating_sub(approved_at)
} else {
    info!("We did not sign over the reorged tenure's first block, considering it as a late-arriving proposal");
    0
};
if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
    // ... permit reorg, mark tenure superseded ...
    continue;
}
```
`proposal_to_sortition = 0` is the minimum possible value the real formula could ever produce, so the "poorly timed" branch is always taken, and the tenure is always pushed to `superseded_tenures` and the reorg allowed — regardless of how long the tenure had actually been running or how many burn blocks had elapsed since its true first-block proposal. The equality this breaks is: "reorg permitted ⇔ true elapsed time between reorged tenure's first-block approval and the next sortition < `first_proposal_burn_block_timing`." For any signer lacking a personal `approved_time` for that block, the check silently degenerates to "reorg permitted ⇔ true" — an equality violation that lets that signer sign a block that legitimately conflicts with (reorgs) a tenure it should have rejected as canonical, satisfying the "signer signing a non-canonical/conflicting block" impact class.

Because `check_parent_tenure_choice`, once it returns `true`, also calls `record_superseded_tenure` (`mark_tenure_superseded`), this signer permanently records the previously-canonical tenure as superseded in its local DB, which per `get_signed_conflicts`/`reorg_permit_stands` (docs/signer-flows.md:329-347) causes the signer's own pre-existing signature over that tenure's blocks to stop blocking the replacement, compounding the effect: the affected signer both signs the illegitimate reorging block and releases its own prior signature from acting as a conflict guard.

### Impact Explanation
A signer landing on this always-allow branch will sign (or pre-commit toward signing) a block for a miner reorging a tenure that a correctly-timed evaluation would have rejected with `ReorgNotAllowed`. This is exactly the "signer signing an invalid/non-canonical/conflicting block" critical-impact class: the signer's local safety check for reorg legitimacy is bypassed entirely, weakening the aggregate 70%-weight safety property the whole pre-commit/signature scheme is built on (docs/signer-flows.md's own model assumes `check_parent_tenure_choice` correctly gates unsafe reorgs). If enough signers happen to be in this state simultaneously (e.g. all recently restarted, or all raced the group threshold on that tenure's first block without individually pre-committing), a stale/malicious miner could force a reorg of an already-multi-block tenure that the protocol was explicitly designed to prevent.

### Likelihood Explanation
This does not require compromising any signer's key or a majority of signers — it is triggered purely by an ordinary state-machine gap (a signer that observed the group signature/global acceptance of a tenure's first block without itself recording a local `approved_time`, e.g., due to restart, catching up, or normal signature-race timing) combined with a single miner slot proposing a reorging tenure-change block, plus ordinary StackerDB gossip. No majority collusion or auth token access is needed; the only necessary condition is at least one signer being in the "row exists via `signed_group`/no personal `approved_time`" state, which can occur in ordinary operation (a signer catching up after restart, or one that observed peer signatures reach 70% weight before completing its own pre-commit round).

### Recommendation
Treat a missing `approved_time` as "timing unknown" rather than "timing is 0" — i.e., do not default to the value that always satisfies the "poorly timed" condition. Concretely, when `local_block_info.approved_time` is `None`, either: (a) fall back to querying the stacks-node for the block's actual observed/proposal time and use that, or (b) conservatively deny the reorg (return `Ok(false)`) instead of assuming the most permissive case, consistent with the surrounding code's stated philosophy that "wrongly signing cannot be taken back" and the node/lookup-failure paths elsewhere in this file already default to the safe (blocking) outcome rather than the permissive one.

### Proof of Concept
Not independently executable from the index (requires spinning up the multi-signer/bitcoind integration harness), but the code path is deterministic and directly inspectable:
1. Bring up a signer set; let a tenure `T1` mine more than zero blocks, with its first block's group signature threshold reached (`signed_group` set) via peer pre-commits/signatures before signer `S` completes its own pre-commit for that specific block — leaving `approved_time` unset for `S` while `get_first_approved_block_in_tenure` still returns the row (since `signed_group IS NOT NULL`).
2. A new miner wins the next sortition and proposes a tenure-change block whose `parent_tenure_id` is not `T1` (a reorg attempt), long after `T1`'s real first block was accepted (well past `first_proposal_burn_block_timing`).
3. On signer `S`, `SortitionsView::check_proposal` → `check_parent_tenure_choice` (stacks-signer/src/chainstate/mod.rs:247-278) computes `proposal_to_sortition = 0` (the `else` branch, since `approved_time` is `None`), which is `< first_proposal_burn_block_timing`, so the reorg is permitted and `T1` is marked superseded on `S`, even though every other, correctly-timestamped signer rejects the same proposal with `ReorgNotAllowed`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L159-222)
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
```

**File:** stacks-signer/src/chainstate/mod.rs (L247-278)
```rust
            let checked_proposal_timing = if let Some(sortition_state_received_time) =
                sortition_state_received_time
            {
                // how long was there between when the proposal was received and the next sortition started?
                let proposal_to_sortition = if let Some(approved_at) =
                    local_block_info.approved_time
                {
                    sortition_state_received_time.saturating_sub(approved_at)
                } else {
                    info!("We did not sign over the reorged tenure's first block, considering it as a late-arriving proposal");
                    0
                };
                if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
                    info!(
                        "Miner is not building off of most recent tenure. A tenure they reorg has already mined blocks, but the block was poorly timed, allowing the reorg.";
                        "parent_tenure" => %self.parent_tenure_id,
                        "last_sortition" => %self.prior_sortition,
                        "violating_tenure_id" => %tenure.consensus_hash,
                        "violating_tenure_first_block_id" => %first_block_mined,
                        "violating_tenure_proposed_time" => local_block_info.proposed_time,
                        "new_tenure_received_time" => sortition_state_received_time,
                        "new_tenure_burn_timestamp" => self.burn_header_timestamp,
                        "first_proposal_burn_block_timing_secs" => first_proposal_burn_block_timing.as_secs(),
                        "proposal_to_sortition" => proposal_to_sortition,
                    );
                    superseded_tenures.push(tenure);
                    continue;
                }
                true
            } else {
                false
            };
```

**File:** stacks-signer/src/signerdb.rs (L1518-1527)
```rust
    /// Return the first approved/signed block in a tenure (identified by its consensus hash)
    pub fn get_first_approved_block_in_tenure(
        &self,
        tenure: &ConsensusHash,
    ) -> Result<Option<BlockInfo>, DBError> {
        let query = "SELECT block_info FROM blocks WHERE consensus_hash = ? AND (signed_self IS NOT NULL OR signed_group IS NOT NULL OR approved_time IS NOT NULL) ORDER BY stacks_height ASC LIMIT 1";
        let result: Option<String> = query_row(&self.db, query, [tenure])?;

        try_deserialize(result)
    }
```

**File:** docs/signer-flows.md (L496-512)
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
