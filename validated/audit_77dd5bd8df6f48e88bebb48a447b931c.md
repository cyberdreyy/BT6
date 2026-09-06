### Title
Improper Input Validation in Signer Reorg-Timing Check Allows Bypass of the Anti-Reorg Safety Gate - (`stacks-signer/src/chainstate/mod.rs`)

### Summary
`SortitionData::check_parent_tenure_choice` decides whether a signer is allowed to sanction a miner's tenure reorg. Analogous to the go-ethereum `TraceChain` bug (CVE-2018-16733), which never checks that the caller-supplied "end" value is actually after the "start" value before using the range, this function computes a duration between two locally-recorded timestamps with `saturating_sub` and never verifies that the later-in-time event (`sortition_state_received_time`) actually occurred after the earlier-in-time event (`approved_at`). If message delivery/gossip timing lets the events arrive out of the assumed order, the subtraction silently clamps to `0` instead of signalling an invalid/ordering error, which causes the reorg-timing test to always evaluate as "poorly timed," incorrectly granting reorg permission.

### Finding Description
`check_parent_tenure_choice` (`stacks-signer/src/chainstate/mod.rs`) is the safety gate that decides whether a miner may reorg a prior tenure that already produced blocks: [1](#0-0) 

```rust
let checked_proposal_timing = if let Some(sortition_state_received_time) =
    sortition_state_received_time
{
    let proposal_to_sortition = if let Some(approved_at) =
        local_block_info.approved_time
    {
        sortition_state_received_time.saturating_sub(approved_at)
    } else {
        info!("We did not sign over the reorged tenure's first block, considering it as a late-arriving proposal");
        0
    };
    if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
        // ... permit the reorg, mark_tenure_superseded ...
```

The intended invariant is that `sortition_state_received_time` (when this signer observed the burn block that starts the *new*, reorging tenure) is always chronologically after `approved_at` (when this signer approved/signed the reorged tenure's first block). The code never validates this ordering — it simply subtracts with `saturating_sub`, which returns `0` for any case where `approved_at >= sortition_state_received_time`, exactly mirroring the missing `end > start` check in the geth advisory. A `0`-second gap is always `< first_proposal_burn_block_timing` (default 60s, see `stacks-signer/src/chainstate/mod.rs` config docs and `sample/conf/signer/mainnet-signer-conf.toml:149-166`), so the "poorly timed, allow the reorg" branch is taken unconditionally whenever the two timestamps are reversed or coincide.

This ordering reversal is directly reachable by a single miner controlling proposal/gossip pacing (no majority, no other signer's key required): if delivery of the earlier tenure's block proposal to one signer is delayed (network lag, deliberate throttling by the malicious miner, or a temporarily disconnected signer catching up), that signer's `mark_locally_accepted`/`approved_time` for the earlier tenure's block can be recorded *after* it already observed (via its own burnchain monitoring, independent of the signer/proposal network) the burn block for the new, reorging sortition. From that signer's point of view, `approved_at > sortition_state_received_time`, the subtraction clamps to `0`, and the anti-reorg gate is bypassed regardless of how well-established (`get_globally_accepted_block_count_in_tenure > 1` still blocks it, but a single globally-accepted block is enough to trigger this path) the reorged tenure actually was.

### Impact Explanation
This breaks the "approved-parent vs canonical" equality that `check_parent_tenure_choice` is meant to enforce. A signer that should refuse to sanction a reorg of a tenure it already signed can be tricked, purely through timing/gossip manipulation by the block-proposing miner, into calling `mark_tenure_superseded` and voting to accept a conflicting/non-canonical tenure-change block — i.e., a signer signing off on an invalid/conflicting reorg. Per the scan's Impact taxonomy this is a Critical-class outcome (a signer signing a non-canonical/conflicting block via a broken equality check), though it is bounded to the affected signer's own vote weight, so its blast radius depends on how many signers experience the same delayed-delivery condition.

### Likelihood Explanation
Reachable by a single miner (plus gossip control) without needing a majority of signers, another signer's key, or local/auth access — matching the in-scope threat model. It requires the attacker-miner to engineer relative delivery/processing delay for one signer's proposal-approval versus that signer's independent burnchain-tip observation, which is plausible under normal network jitter or deliberate targeted delay of gossip/StackerDB messages to a specific signer, but is probabilistic/timing-dependent rather than deterministic, which lowers likelihood relative to a purely deterministic logic bug.

### Recommendation
In `check_parent_tenure_choice`, before computing `proposal_to_sortition`, explicitly check that `sortition_state_received_time >= approved_at`; if the ordering is violated, treat it as an error/insufficient-information case (fail closed toward "reorg not permitted") rather than silently clamping to `0` via `saturating_sub`. This mirrors the fix pattern for CVE-2018-16733 — validate that the "end" reference point is not before the "start" reference point before deriving a range/duration from them.

### Proof of Concept
1. Miner M mines tenure A's first (and only) block; it is proposed to signer S but delivery/processing to S is delayed (e.g., S is briefly partitioned from StackerDB or the message is queued behind other traffic).
2. Independently, S's node observes the next Bitcoin block and records the burn block for the sortition that starts tenure B via `insert_burn_block` — this happens through S's own burnchain monitoring, not through the proposal network, so it is unaffected by the delay in step 1.
3. S finally processes and signs tenure A's block via `mark_locally_accepted`, setting `approved_time` to a wall-clock value that is now *after* the burn-block receive time already recorded for tenure B's sortition.
4. Miner M (or a colluding successor) now proposes a tenure-change block for tenure C claiming to reorg past tenure A instead of building on B. When S evaluates `check_parent_tenure_choice` for this proposal, `sortition_state_received_time.saturating_sub(approved_at)` evaluates to `0`, which is `< first_proposal_burn_block_timing`, so S treats tenure A as "poorly timed" and sanctions the reorg (`mark_tenure_superseded`), voting to accept a block that reorgs a tenure it had already signed — despite tenure A not actually having been poorly timed in real chain-time terms.

### Citations

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
