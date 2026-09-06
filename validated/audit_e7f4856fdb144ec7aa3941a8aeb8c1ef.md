### Title
Reorg-timing check in `check_parent_tenure_choice` keys off a locally-observed, attacker-controllable timestamp instead of the tenure's actual proposal time - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`check_parent_tenure_choice` decides whether a reorg of an already-mined tenure is "valid miner behavior" by comparing the new sortition's receive time against `local_block_info.approved_time` — the time *this specific signer* happened to approve/sign the reorged tenure's first block — rather than any tamper-resistant, globally-consistent proposal timestamp. Because the party proposing that earlier tenure is the one controlling when/if the `BlockProposal` message reaches each signer, it can steer `approved_time` to be arbitrarily close to the next sortition, making a genuinely well-timed tenure look "poorly timed" and thereby legitimizing its own supersession.

### Finding Description
In `stacks-signer/src/chainstate/mod.rs` (lines 247-278), for each tenure being potentially reorged:

```
let proposal_to_sortition = if let Some(approved_at) = local_block_info.approved_time {
    sortition_state_received_time.saturating_sub(approved_at)
} else { 0 };
if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
    ...
    superseded_tenures.push(tenure);
    continue;
}
``` [1](#0-0) 

`local_block_info` is fetched via `signer_db.get_first_approved_block_in_tenure`, i.e. it is this signer's own local record of when it approved/signed the reorged tenure's first block — a value that is a function of *message delivery timing to this signer*, not of any commitment the network as a whole can verify. The intent of the rule (per the surrounding comments/log at line 261) is to allow reorging a tenure only if that tenure's block was "poorly timed" (i.e., arrived so close to the next Bitcoin block that it never had a fair chance to be recognized). But the code substitutes "poorly timed" with "approved late by this one signer," which the original proposer of that tenure fully controls by choosing when to gossip its `BlockProposal` to that signer.

The equality the rule is supposed to enforce is:
```
effective_reorg_timing == real_first_proposal_burn_block_timing
```
i.e. the reorg should only be sanctioned when the *true* elapsed time between the tenure's block becoming known and the next sortition is short. Because `approved_time` can be pushed arbitrarily close to `sortition_state_received_time` by delaying delivery of the original proposal (an action fully within the proposer's control, since they are the winning miner and control their own message gossip), the equality breaks: `proposal_to_sortition` can be made small even though `real_first_proposal_burn_block_timing` (time since the block was actually proposed/could have been known) was long. This lets the same actor (winning a later sortition) retroactively legitimize a reorg of their own earlier, genuinely-well-timed tenure via `record_superseded_tenure` → `signer_db.mark_tenure_superseded`, which is documented to remove the conflict guard so a previously-signed block on that tenure "does not later block the replacement." [2](#0-1) 

No other guard corrects for this: the function only additionally checks that ≤1 block was globally accepted in the reorged tenure (line 211-223), which does not prevent a single, honestly-timed, already-approved block from being reorged if `approved_time` was artificially delayed for the evaluating signer.

### Impact Explanation
This breaks chain safety: `mark_tenure_superseded` removes the conflict guard on a block this signer already approved/signed, allowing the signer to subsequently sign a *conflicting* block for a tenure it had previously endorsed — a reorg deeper than the time-based rule is meant to allow. This matches the Critical category: "a signer signing an invalid, non-canonical, or conflicting block (chain safety)." Because the manipulation only requires control over the proposer's own message-gossip timing for their own tenure, it is repeatable across cycles by any miner who wins consecutive (or near-consecutive) sortitions.

### Likelihood Explanation
The precondition is that the same attacker (or a colluding pair) wins the tenure to be reorged and a later tenure whose `parent_tenure_id` skips back past it. The attacker needs only their own miner slot (to produce and control delivery of the original `BlockProposal`) plus ordinary gossip — no signer majority, no compromised keys, no auth token, and no local access are required, satisfying the "one slot plus gossip" threat model. The main uncertainty is that this requires winning (or otherwise arranging) two sortitions in a favorable order, which is probabilistic rather than guaranteed, but it is not privileged access — it is a standard capability of any miner with enough hash/STX power to occasionally win consecutive slots, and the exploit can be attempted repeatedly.

### Recommendation
Use a timestamp that reflects the tenure's actual, verifiable first-proposal time (e.g., a timestamp embedded/committed in the block or tenure-change payload, or the earliest time *any* signer/majority observed the proposal) rather than this-signer's local `approved_time`, which is a function of message delivery an adversarial proposer controls. At minimum, cross-check `approved_time` against `proposed_time` recorded at first receipt of the raw proposal (before any node-validation latency) and/or require corroboration from multiple signers' observed times before treating a tenure as "poorly timed" for reorg purposes.

### Proof of Concept
Rust test in `stacks-signer/src/chainstate/tests/mod.rs`:
1. Build a `SortitionData` where `parent_tenure_id` points to a tenure two sortitions back (`self.prior_sortition != self.parent_tenure_id`), triggering the reorg-check path.
2. In `signer_db`, insert a `BlockInfo` for the reorged tenure's first (and only) globally-accepted block with `proposed_time` set far in the past (simulating an honest, well-timed proposal) but `approved_time` set to a value very close to `sortition_state_received_time` (simulating the attacker delaying delivery of the proposal to this signer).
3. Call `check_parent_tenure_choice` and assert it returns `Ok(true)` (reorg permitted) even though the real elapsed time from `proposed_time` to the new sortition exceeds `first_proposal_burn_block_timing`.
4. Assert (via a mirrored computation from `proposed_time`) that `real_first_proposal_burn_block_timing >= first_proposal_burn_block_timing` while `proposal_to_sortition < first_proposal_burn_block_timing`, demonstrating the broken equality `effective_reorg_timing != real_first_proposal_burn_block_timing`.

Note: I was not able to fully verify, due to tool-call limits, the exact code path in `stacks-signer/src/signerdb.rs` that sets `approved_time` versus `proposed_time` (i.e., confirming that `approved_time` is stamped purely on local message receipt/processing rather than derived from a network-wide consistent source). This should be double-checked before treating the finding as fully confirmed, since if `approved_time` were instead derived from a signed/committed timestamp rather than local wall-clock receipt, the attacker's control over it would be reduced.

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
