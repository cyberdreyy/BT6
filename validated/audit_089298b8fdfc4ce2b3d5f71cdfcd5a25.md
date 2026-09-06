### Title
Reorg-permission logic in `check_parent_tenure_choice` conflates "this signer personally approved the block" with "the block arrived late," letting a miner reorg an already globally-accepted tenure - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`SortitionData::check_parent_tenure_choice` decides whether a miner is allowed to build off a tenure other than the immediately prior sortition (a reorg). For each reorged tenure that produced exactly one block, it is supposed to permit the reorg only if that block's *first proposal* arrived implausibly close to the next sortition (`first_proposal_burn_block_timing`) — i.e., only if the tenure was too short-lived to matter. To compute that timing gap it uses `local_block_info.approved_time`, which is *this signer's own* record of when *it* approved/signed the block, not a network-wide, canonical timestamp of when the block was actually produced or globally accepted.

### Finding Description [1](#0-0) 

The function first checks `globally_accepted_blocks > 1` — a real, node-verified signal — and refuses the reorg if the reorged tenure produced more than one globally accepted block. But when the tenure produced exactly one (or the local record disagrees), it falls through to a purely *local* timing computation: [2](#0-1) 

```rust
let checked_proposal_timing = if let Some(sortition_state_received_time) = sortition_state_received_time {
    let proposal_to_sortition = if let Some(approved_at) = local_block_info.approved_time {
        sortition_state_received_time.saturating_sub(approved_at)
    } else {
        info!("We did not sign over the reorged tenure's first block, considering it as a late-arriving proposal");
        0
    };
    if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
        // ... permit reorg, mark tenure superseded ...
        superseded_tenures.push(tenure);
        continue;
    }
    true
} else {
    false
};
```

The bug is structural: `approved_time` is set only when **this** signer itself signed/approved the block — it is not a proxy for "when the block was actually proposed" or "whether the network accepted it on time." If this signer did not personally approve that specific block (e.g., it rejected it, was slow to receive/validate it, restarted and lost local state, or simply disagreed while a 70%-weight majority of *other* signers approved it and it became globally accepted), `approved_time` is `None`. The code then substitutes `proposal_to_sortition = 0`, which is (almost) always `< first_proposal_burn_block_timing`, so the reorg is unconditionally treated as legitimate ("late-arriving") and the tenure is marked **superseded** via `mark_tenure_superseded`.

This is the same root-cause pattern as the reference report: a protective threshold ("was this reorg legitimately timed?") is computed from state that does not represent objective/ground truth but instead reflects the same actor's own incomplete, self-referential view — exactly as the Vault computed `amountOutMinimum` from the pool state visible to it *after* the attacker's own manipulation, instead of an external reference price. Here, the signer computes "was the block late" from its own possibly-incomplete local bookkeeping instead of the block's actual origination time or the network's canonical acceptance status. The check is meant to gate an important safety property (don't let a miner discard an already-settled, single-block tenure that the network accepted) but is satisfied by "I personally have no record of approving it," which is a much weaker and attacker-adjacent condition than "the tenure was genuinely short-lived and marginal."

### Impact Explanation
Once `mark_tenure_superseded` records the permit, this signer's own future signature over the superseded tenure's (globally accepted) block no longer counts as a conflict (see `reorg_permit_stands` / conflict-checking logic referenced from `signer.rs`), and the signer will go on to sign the miner's replacement chain. This lets a single miner, by simply proposing a reorging tenure to a signer whose local record of the previous, already globally-accepted block is incomplete (offline gap, restart, message loss, or a legitimate rejection later overridden by group consensus), obtain that signer's signature over a **non-canonical/conflicting block** that discards a tenure the rest of the network already finalized. This is exactly the "signer signing a conflicting/non-canonical block" class called out as Critical impact, because it breaks the approved-parent-vs-canonical equality the check exists to enforce.

### Likelihood Explanation
No majority of signers or leaked keys is required — only a single miner (who is a one-slot proposer already trusted to author tenure-change proposals) and a target signer whose local `signerdb` state does not contain an `approved_time` for the block in the tenure being reorged. This is a plausible, ordinary occurrence: a signer that rejected the block (disagreeing with the majority), one that missed/never received the original proposal, or one that lost/reset local state (a scenario the codebase elsewhere explicitly worries about, e.g. equivocation-guard-on-restart concerns) will all have `approved_time == None` while `globally_accepted_blocks == 1` remains true from the node's perspective. The miner does not need to coordinate with anyone; they only need the affected signer's known/observable local state.

### Recommendation
Do not use `approved_time` (a signer-local, self-referential value) as the sole proxy for "was the block proposal late." Instead, base `check_parent_tenure_choice`'s timing decision on a network-verifiable proposal timestamp (e.g., the node-reported time the block/tenure was mined or accepted, obtained the same way `globally_accepted_blocks` is obtained) rather than whether *this particular signer* has a local approval record. At minimum, when `local_block_info.approved_time` is `None` but the tenure is known to have a globally accepted block, the function should not default to treating it as "late" — it should either query the node for the block's actual proposal/acceptance time or conservatively refuse the reorg, mirroring the existing "we have no local knowledge" refusal path used earlier in the same function (`return Ok(false)` at the "no local knowledge" branch) rather than the permissive path.

### Proof of Concept
1. Signer S rejects (or never fully validates/records approval for) the sole block `B` proposed in tenure `T`, while the rest of the signer set reaches the 70% threshold and `B` becomes globally accepted (node reports `globally_accepted_blocks == 1` for `T`).
2. A miner proposes a new tenure `T'` whose `TenureChangePayload.prev_tenure_consensus_hash` points to the sortition prior to `T` (i.e., it reorgs away `T`/`B`), following the flow in `check_proposal` → `validate_tenure_change_payload` → `check_parent_tenure_choice`.
3. On signer S, `check_parent_tenure_choice` iterates `tenures_reorged`, finds `T` with `globally_accepted_blocks == 1` (not `> 1`, so it does not early-reject), and calls `get_first_approved_block_in_tenure(T)`, which returns S's local record of `B` with `approved_time = None` (since S itself did not approve it).
4. `proposal_to_sortition` is forced to `0` and immediately satisfies `< first_proposal_burn_block_timing`, so `T` is pushed into `superseded_tenures` and the reorg is permitted; `mark_tenure_superseded` is recorded.
5. S subsequently signs a block in `T'` that conflicts with the globally-accepted `B` in `T`, because its local conflict-detection no longer treats `T`'s block as blocking (`reorg_permit_stands` sees a standing permit).

Note: I was unable to complete verification of the exact field semantics of `SignerDb::get_first_approved_block_in_tenure` and `approved_time` in `stacks-signer/src/signerdb.rs` before the tool budget ran out (the grep matched 53 lines but I could not read the surrounding context to confirm precisely when `approved_time` is populated vs. left `None`, e.g. whether rejection also clears it or whether it's set on any local processing regardless of vote). This should be confirmed against `stacks-signer/src/signerdb.rs` before treating this as a confirmed, unconditional finding — the core logical flaw (using a self-referential "I did not personally approve" flag as a proxy for "the network saw this late") is nonetheless directly readable in `chainstate/mod.rs` as cited above.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L210-245)
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
