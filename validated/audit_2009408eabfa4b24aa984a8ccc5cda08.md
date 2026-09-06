### Title
Reorg-permission decision in `check_parent_tenure_choice` relies on each signer's own local wall-clock "receive time" instead of a canonical, agreed-upon value — ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
### Finding Description
The external report's root cause is generic: a protocol used an easily-observable, per-observer, manipulable instantaneous reading (Uniswap spot price) to make an irreversible payout decision, instead of a robust, agreed-upon value (a TWAP oracle). The reachable analog in this repo is `SortitionData::check_parent_tenure_choice` in `stackslib` signer chainstate logic, exposed via `stacks-signer/src/chainstate/mod.rs`, lines 170-295. This function decides whether a new miner is allowed to reorg a prior tenure, and that decision is based on comparing two purely local, per-signer wall-clock timestamps:

- `sortition_state_received_time`, from `signer_db.get_burn_block_receive_time(&self.burn_block_hash)` — the local time *this specific signer's node* recorded upon observing the new burn block.
- `local_block_info.approved_time`, from `SignerDb` for the reorged (victim) tenure's first block — the local time *this specific signer* stamped when it pre-committed to or locally accepted that block. [1](#0-0) [2](#0-1) 

The threshold comparison — `if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing { … allow reorg … }` — decides whether the "poorly timed" exception applies and the reorg is sanctioned (`superseded_tenures.push(tenure)`), which then permanently records the victim tenure as superseded via `record_superseded_tenure`/`mark_tenure_superseded`, voiding this signer's own conflict-guard over the block it may have already signed in the victim tenure [3](#0-2) .

Because both `approved_time` and `sortition_state_received_time` are recorded independently by each signer based on when messages/blocks/burn-events physically reached that signer over the network (StackerDB gossip and the node's own burn-scanner), they are not a consensus-validated or canonical quantity — they are the signer-analog of an easily-influenced "spot" reading. Ordinary network jitter, or a miner/relay deliberately delaying propagation of the victim tenure's first block to some signers while it propagates normally to others, causes different signers to compute different `proposal_to_sortition` values for the exact same objective reorg question. This is exactly the class of bug flagged in the report: a decision that should be based on a robust/canonical measurement is instead keyed off a value that varies by observer and by message timing, and it directly gates a **safety-relevant, one-way decision** — whether to sign a tenure-change block that reorgs a previously-approved/signed tenure.

### Impact Explanation
If signers disagree about whether `first_proposal_burn_block_timing` was crossed for the same tenure/sortition pair (purely due to gossip/network timing differences, not due to any objective disagreement about chain state), a subset of signers will treat the reorg as legitimate (`check_parent_tenure_choice` → `true`, `ReorgNotAllowed` never returned, tenure marked superseded, and the reorg-permit later suppresses this signer's own prior signature as a conflict per `record_superseded_tenure`) while another subset will treat it as illegitimate and reject/refuse to sign. This can produce a fractured signer set voting on two mutually exclusive chain histories for the same height/tenure based on non-canonical local clock inputs, which is the general shape of the "signer signing a non-canonical/conflicting block" impact category: some signers end up signing a tenure-change/reorg that the rest of the network does not agree is valid. A single new miner naturally controls when and to whom it broadcasts/propagates the competing tenure's blocks (an ordinary one-slot miner + gossip capability), so the mismatch is triggerable without needing majority signer collusion.

### Likelihood Explanation
This requires only ordinary conditions: ordinary network propagation delay variance across signers, or a miner (or any gossip-path actor) selectively delaying broadcast of the reorg candidate's first block to a subset of signers relative to when the new sortition's burn block reaches them. No majority of signers, no signer keys, and no auth token are needed — it is achievable by the block-proposing miner alone plus normal gossip-layer timing variance, which is within the rules' allowed trigger class ("a one-slot miner (plus gossip)"). The existing regression tests (`check_parent_tenure_choice_reorg_timing_ok`/`_bad` in `stacks-signer/src/chainstate/tests/v2.rs` and `stacks-signer/src/chainstate/tests/v1.rs`) already demonstrate the binary knife-edge behavior around the timing threshold, confirming the decision is timing-sensitive and observer-local, but they only test a single simulated signer's view — they do not test for cross-signer disagreement, so this divergence risk is not covered.

### Recommendation
Replace the purely local wall-clock comparison with a value that is derivable identically by every signer from consensus/canonical data — e.g., compare burn-chain-native timestamps/heights that are part of the canonical burnchain history (the sortition's own recorded burn-block timestamp and the victim tenure's burn-block height/timestamp) rather than signer-local receipt times (`get_burn_block_receive_time`, `approved_time`), which are network-timing artifacts. If a locally-observed timestamp must remain part of the heuristic, it should only be used as a fallback and never allowed to flip the reorg-permission outcome across signers for the same objective tenure pair without a canonical corroborating signal (e.g., require agreement/threshold among signers' independent observations before treating a reorg as sanctioned, similar to how pre-commit/threshold-based decisions are already made elsewhere in the protocol).

### Proof of Concept
Not independently exploitable/provable purely via static code reading in ask-only mode: the divergence requires demonstrating that two different signer processes, given identical canonical chain inputs but different message-arrival timing (achievable by delaying StackerDB/gossip propagation of the victim tenure's block to a subset of signers, or delaying the new tenure-change proposal's arrival at a subset of signers), compute `proposal_to_sortition` values that straddle `first_proposal_burn_block_timing_secs` on opposite sides. This would need to be validated with an integration/simulation test (e.g., extending `stacks-signer/src/chainstate/tests/v1.rs`/`v2.rs`'s `reorg_timing_testing` harness to run two independent `SignerDb`/`SortitionData` instances with staggered `insert_burn_block`/`approved_time` stamps derived from artificially skewed arrival times) — this is left as a follow-up since a background engineering session with test execution would be required to confirm the observable signer-set split in practice.

### Citations

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L1-1)
```rust
// Copyright (C) 2024-2026 Stacks Open Internet Foundation
```

**File:** stacks-signer/src/chainstate/mod.rs (L197-278)
```rust
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
            let Some(local_block_info) =
                signer_db.get_first_approved_block_in_tenure(&tenure.consensus_hash)?
            else {
                warn!(
                    "Miner is not building off of most recent tenure, but a tenure they attempted to reorg has already mined blocks, and there is no local knowledge for that tenure's block timing.";
                    "parent_tenure" => %self.parent_tenure_id,
                    "last_sortition" => %self.prior_sortition,
                    "violating_tenure_id" => %tenure.consensus_hash,
                    "violating_tenure_first_block_id" => %first_block_mined,
                );
                return Ok(false);
            };

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
