### Title
Local, unauthenticated proposal-timing measurement in `check_parent_tenure_choice` lets a miner trick individual signers into granting an unwarranted reorg permit - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
`SortitionData::check_parent_tenure_choice` decides whether a new tenure is allowed to reorg a still-live prior tenure by comparing two **locally-observed, per-signer wall-clock timestamps** — the time this signer received the reorging sortition (`sortition_state_received_time`) and the time this signer itself approved the reorged tenure's first block (`approved_time`) — against a threshold (`first_proposal_burn_block_timing`). This is structurally the same defect class as the reported Uniswap issue: a decision that gates an irreversible, security-relevant action (permitting a reorg / eventually signing a competing block) is made from a single, freshly-observed, easily-influenced data point instead of a value validated across multiple independent observers. [1](#0-0) 

### Finding Description
When a miner's tenure does not build on the most recent sortition, `check_parent_tenure_choice` decides whether the reorg is permitted per potentially-superseded tenure. If that tenure already produced a block, the function computes:

```
proposal_to_sortition = sortition_state_received_time.saturating_sub(approved_time)
```

and permits the reorg if `proposal_to_sortition < first_proposal_burn_block_timing`. [2](#0-1) 

Both timestamps are purely local to the individual signer process:
- `approved_time` is stamped on this signer's own `BlockInfo` at pre-commit or local acceptance time, not at any consensus-visible moment.
- `sortition_state_received_time` comes from `signer_db.get_burn_block_receive_time`, i.e., whenever this specific signer's node happened to observe/forward the new burn block. [3](#0-2) [4](#0-3) 

Because neither value is cross-checked against any canonical, aggregated, or consensus-derived reading (unlike `check_latest_block_in_tenure`, which at least falls back to the node's `get_tenure_tip`), a single actor who can influence *when a specific signer* receives the sortition relative to when that same signer approved the prior tenure's block — e.g., by manipulating block/tenure-change propagation timing toward that signer, or by delaying/hastening its own block-commit broadcast — can make `proposal_to_sortition` come out under `first_proposal_burn_block_timing` for that signer even though, from a globally-fair vantage point, the prior tenure's block was not "poorly timed" and should not be reorgable. This is the direct analog of pulling `sqrtPriceX96` from `slot0`: a manipulable instantaneous reading is substituted for a trustworthy, validated measurement, and the manipulation window needed is a single block/tenure transition — not a majority of signers, not a compromised key.

Once `check_parent_tenure_choice` returns `Ok(true)` for that tenure, the tenure is marked superseded via `record_superseded_tenure` → `SignerDb::mark_tenure_superseded`, permanently (while the permitting sortition stays canonical) exempting any signature this signer already placed in the superseded tenure from blocking a competing proposal, per `reorg_permit_stands`'s use in the pre-commit signing path. [5](#0-4) [6](#0-5) 

This breaks the "approved-parent vs canonical" equality this signer is supposed to maintain: a tenure it previously locally/globally approved can be treated by this signer as fair game for reorg, and it can go on to sign a conflicting block for the new tenure, purely as a result of a local timing artifact the block proposer can steer.

### Impact Explanation
This falls under Critical: a signer can be induced to sign a conflicting/non-canonical block (approving a tenure that reorgs one it already approved) because the permit-to-reorg decision hinges on a local, single-observer, attacker-influenceable timestamp comparison rather than a value that is validated or agreed upon by the broader signer set or the canonical chain state.

### Likelihood Explanation
No majority of signers, stolen key, or local/auth-token access is required — only the ability (available to whoever controls block/tenure-change propagation timing, i.e., the acting miner or a well-positioned relay) to shift when a particular signer perceives the new sortition relative to when it approved the prior tenure's block. This is directly analogous to sandwiching a single transaction: the manipulation window is one tenure transition observed by one victim signer.

### Recommendation
Do not gate reorg permission on a signer-local receive/approval timestamp difference. Instead, derive the "was this tenure's first block poorly timed" determination from data that cannot be skewed per-victim-signer — e.g., the burn block's own timestamp/height relative to when the tenure's first block was mined according to the canonical burnchain/stacks-node view (already partially available via `first_block_mined`/`get_tenure_forking_info`), or require agreement across a threshold of signers' locally observed timings before treating a reorg as permitted, mirroring the pre-commit quorum used elsewhere in the protocol.

### Proof of Concept
1. Miner M controls (or is favorably positioned relative to) propagation timing toward signer S.
2. Tenure T1 produces a block B1; S approves B1 and stamps `approved_time = t0` in its local `BlockInfo`.
3. M delays broadcasting/propagating the new sortition/burn block specifically to S (or otherwise arranges that S's `get_burn_block_receive_time` for the new sortition lands close to `t0`), while other signers receive it at the "normal" time.
4. M proposes a tenure-start block T2 that does not build on T1, but on an earlier sortition.
5. S's `check_parent_tenure_choice` computes `proposal_to_sortition = sortition_state_received_time - t0`, which — due to the engineered delay — is less than `first_proposal_burn_block_timing`, even though objectively T1 was not "poorly timed."
6. S incorrectly treats the reorg of T1 as permitted, marks T1 superseded, and becomes willing to sign a block for T2 that conflicts with the block it already approved for T1. [1](#0-0)

### Citations

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

**File:** docs/signer-flows.md (L156-158)
```markdown
Timestamps: `approved_time` is stamped at pre-commit _or_ local acceptance
(first wins), `signed_self` only when we sign, `signed_group` when the group
threshold is observed.
```

**File:** stacks-signer/src/v0/signer.rs (L1374-1392)
```rust
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
