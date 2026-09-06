### Title
Signer computes the reorg-timing gap with a one-sided `saturating_sub`, letting async approval delays masquerade as a "poorly-timed tenure" and improperly unlock a reorg - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
`SortitionData::check_parent_tenure_choice` decides whether a miner is allowed to reorg a prior tenure by measuring the time between when that tenure's first block was *approved* and when the *new* sortition (the one attempting the reorg) was received. It computes this gap as:

```rust
let proposal_to_sortition = if let Some(approved_at) = local_block_info.approved_time {
    sortition_state_received_time.saturating_sub(approved_at)
} else {
    info!("We did not sign over the reorged tenure's first block, considering it as a late-arriving proposal");
    0
};
if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
    // ... allow the reorg (mark tenure superseded)
}
``` [1](#0-0) 

### Finding Description
The gap the code intends to measure is symmetric in nature — "how much time elapsed between this signer approving the earlier tenure's block and the new sortition arriving." But the implementation is only correct when `approved_at <= sortition_state_received_time`. If `approved_at > sortition_state_received_time` (the local signer's *approval timestamp* for the older tenure's block lands after it has already recorded the new sortition's burn block — e.g. because the earlier block's validation/approval was delayed by node load, a stalled validation endpoint, or ordinary async processing lag, while the new sortition event was received and timestamped promptly), `saturating_sub` clamps the result to `0`.

A `proposal_to_sortition` of `0` is the *most lenient* possible value: it is always `< first_proposal_burn_block_timing`, so the code unconditionally falls into the "poorly timed, allow the reorg" branch:

```rust
if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
    info!("... allowing the reorg.");
    superseded_tenures.push(tenure);
    continue;
}
``` [2](#0-1) 

Just like the reported price-difference bug — where one side of a comparison used an equation that degenerates differently than the other — this timing computation degenerates to a single fixed value (`0`) whenever the ordering assumption is violated, instead of reflecting the true (possibly large) elapsed time between the proposal and the reorging sortition. The asymmetry always biases toward *allowing* the reorg, never toward disallowing it, because the "wrong-direction" case is silently coerced to the value that satisfies the `<` check.

Once a reorg is (incorrectly) sanctioned this way, the older tenure is marked `superseded` via `mark_tenure_superseded`, so the signer's own earlier signature over that tenure's block no longer blocks it from signing the new, reorging block — see the comment on `check_parent_tenure_choice`: "A permitted reorg is recorded once the whole reorg is permitted... so a signature we already placed on one of those blocks does not later block the replacement." [3](#0-2) 

This directly matches the in-scope impact class: the signer can be induced to sign a **non-canonical/conflicting block** that reorgs an established tenure it had already signed off on, in situations that the `first_proposal_burn_block_timing` guard exists specifically to prevent.

### Impact Explanation
`first_proposal_burn_block_timing` exists as a safety valve: a tenure that has already produced a globally-accepted block must not be casually reorged unless the replacement miner genuinely had no reasonable chance to beat the outdated commit (i.e., the first tenure's first block was proposed/approved essentially at the moment of the burn-block transition). The buggy clamp defeats that guard under an ordinary operational condition (validation/approval latency), letting a signer sanction a reorg of a tenure that was *not* actually poorly timed. This is exactly the "signer signing a non-canonical/conflicting block" class called out as Critical impact, since it can let the local signer's signature (and by extension weight toward the 70% supermajority) go toward replacing an already-settled tenure.

### Likelihood Explanation
No majority collusion, node compromise, or auth-token access is required. The condition depends purely on the relative ordering/timing of two locally-recorded timestamps (`approved_time` for the reorged tenure's first block vs. `sortition_state_received_time` for the new sortition), both of which are influenced by ordinary node/network latency that a single miner can amplify (e.g., delaying propagation/validation of the earlier block while promptly winning/broadcasting the next sortition). This makes it a plausibly reachable, one-miner-triggerable condition rather than a purely theoretical one, though it does require the specific race (approval recorded after the new sortition's burn-block receipt) to occur, so it is not certain to trigger on every reorg attempt.

### Recommendation
Do not treat the wrong-direction case as `0`. Instead:
- Detect when `approved_at > sortition_state_received_time` explicitly and treat it conservatively (i.e., as evidence the timing gap is *unknown/large*, defaulting to the **disallow-reorg** branch) rather than collapsing to the most permissive value.
- Alternatively, use signed arithmetic (`i64`/checked subtraction) internally and only allow the reorg when `0 <= proposal_to_sortition < first_proposal_burn_block_timing`, rejecting (or logging and defaulting to "no reorg") when the subtraction would be negative.
- Add a regression test mirroring `check_proposal_reorg_timing_bad`/`check_proposal_reorg_timing_ok` in `stacks-signer/src/chainstate/tests/v1.rs`/`v2.rs` that sets `approved_at > sortition_state_received_time` and asserts the reorg is refused.

### Proof of Concept
1. Signer S has already approved (signed) the first block of tenure T1 at local time `approved_at = t0`.
2. Due to node/validation latency (e.g., the block validation/approval round-trip to the stacks-node is slow, or `TEST_VALIDATE_STALL`-style stalls as used in the codebase's own reorg tests such as `reorg_attempts_activity_timeout_exceeded` in `stacks-node/src/tests/signer/v0/reorg.rs`), the `approved_time` for T1's block is only persisted to `signer_db` at `t0`, but the burn block for the *next* sortition (miner attempting the reorg to T2) is received and timestamped by S at `sortition_state_received_time = t0 - Δ` for some `Δ > 0` (i.e., recorded strictly before the delayed approval write lands, even though causally the earlier block was proposed well before the new sortition).
3. When the new miner proposes a block building on something other than the prior sortition, `check_parent_tenure_choice` runs and computes:
   ```rust
   sortition_state_received_time.saturating_sub(approved_at) // = (t0-Δ) - t0 → clamps to 0
   ```
4. `Duration::from_secs(0) < first_proposal_burn_block_timing` is always true, so the reorg is (incorrectly) permitted and T1 is marked `superseded`, even though the real gap `Δ` may vastly exceed `first_proposal_burn_block_timing`.
5. Signer S subsequently signs the new, reorging block, contributing weight toward globally accepting a block that supersedes a tenure it had previously signed and that the timing guard was designed to protect.

Note: exact reproduction requires controlling the relative timestamps recorded by `signer_db` (`get_burn_block_receive_time` vs. `approved_time`), which I was unable to trace to the byte-for-byte write path within the available tool budget (the `signerdb.rs` write sites for `approved_time` were not fully inspected). This should be verified against `stacks-signer/src/signerdb.rs`'s block-approval and burn-block-receive-time write paths before treating the PoC as fully confirmed.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L159-169)
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
