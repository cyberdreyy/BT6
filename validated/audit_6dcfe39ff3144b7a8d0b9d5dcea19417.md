### Title
Miner can manipulate the reorg-timing heuristic to retroactively erase an already-signed tenure block - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
`SortitionData::check_parent_tenure_choice` decides whether a signer may permit a new miner's tenure-change block to reorg an earlier tenure that already produced a block. The permission hinges on a timing heuristic — "was the earlier block approved too close to the reorging sortition for the new miner to have seen it?" — but the timestamp that decides this (`approved_time` of the earlier block) is effectively under the control of the miner who mined that earlier tenure, because it is stamped by signers only once the miner actually broadcasts the proposal. This mirrors the reported "lender sandwich" pattern: an attacker-controlled input (auction length / here, proposal-broadcast timing) that decides eligibility for a state-changing action (seize collateral / here, permit a reorg) is evaluated at the moment of the attacker's choosing rather than validated against an expectation fixed before the victim commits.

### Finding Description
`check_parent_tenure_choice` at [1](#0-0)  computes, for each tenure being reorged:

```
proposal_to_sortition = sortition_state_received_time - approved_at
if Duration::from_secs(proposal_to_sortition) < first_proposal_burn_block_timing {
    // "poorly timed" -> reorg permitted
}
``` [2](#0-1) 

`approved_at` comes from `local_block_info.approved_time`, which is stamped the first time signers pre-commit or locally accept the block — i.e., at the moment the miner of the earlier tenure actually broadcasts/propagates that proposal to signers, not at block-production time. A miner who wants a later reorg of their own (or a colluding miner's) tenure to be sanctioned by the "poorly timed" exception can simply withhold or delay broadcasting the first block of that tenure until shortly before a chosen later sortition arrives. This makes `proposal_to_sortition` artificially small, satisfying `< first_proposal_burn_block_timing` even though the delay was attacker-orchestrated, not caused by genuine network/timing conditions.

Once `check_parent_tenure_choice` returns `true`, the reorged tenure is recorded as superseded via `record_superseded_tenure`/`mark_tenure_superseded` [3](#0-2) , and any signature signers already placed on that tenure's block stops counting as a conflict for as long as the permitting sortition remains canonical (`reorg_permit_stands`, `PERM` branch in `handle_block_pre_commit`) [4](#0-3) . The only backstop against reorging a tenure with real history is `globally_accepted_blocks > 1` [5](#0-4)  — a tenure with exactly one globally-accepted block remains eligible for the "poorly timed" exception regardless of how that timing came about.

This breaks the intended equality "a reorg is permitted only when the new miner genuinely had no opportunity to see the prior tip" into "a reorg is permitted whenever the prior tenure's miner chose to delay disclosure," letting a signer sign a tenure-change block that supersedes a previously globally-accepted block — a conflicting/non-canonical continuation of the chain from the signer's perspective.

### Impact Explanation
This is a Critical-class issue per the rubric: it results in signers being led to sign a block that reorgs a block they had already globally accepted, based on a heuristic an attacking miner can steer using nothing but control over their own broadcast timing (a capability any single-slot miner has). No majority of signers, no other signer's key, and no auth_token are needed — only the ability to win (or collude to win) two sortitions and to delay one proposal's disclosure.

### Likelihood Explanation
The attack requires the attacker to win (or arrange, e.g. via mining-pool concentration) two sortitions: one to produce the tenure to be reorged (and withhold its broadcast), and a later one from which to submit the reorging tenure-change block within the `first_proposal_burn_block_timing` window of the withheld tenure's eventual approval. This is a plausible, if not trivial, capability for well-resourced miners, and does not require rushing within a single block interval like the original report — the attacker has full control over exactly when to release the earlier proposal, making the timing condition trivially satisfiable at a moment of their choosing.

### Recommendation
Anchor the "poorly timed" determination to a timestamp the attacker cannot delay at will — e.g., the burn-block height/time at which the tenure's block-commit was mined, or the tenure-start time recorded by the stacks-node, rather than `approved_time` (which depends on when the miner chooses to broadcast the proposal to signers). Additionally, consider bounding how long a proposed-but-unbroadcast tenure block may retroactively benefit from the reorg-timing exception (e.g., requiring `approved_time` to be close to the tenure's own burn-block arrival, not just close to the *later* reorging sortition), and treat any tenure with an already globally-accepted block as ineligible for the exception unless there is independent evidence (e.g., burnchain-timestamp-based) that visibility was genuinely too late.

### Proof of Concept
Cannot be fully constructed/verified from static code review alone — a definitive PoC would require running the signer/node harness (e.g. the `stacks-node/src/tests/signer/v0/reorg.rs` test fixtures such as `allow_reorg_within_first_proposal_burn_block_timing_secs`) with a miner client modified to deliberately delay broadcasting its tenure's first block until just before a chosen later sortition, then observing whether `check_parent_tenure_choice` grants the reorg permit and whether previously globally-accepted state is superseded. I was unable to execute this in the current environment, so likelihood/exploitability should be validated by a Devin session capable of running the existing `reorg.rs` integration tests with a modified miner broadcast-delay parameter.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L170-199)
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

        // this value *should* always be some, but try to do the best we can if it isn't
        let sortition_state_received_time =
            signer_db.get_burn_block_receive_time(&self.burn_block_hash)?;
```

**File:** stacks-signer/src/chainstate/mod.rs (L210-223)
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

**File:** stacks-signer/src/v0/signer.rs (L1368-1382)
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
```
