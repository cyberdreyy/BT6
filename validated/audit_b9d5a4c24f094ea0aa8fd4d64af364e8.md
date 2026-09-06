## Title
Reorg-timing check treats an unsigned/unknown tenure as automatically "poorly timed," letting a single miner force an unwarranted tenure reorg - (File: stacks-signer/src/chainstate/mod.rs)

### Summary
`SortitionData::check_parent_tenure_choice` decides whether a miner is allowed to reorg a prior tenure by comparing the elapsed time between when this signer approved the reorged tenure's first block (`approved_time`) and when the new burn block arrived (`sortition_state_received_time`), against `first_proposal_burn_block_timing`. This is directly analogous to the ThecosomataETH bug: a freshness/tamper-resistance decision is made from a locally-observed timestamp with no fallback verification against ground truth, and the "stale" branch defaults to the permissive outcome. [1](#0-0) 

### Finding Description
When `check_parent_tenure_choice` evaluates whether a reorged tenure's first block was "poorly timed" (and therefore may be legitimately reorged), it computes `proposal_to_sortition` from `local_block_info.approved_time`:

```rust
let proposal_to_sortition = if let Some(approved_at) = local_block_info.approved_time {
    sortition_state_received_time.saturating_sub(approved_at)
} else {
    info!("We did not sign over the reorged tenure's first block, considering it as a late-arriving proposal");
    0
};
if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
    // ... reorg permitted, tenure marked superseded
}
``` [2](#0-1) 

If this signer has local knowledge of the tenure (`get_first_approved_block_in_tenure` returned `Some`) but never actually pre-committed/signed that block itself (e.g. it rejected it, saw only a proposal, or restarted and lost the signature timestamp while the block row survived), `approved_time` is `None`. The code path treats that as `proposal_to_sortition = 0`, which is unconditionally less than `first_proposal_burn_block_timing` — i.e., it is *always* judged "poorly timed" and the reorg is unconditionally permitted, regardless of how much real wall-clock time actually elapsed between the tenure's first block and the new sortition. This is the same class of flaw as the oracle report: a staleness/freshness signal is used to gate a security-relevant decision, but when the signal is missing/degraded, the code defaults to the *permissive* branch instead of the *safe* (denial) branch, and there is no independent tamper-resistance/deviation check (e.g., consulting `proposed_time`, the block header timestamp, or the node directly) to corroborate the "0" default.

Because this is per-signer local state (`signer_db.get_first_approved_block_in_tenure`, `approved_time`), a signer that is out of sync with its own local approval bookkeeping for that specific tenure (a plausible operational condition — e.g. added late, restarted, or its own proposal handling raced with a competing block) will hand a *one-slot miner* an unconditional "permit" to reorg an already-mined tenure, once that same miner also arranges (or benefits from) other signers reaching the same degenerate state or being on the permissive side of the vote. Since `check_parent_tenure_choice` is invoked independently by every signer evaluating the proposal (`check_proposal` in `chainstate/v1.rs`/`v2.rs`), a miner who can get enough signers into this "no approved_time" state effectively breaks the approved-parent vs. canonical-parent equality that this check exists to enforce, without needing to control any signer's key or a majority-collusion attack — merely by exploiting each signer's own incomplete bookkeeping.

### Impact Explanation
If triggered, this allows a miner to have signers unconditionally treat a tenure as legitimately reorgable ("poorly timed") — approving a `TenureChange`/parent-tenure choice that should have been rejected under the real elapsed-time rule. This is the exact class the task flags as Critical: a signer being led to sign a non-canonical/conflicting parent-tenure choice, i.e., approving a reorg of a tenure that produced blocks and was NOT actually poorly timed. Once the reorg is permitted, `record_superseded_tenure` immediately voids that signer's own prior conflict protection over the reorged tenure's blocks (`mark_tenure_superseded`), removing the last local defense against signing a genuinely conflicting chain. [3](#0-2) 

### Likelihood Explanation
The trigger condition — a signer having `local_block_info` for a tenure (i.e., it once evaluated/stored that block) but no `approved_time` on it — is plausible via ordinary rejection/db-state paths (a block can be present in `signer_db.blocks` without ever being pre-committed/signed by this specific signer, e.g. it was rejected, is `PreCommitted` only from a peer's chunk, or was reconstructed after a restart). This does not require a majority of signers to be malicious or colluding, and does not require the auth token or another signer's key — only that the reorging miner's proposal happens to hit signers in this locally-degraded state. However, the exact operational conditions needed to reliably reach this state for `get_first_approved_block_in_tenure`'s notion of "approved" versus "signed" were not fully verifiable from the available index (the precise semantics of `get_first_approved_block_in_tenure`'s SQL/state filter and whether `approved_time` can be `None` for a row it returns could not be confirmed with certainty in this pass).

### Recommendation
Change the missing-`approved_time` branch in `check_parent_tenure_choice` to default to the safe/denying outcome (or refuse the reorg / return an error requiring the signer to sync state) instead of substituting `0`. At minimum, corroborate the "we never signed it" case against an independent source (e.g. the block's own header timestamp, or a query to the node) before permitting an unconditional reorg, mirroring the oracle-report's recommendation to require both freshness and tamper-resistant corroboration rather than trusting a single unverified signal.

### Proof of Concept
Not independently reproducible from the indexed code alone — the exact reachability of `local_block_info.approved_time == None` while `get_first_approved_block_in_tenure` still returns `Some` could not be confirmed with certainty in this pass (the definition of `get_first_approved_block_in_tenure` in `stacks-signer/src/signerdb.rs` was not fully retrieved). A Devin session with full repository access should:
1. Inspect `SignerDb::get_first_approved_block_in_tenure` and `BlockInfo.approved_time`/`mark_pre_committed` to confirm whether a block can be returned by that query with `approved_time == None`.
2. If confirmed, write a unit test analogous to `check_parent_tenure_choice_reorg_timing_bad`/`_ok` in `stacks-signer/src/chainstate/tests/v1.rs`/`v2.rs`, but constructing `local_block_info` with `approved_time = None`, and assert that `check_parent_tenure_choice` incorrectly permits the reorg regardless of `sortition_state_received_time`.

### Citations

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
