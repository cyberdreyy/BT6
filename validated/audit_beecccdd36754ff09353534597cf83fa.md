## Analysis

Mapping the CDP-closing "grace-period gaming" bug class onto the signer codebase, the closest reachable analog is the "poorly timed tenure" reorg-permission check in `check_parent_tenure_choice`, which — exactly like the ebtc grace period — decides whether a state transition (here: discarding an already-signed, globally-accepted tenure) is permitted based on a timing window, and that window is computed from data a single miner directly controls.

### Title
Miner-controlled proposal-broadcast delay lets a single miner manufacture a "poorly-timed" reorg permit for its own already-globally-accepted block - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
`check_parent_tenure_choice` decides whether a new tenure is allowed to reorg away a prior tenure's blocks by comparing `sortition_state_received_time` (when this signer saw the next burn block) against `local_block_info.approved_time` (when this signer approved the prior tenure's first block), via `proposal_to_sortition = sortition_state_received_time.saturating_sub(approved_at)`. If that gap is below `first_proposal_burn_block_timing`, the prior tenure is judged "poorly timed" and the reorg is sanctioned (`mark_tenure_superseded`), voiding the signers' own prior signature as a future conflict. [1](#0-0) 

### Finding Description
`approved_time` is stamped locally by each signer at the moment it pre-commits or locally accepts a proposal — i.e., at the moment the miner chooses to broadcast that proposal. [2](#0-1) 

A miner who wins a tenure (T1) fully controls when it broadcasts T1's block. If the miner deliberately withholds broadcasting T1's sole block until just before it (or an immediately-following winner) can propose the next tenure (T2), then for every signer `approved_time` will be very close to `sortition_state_received_time`, making `proposal_to_sortition` small regardless of how much real wall-clock time T1's tenure actually had available. The check only refuses the reorg if the reorged tenure has more than one globally-accepted block: [3](#0-2) 

So as long as the miner keeps T1 to exactly one (fully signed, globally-accepted, canonical) block, the "poorly timed" branch is reachable purely by the miner's own broadcast-timing choice: [1](#0-0) 

This mirrors the report's core defect: a threshold/grace-period check ("was this tenure established long enough to have been RBF'd fairly?") is evaluated using a value the attacker themselves can steer (their own broadcast delay) rather than a canonical, attacker-independent signal, just as the BCCR TCR check could be walked across its threshold by an attacker's own preceding action (closing a CDP) rather than an independent market event. The config documentation for this exact parameter states the intended semantics are being subverted: [4](#0-3) 

The result: `validate_tenure_change_payload` (v1) / (v2) accepts a tenure-change block that reorgs past a canonical, globally-accepted block, and signers sign it, because `check_parent_tenure_choice` returned `true`: [5](#0-4) [6](#0-5) 

### Impact Explanation
This breaks the "approved-parent vs canonical" equality the signer set is supposed to enforce: signers end up signing a tenure-change block that discards a block they themselves already fully signed and that reached global acceptance (i.e., a canonical block with possibly-confirmed user transactions), on the basis of a "poor timing" signal the attacker manufactured rather than one reflecting genuine RBF unfairness. Per the stated impact categories, this is a signer signing a non-canonical/conflicting block relative to what the protocol's own reorg-fairness invariant should have produced — Critical.

### Likelihood Explanation
It requires only a single miner to (a) win a tenure slot, (b) choose to delay broadcasting its own sole block until near the tenure's end (fully within that miner's unilateral control — no other signer or miner cooperation needed to trigger the "poorly timed" branch's inputs), and (c) win (or have) the immediately following sortition propose a reorging tenure-change block. Part (c) needs the miner to also produce the next tenure, which is a realistic scenario for any miner with recurring block-production chances (no majority-of-signers or majority-of-miners assumption is required — this is achievable by ordinary sortition luck over repeated rounds, not stake/majority control). No StackerDB/transport timing tricks against other signers are needed: every signer independently computes the same biased inputs from the miner's own delayed broadcast.

### Recommendation
Do not let a miner-controlled broadcast timestamp (`approved_time`) alone decide whether a tenure was "poorly timed" for reorg purposes. Anchor the "poorly timed" judgment to a value the reorged tenure's miner cannot freely choose — e.g., the time the *sortition* that started the reorged tenure was received relative to the *next* sortition (independent of when the miner chose to broadcast its block), or require additional corroboration (such as the elapsed time since the reorged tenure's sortition itself, not since the signer's proposal approval) before granting the reorg permit in `check_parent_tenure_choice`.

### Proof of Concept
1. Miner M wins the sortition for tenure T1.
2. M does not broadcast T1's block proposal immediately; it waits, holding the proposal back for as long as `block_proposal_timeout` allows without being marked as an inactive/invalid miner.
3. Signers receive and sign T1's sole block late; each signer's `approved_time` (stamped in `mark_pre_committed`/`mark_locally_accepted`, per [2](#0-1) ) is now close to real time.
4. T1's block reaches global acceptance (one globally-accepted block in T1 — satisfies the `globally_accepted_blocks > 1` guard at [3](#0-2) ).
5. M (having also won, or arranged to win, the immediately-following sortition) proposes tenure T2's tenure-change block with `prev_tenure_consensus_hash` pointing past T1 (`parent_tenure_id != prior_sortition`).
6. Each signer computes `proposal_to_sortition = sortition_state_received_time - approved_time` (small, by construction) at [7](#0-6)  and, finding it under `first_proposal_burn_block_timing`, sanctions the reorg and marks T1 superseded.
7. Signers sign T2's tenure-change block, discarding T1's canonical, globally-accepted block, even though T1's tenure was not genuinely "poorly timed" from the network's perspective — only from the perspective of M's self-chosen broadcast delay.

### Citations

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

**File:** stacks-signer/src/chainstate/mod.rs (L247-274)
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
```

**File:** docs/signer-flows.md (L156-162)
```markdown
Timestamps: `approved_time` is stamped at pre-commit _or_ local acceptance
(first wins), `signed_self` only when we sign, `signed_group` when the group
threshold is observed.

> Anchors: `BlockInfo::check_state`, `move_to`, `mark_pre_committed`,
> `mark_locally_accepted`, `mark_globally_accepted`, `mark_locally_rejected`,
> `mark_globally_rejected` (signerdb.rs)
```

**File:** sample/conf/signer/mainnet-signer-conf.toml (L149-166)
```text
# Reorg protection window. Measures the time between when the first block
# of a tenure was signed and when the next burn block (sortition) arrived.
#
# If a new miner tries to reorg a tenure that already produced blocks:
#   - If (burn_block_received - first_block_signed) < this value:
#     Reorg is ALLOWED (the tenure was "poorly timed" and the incoming
#     miner did not have sufficient time to RBF an outdated commit)
#   - If (burn_block_received - first_block_signed) >= this value:
#     Reorg is DENIED (the tenure was established long enough for the
#     incoming miner to RBF any outdated commit)
#
# WARNING: Setting this too LOW allows dangerous reorgs of established
# tenures. Setting it too HIGH blocks legitimate miner handoffs when
# the previous tenure's first block arrived shortly before the sortition.
#
# Default: 60
# Units: seconds
# first_proposal_burn_block_timing_secs = 60
```

**File:** stacks-signer/src/chainstate/v1.rs (L483-504)
```rust
        // Ensure that the tenure change block confirms the expected parent block
        let confirms_expected_parent = SortitionData::check_tenure_change_confirms_parent(
            tenure_change,
            block,
            signer_db,
            client,
            self.config.tenure_last_block_proposal_timeout,
            self.config.reorg_attempts_activity_timeout,
        )
        .map_err(SignerChainstateError::from)?;
        if !confirms_expected_parent {
            return Err(RejectReason::InvalidParentBlock);
        }
        // now, we have to check if the parent tenure was a valid choice.
        let is_valid_parent_tenure = proposed_by.state().data.check_parent_tenure_choice(
            signer_db,
            client,
            &self.config.first_proposal_burn_block_timing,
        )?;
        if !is_valid_parent_tenure {
            return Err(RejectReason::ReorgNotAllowed);
        }
```

**File:** stacks-signer/src/chainstate/v2.rs (L327-339)
```rust
        // Ensure that the tenure change block confirms the expected parent block
        let confirms_expected_parent = SortitionData::check_tenure_change_confirms_parent(
            tenure_change,
            block,
            signer_db,
            client,
            config.tenure_last_block_proposal_timeout,
            config.reorg_attempts_activity_timeout,
        )
        .map_err(SignerChainstateError::from)?;
        if !confirms_expected_parent {
            return Err(RejectReason::InvalidParentBlock);
        }
```
