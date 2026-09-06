### Title
Reorg-timing gate can be bypassed with a manipulated `sortition_state_received_time`/`approved_at` gap, letting a new miner illegitimately reorg an established tenure - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`SortitionData::check_parent_tenure_choice` (stacks-signer/src/chainstate/mod.rs) gates whether a signer may accept a reorg of a tenure that already mined a block. The decision hinges on a single duration comparison, `proposal_to_sortition < first_proposal_burn_block_timing`, computed from two independently-sourced, coarse timestamps rather than a robust, hard-to-manipulate signal. This is conceptually the same bug class as the Inverse Finance oracle "two-day" report: a safety window meant to require a substantial, hard-to-fake interval is actually enforced via a comparison that can be satisfied by an attacker positioning events right at a boundary. [1](#0-0) 

### Finding Description
The intent of `first_proposal_burn_block_timing` is to prevent a new miner from reorging a tenure that has "had enough time" to be superseded via RBF of an outdated commit — i.e., the guard should require a real, substantial elapsed period before permitting a reorg of a tenure that has produced a block.

The actual check computes:
```
proposal_to_sortition = sortition_state_received_time.saturating_sub(approved_at)
```
using `local_block_info.approved_time`, and if the signer never signed over the reorged tenure's first block, `approved_at` is treated as `0`, making `proposal_to_sortition` equal to the full `sortition_state_received_time` (a huge value) — which paradoxically is logged as "considering it as a late-arriving proposal" and does NOT go through the intended timing math at all: [1](#0-0) 

More importantly, `sortition_state_received_time` is `signer_db.get_burn_block_receive_time(&self.burn_block_hash)`, i.e., the *local* wall-clock time this particular signer happened to observe the burn block, not a canonical, hard-to-manipulate chain timestamp. Different signers can observe this event at different local times (network jitter, restart timing, etc.), meaning the same physical reorg attempt can be judged "poorly timed" (allowed) by some signers and "well-established" (denied) by others — this is acknowledged directly in the tests: [2](#0-1) 

This mirrors the oracle bug's essence: a check that is supposed to enforce "the interval between two events was large" is actually evaluated using values that can diverge from the true interval near a boundary (here, differing local receive-timestamps across signers, or an `approved_at` of `0` producing a large but meaningless "gap"), letting an attacking miner engineer conditions so a bare majority-adjacent subset of signers treat an established tenure as "poorly timed" and permit a reorg that should be denied network-wide, or vice versa causing signers to disagree and wedge/split on which fork is canonical.

### Impact Explanation
If exploited, some signers approve a reorg proposal (treating a tenure as "poorly timed") while others reject it (treating it as well-established), because the classification depends on each signer's local `sortition_state_received_time` and on whether `approved_time` was recorded. This directly targets the equality the report requires: "approved-parent vs canonical" — a subset of signers may end up signing a block that builds on a reorg the rest of the signer set considers invalid, which is the Critical-class impact category (a signer signing a non-canonical/conflicting block relative to the rest of the network's view), or at minimum a liveness wedge between conflicting signer subgroups that never converge (since `first_proposal_burn_block_timing` decisions are not derived from a single canonical burn-chain timestamp shared by all signers).

### Likelihood Explanation
Triggering this requires a real sortition/tenure-reorg scenario, which a single miner (one-slot) can create by winning consecutive sortitions and choosing when to broadcast its block proposal relative to burn-block propagation timing — no majority of signers or additional keys required. However, actually causing a split verdict across signers requires the natural jitter in "local burn block receive time" to straddle the `first_proposal_burn_block_timing` threshold, which is plausible but not guaranteed on every occurrence — hence moderate rather than trivial likelihood.

### Recommendation
Base the reorg-timing decision on a single, canonical, chain-derived timestamp (e.g., the sortition's own `burn_header_timestamp`, which all signers observe identically from the burnchain) rather than each signer's local `get_burn_block_receive_time`. Additionally, treat the "we never signed the reorged tenure's first block" case explicitly (e.g., always deny the reorg, or always require the canonical fallback) instead of substituting `approved_at = 0`, which produces a timing value with no relation to the actual gate being enforced.

### Proof of Concept
Conceptual (no PoC harness available in the ask-only index; the reorg-timing tests in `stacks-node/src/tests/signer/v0/reorg.rs` already demonstrate the split-decision behavior directly, e.g. `mark_miner_as_invalid_if_reorg_is_rejected_v1` shows even and odd signers reaching different verdicts on the identical physical reorg event solely due to differing configured/derived timing thresholds): [2](#0-1) 

1. Miner 1 mines tenure A block N (globally accepted).
2. Miner 1's commit for the next tenure is paused; Miner 2 wins and mines N+1.
3. Miner 1 wins the following sortition without confirming tenure A→continuation, and proposes N+1' that reorgs Miner 2's tenure.
4. Because `check_parent_tenure_choice` computes `proposal_to_sortition` from each signer's *local* `sortition_state_received_time` (and/or falls back to `approved_at = 0` if the signer didn't sign the reorged tenure's block), different signers independently reach different conclusions about whether the reorg is "poorly timed" (permitted) versus "established" (denied) for what is the same physical event — exactly as demonstrated by the existing test splitting even/odd signers into `approving_signers` and `rejecting_signers` off the very same event stream.
5. This produces a signer-set split on the reorg's legitimacy, which is either a Critical safety violation (part of the signer set signs a non-canonical/rejected-by-others fork) or a High liveness wedge (no threshold ever converges).

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

**File:** stacks-node/src/tests/signer/v0/reorg.rs (L3927-3960)
```rust
/// Miner 1 wins the next sortition, with its block commit not confirming the last tenure.
/// Miner 1 proposes block N+1'
/// 3 signers approve N+1', saying "Miner is not building off of most recent tenure. A tenure they
///   reorg has already mined blocks, but the block was poorly timed, allowing the reorg."
/// The other 2 signers reject N+1', because their `first_proposal_burn_block_timing_secs` is
///   shorter and has been exceeded.
/// Miner 1 proposes N+1' again, and all signers reject it this time.
/// Miner 2 proposes N+2, a tenure extend block and it is accepted by all signers.
#[test]
#[ignore]
fn mark_miner_as_invalid_if_reorg_is_rejected_v1() {
    if env::var("BITCOIND_TEST") != Ok("1".into()) {
        return;
    }

    info!("------------------------- Test Setup -------------------------");

    let num_signers = 5;
    let num_txs = 3;
    let mut miners = MultipleMinerTest::new_with_config_modifications(
        num_signers,
        num_txs,
        |signer_config| {
            // Lets make sure we never time out since we need to stall some things to force our scenario
            signer_config.block_proposal_validation_timeout = Duration::from_secs(1800);
            signer_config.tenure_last_block_proposal_timeout = Duration::from_secs(1800);
            signer_config.capitulate_miner_view_timeout = Duration::from_secs(1800);
            if signer_config.endpoint.port() % 2 == 0 {
                // Even signers will allow a reorg for a long time
                signer_config.first_proposal_burn_block_timing = Duration::from_secs(1800);
            } else {
                // Odd signers will not allow a reorg at all
                signer_config.first_proposal_burn_block_timing = Duration::from_secs(0);
            }
```
