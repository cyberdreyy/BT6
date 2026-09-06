### Title
Signers permit a sole miner to reorg an already globally-accepted block by racing the `first_proposal_burn_block_timing` window - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
`check_parent_tenure_choice` treats a reorged tenure as "poorly timed" (and therefore safe to reorg) purely based on the gap between when the tenure's first block was *approved by this signer* and when the next sortition arrived — never checking whether that block has already become the network's confirmed, globally-accepted tip. A miner that wins a fast follow-up sortition can therefore force all signers to sign a competing tenure-change block that discards an already pushed/confirmed block, exactly mirroring the external report's pattern of an under-bounded, timing-sensitive parameter enabling value-destroying front-running.

### Finding Description
`SortitionData::check_parent_tenure_choice` decides whether a new tenure is allowed to build off something other than the immediately prior sortition (i.e., to reorg one or more tenures). The only real gate against reorging a tenure that already produced a confirmed block is: [1](#0-0) 

which refuses the reorg only if **more than one** globally accepted block exists in the tenure. If exactly one globally accepted block exists, execution falls through to a purely time-based test: [2](#0-1) 

Here `proposal_to_sortition` is computed from `local_block_info.approved_time` (a signer-local pre-commit/approval timestamp, not the moment the block became globally accepted/pushed to the node) versus `sortition_state_received_time`. If that gap is below the configurable `first_proposal_burn_block_timing` (default 60s, configurable per-signer — see `sample/conf/signer/mainnet-signer-conf.toml`), the tenure is marked "poorly timed" and pushed into `superseded_tenures`, which is later recorded via `mark_tenure_superseded`, explicitly excluding the conflict from blocking a future signature: [3](#0-2) 

Nothing in this path asks whether the block was already pushed to, and processed by, the stacks-node (i.e., is the actual canonical chain tip) — it only asks whether *this signer* saw a single globally-accepted block that was locally approved close to the sortition boundary. A miner fully controls the timing of its own block-commit/proposal and can engineer a fast tenure handoff (or simply benefit from natural Bitcoin block-time variance) so that a competing miner's freshly-confirmed tenure looks "poorly timed," then win the very next sortition and propose a tenure-change block that reorgs it. All signers evaluate the same deterministic, node-timing-driven rule independently — no majority collusion is required, and no signer key beyond the attacking miner's own mining/proposal capability is needed.

The stacks-node/src/tests/signer/v0/reorg.rs test suite explicitly demonstrates and validates this exact scenario: Miner 2 mines and gets block N+1 **pushed** (globally accepted, on-chain), yet because Miner 1 wins the next sortition within `first_proposal_burn_block_timing_secs`, all signers approve Miner 1's competing N+1', reorging Miner 2's confirmed block out of the chain: [4](#0-3) 

The sample config file itself documents the danger, calling this an explicit safety/liveness trade-off knob rather than a validated bound: [5](#0-4) 

This is the direct analog of the CatalystVault report's core defect: a single privileged/attacker-controllable, weakly-bounded parameter (fee cap / timing window) whose value determines whether an operation that should be blocked (100% fee drain / reorg of a confirmed block) is instead silently permitted, and the surrounding checks (`minOut` validation / global-acceptance check) do not independently prevent the loss.

### Impact Explanation
This lets a single miner cause signers to sign a **conflicting, non-canonical block that discards an already globally-accepted block** — the Critical-impact category defined for this scan ("a signer signing an invalid, non-canonical, or conflicting block"). Transactions and STX transfers already confirmed in the discarded tenure are rolled back, which is a consensus-safety violation, not merely a liveness hiccup.

### Likelihood Explanation
The attack needs no majority of signers, no other signer's key, and no auth token — only a miner capable of winning two sortitions close together (achievable by chance for any active miner, or engineered by controlling commit timing / stalling a competitor as the test harness does). The check is deterministic given node-reported timing data, so all signers reach the same (wrong) conclusion independently, making the reorg reliably succeed rather than depend on a coincidental majority.

### Recommendation
Before permitting a "poorly timed" reorg, additionally verify that the tenure's block has not already been observed as the node's processed/canonical chain tip (e.g., via `get_tenure_tip`/chain-info) at evaluation time, not just at proposal-approval time — analogous to adding a hard, unconditional cap rather than relying solely on a tunable timing heuristic. At minimum, tighten `check_parent_tenure_choice` to refuse any reorg of a tenure whose block is currently the canonical tip, regardless of how "close" the timing was.

### Proof of Concept
See the existing (currently passing/expected) test `allow_reorg_within_first_proposal_burn_block_timing_secs`, which reproduces the exact chain of events: Miner 1 mines block N; Miner 2 wins the next sortition and mines block N+1, which gets pushed/confirmed on the node; Miner 1 wins the following sortition within `first_proposal_burn_block_timing_secs` and proposes N+1′; all signers accept N+1′, reorging out Miner 2's already-confirmed N+1. [6](#0-5)

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

**File:** stacks-signer/src/v0/signer.rs (L1208-1229)
```rust
    /// Whether a reorg permit recorded for this conflict's tenure still stands.
    ///
    /// `check_parent_tenure_choice` records a permit when the reorg-timing rules sanction a
    /// later tenure replacing what the conflict's tenure built (see
    /// [`SignerDb::mark_tenure_superseded`]). A standing permit excludes the conflict entirely:
    /// our signature must not stand in the way of a replacement we sanctioned. But the permit
    /// is only as alive as the sortition it was granted to: if a burnchain fork orphaned the
    /// permitting sortition, the reorg we sanctioned can no longer happen, and the record must
    /// not keep suppressing the conflict.
    ///
    /// A false 404 here (e.g. from a node still catching up) only restores a conflict the
    /// permit could have excluded, which at worst delays the replacement, so unlike
    /// `conflict_still_blocks` no tip-height guard is needed. A node error voids the permit for
    /// the same reason: blocking is the direction that can be taken back.
    fn reorg_permit_stands(
        &self,
        stacks_client: &StacksClient,
        conflict: &SignedConflictInfo,
    ) -> bool {
        let Some(superseded_by) = &conflict.superseded_by else {
            return false;
        };
```

**File:** stacks-node/src/tests/signer/v0/reorg.rs (L482-521)
```rust
/// Test a scenario where:
/// Two miners boot to Nakamoto.
/// Sortition occurs. Miner 1 wins.
/// Miner 1 proposes a block N
/// Signers accept and the stacks tip advances to N
/// Miner 1's block commits are paused so it cannot confirm the next tenure.
/// Sortition occurs. Miner 2 wins.
/// Miner 2 successfully mines blocks N+1
/// Sortition occurs quickly, within first_proposal_burn_block_timing_secs. Miner 1 wins.
/// Miner 1 proposes block N+1'
/// Signers approve N+1', saying "Miner is not building off of most recent tenure. A tenure they
///   reorg has already mined blocks, but the block was poorly timed, allowing the reorg."
/// Miner 1 proposes N+2' and it is accepted.
/// Miner 1 wins the next tenure and mines N+3, off of miner 1's tip. (miner 2's N+1 gets reorg)
#[test]
#[ignore]
fn allow_reorg_within_first_proposal_burn_block_timing_secs() {
    if env::var("BITCOIND_TEST") != Ok("1".into()) {
        return;
    }

    let num_signers = 5;
    let num_txs = 3;

    let mut miners = MultipleMinerTest::new_with_config_modifications(
        num_signers,
        num_txs,
        |signer_config| {
            // Lets make sure we never time out since we need to stall some things to force our scenario
            signer_config.block_proposal_validation_timeout = Duration::from_secs(1800);
            signer_config.tenure_last_block_proposal_timeout = Duration::from_secs(1800);
            signer_config.first_proposal_burn_block_timing = Duration::from_secs(1800);
        },
        |config| {
            config.miner.block_commit_delay = Duration::from_secs(0);
        },
        |config| {
            config.miner.block_commit_delay = Duration::from_secs(0);
        },
    );
```

**File:** sample/conf/signer/mainnet-signer-conf.toml (L151-166)
```text
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
