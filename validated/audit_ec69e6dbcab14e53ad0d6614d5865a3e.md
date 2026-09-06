### Title
Miner-controlled proposal timing can force `check_parent_tenure_choice` to sanction a reorg of an already globally-accepted, non-late block - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`SortitionData::check_parent_tenure_choice` grants an automated "reorg permit" whenever the elapsed time between a tenure's block being signer-approved and the next sortition's burn block being received is below `first_proposal_burn_block_timing`. This decision is fed by a signer-local timestamp (`approved_time`) whose *lateness* a miner can unilaterally control by delaying when it broadcasts its own tenure-start block proposal to the signer set. A miner can therefore manufacture the "poorly timed" condition for a tenure that in fact had ample time and was properly signed/globally accepted, causing the signer to record that tenure as **superseded** and later sign a conflicting replacement block at the same height — an unbounded, automated trust in an externally-influenced input feeding directly into a safety-critical decision, structurally analogous to the AMO `uopt` feeding `MINTR` without bounds.

### Finding Description
`check_parent_tenure_choice` (`stacks-signer/src/chainstate/mod.rs:170-291`) is the single place that decides whether a miner is allowed to reorg a previous tenure. For a reorged tenure with at most one globally-accepted block, it computes:

```
proposal_to_sortition = sortition_state_received_time.saturating_sub(approved_at)
``` [1](#0-0) 

If `proposal_to_sortition < first_proposal_burn_block_timing`, the tenure is treated as having produced its "first (and only) block very close to the burn-block transition," so the reorg is *permitted* and the tenure is pushed into `superseded_tenures`, which is later persisted via `mark_tenure_superseded` [2](#0-1) . A tenure marked superseded is thereafter excluded from `get_signed_conflicts`/`reorg_permit_stands` checks at signature time, so a signature the signer already placed on that tenure's block **no longer blocks a later, conflicting replacement** [3](#0-2) , [4](#0-3) .

The two timestamps compared are:
- `sortition_state_received_time`: when *this* signer's node locally received the burn block for the new (reorging) sortition — outside the miner's direct control.
- `approved_at` (`local_block_info.approved_time`): when *this* signer approved (signed) the reorged tenure's first block.

`approved_at` is only reached after the miner *proposes* its block to the signer set. A miner fully controls when it broadcasts its tenure-start block proposal within its tenure window. By deliberately delaying that proposal until just before the next Bitcoin block/sortition, the miner can make `approved_at` arbitrarily close to `sortition_state_received_time`, driving `proposal_to_sortition` below `first_proposal_burn_block_timing` even though the tenure was not actually disadvantaged by unlucky timing — the miner chose to publish late. This satisfies the "poorly timed, allow reorg" branch regardless of the tenure's true fairness, causing the signer to mark that tenure superseded even though it has a legitimately, promptly-signed and globally-accepted block.

This is the analog of the AMO bug: `uopt` (an externally-influenced value, controlled by Silo/market conditions) feeds directly into `MINTR`'s mint decision without being bounded, letting an attacker who controls the input starve the intended safety window. Here, `approved_time` — indirectly steerable by the miner's own proposal-broadcast timing — feeds directly into an automated "supersede/permit reorg" decision without any bound checking whether the tenure's block had actually been live/confirmed for a safe duration on the *node's* canonical view, only comparing two signer-local wall-clock timestamps that the miner can influence at one end.

### Impact Explanation
Once a tenure is (incorrectly) marked superseded, the signer's own previously-placed, valid signature over that tenure's block no longer counts as a blocking conflict (`reorg_permit_stands` returns true while the permitting sortition remains canonical). This directly enables the signer set to sign a **conflicting block** at the same chain height that replaces an already globally-accepted block — a Critical-class outcome under this scan's rubric ("a signer signing an invalid, non-canonical, or conflicting block"). It undermines the core guarantee that a signature is a "bearer instrument" that must block replacement of already-finalized state, as documented in `docs/signer-flows.md` section 8 [5](#0-4) .

### Likelihood Explanation
The attack requires only a single miner's cooperation: control over when it broadcasts its own tenure-start block proposal to the signer set (well within a miner's normal capabilities, no majority or signer-key compromise needed), plus subsequently winning (or having an ally win) the next sortition to trigger the reorg check against the delayed tenure. No signer collusion, StackerDB manipulation, or majority signer control is required — only ordinary miner-side timing control over proposal broadcast, which is a "one-slot miner" action.

### Recommendation
Do not rely solely on the *signer's local* `approved_time` to judge whether a tenure's block was "late." Instead, bound the reorg-permit decision using node-observed, harder-to-manipulate signals — e.g., require confirmation from the Stacks node about how long the block has been the canonical tip before allowing supersession, or measure from `proposed_time` (when the proposal was first seen) rather than `approved_time` (when validation/signing completed, which the miner can push later by simply proposing late), and additionally require that the reorged tenure's block was never observed as canonical for more than a bounded window regardless of when the signer got around to approving it. Consider also capping how often/how far back reorg permits based on this heuristic can be granted, similar to imposing `ulow`/`ucrit` bounds before allowing the automated bypass.

### Proof of Concept
1. Miner M wins tenure T's sortition.
2. M deliberately withholds broadcasting T's tenure-start block proposal to the signer set until shortly before the next Bitcoin block is expected (well within M's control; no signer or majority needed).
3. Signers validate and sign T's block normally once proposed; `approved_time` is recorded very close to the next sortition's arrival.
4. T's block reaches 70% signatures and is globally accepted normally.
5. The next sortition (won by M or a colluding miner) proposes a tenure-change block whose `parent_tenure_id` reorgs away T (`prior_sortition != parent_tenure_id`).
6. `check_parent_tenure_choice` computes `proposal_to_sortition = sortition_state_received_time - approved_time`, which is small (by construction from step 2), so it takes the "poorly timed" branch, adds T to `superseded_tenures`, and returns `Ok(true)` [6](#0-5) .
7. `mark_tenure_superseded` persists this, so when the new conflicting block reaches the pre-commit threshold, `reorg_permit_stands` returns `true` for T's conflict and the signer signs the replacement, discarding the already globally-accepted block from T [3](#0-2) .

Note: I was unable to fully verify within the available context whether `first_proposal_burn_block_timing`'s default value and `reorg_attempts_activity_timeout` gating (used elsewhere to bound "miner activity" windows) provide an additional guard against this specific timing manipulation in `check_parent_tenure_choice` itself — the code path reviewed does not reference `reorg_attempts_activity_timeout` in this function, only in the separate `is_timed_out`/miner-inactivity checks, so this uncertainty should be confirmed against the full `stacks-signer/src/chainstate/mod.rs` and `config.rs` defaults before treating this as final.

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

**File:** stacks-signer/src/v0/signer.rs (L1208-1247)
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
        match stacks_client.get_sortition_by_burn_hash(&superseded_by.burn_block_hash) {
            Ok(_) => true,
            Err(ClientError::RequestFailure(reqwest::StatusCode::NOT_FOUND)) => {
                info!("{self}: The tenure we permitted to reorg a conflicting block's tenure was itself orphaned by a burnchain fork. The permit no longer excludes the conflict.";
                    "conflicting_consensus_hash" => %conflict.consensus_hash,
                    "superseded_by_consensus_hash" => %superseded_by.consensus_hash,
                    "superseded_by_burn_block_hash" => %superseded_by.burn_block_hash,
                );
                false
            }
            Err(e) => {
                warn!("{self}: Failed to check whether the sortition that permitted a reorg is still canonical: {e:?}. Treating the permit as void.";
                    "conflicting_consensus_hash" => %conflict.consensus_hash,
                    "superseded_by_consensus_hash" => %superseded_by.consensus_hash,
                );
                false
            }
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1432-1457)
```rust
        if conflicts.iter().any(|conflict| {
            conflict.consensus_hash == block_info.block.header.consensus_hash
                && !self.reorg_permit_stands(stacks_client, conflict)
        }) {
            match stacks_client.get_tenure_tip(&block_info.block.header.consensus_hash) {
                Ok(tip) => {
                    let tip_height = tip.anchored_header.height();
                    if tip_height >= block_info.block.header.chain_length {
                        warn!(
                            "{self}: Reached the pre-commit threshold for a block that conflicts with previously signed or accepted blocks, and the canonical tip of its tenure is already at or above the proposed height. Refusing to sign.";
                            "signer_signature_hash" => %block_hash,
                            "block_height" => block_info.block.header.chain_length,
                            "canonical_tip_height" => tip_height,
                        );
                        return;
                    }
                }
                Err(e) => {
                    warn!(
                        "{self}: Failed to fetch the canonical tip of the proposed block's tenure: {e:?}. Treating the tenure as unconfirmed.";
                        "signer_signature_hash" => %block_hash,
                        "consensus_hash" => %block_info.block.header.consensus_hash,
                    );
                }
            }
        }
```

**File:** docs/signer-flows.md (L496-511)
```markdown
One decision does have to be recorded, because it is ours rather than the
node's. When a miner builds off something other than the prior sortition,
`check_parent_tenure_choice` decides whether the reorg is allowed: it is, if
every tenure being reorged has at most one globally accepted block and produced
its first block too close to the next sortition to count
(`first_proposal_burn_block_timing`). Having sanctioned that replacement, the
signer records those tenures as **superseded** (`mark_tenure_superseded`), so its
own signature over what they built does not then block the replacement it just
permitted — the node cannot answer this one at signing time, since it still
serves the reorged tenure as fully live until the replacement lands. What _is_
still derived from the node is the permit's own validity: the record carries the
permitting tenure's sortition, and it only excludes conflicts while that
sortition remains canonical (section 5, `reorg_permit_stands`), so a burnchain
fork that orphans the permitting tenure automatically voids the permit. A record
more than `MAX_FORK_DEPTH` (100) burn blocks below the tip is dropped; a fork
that deep would cause far bigger problems than a stale conflict.
```
