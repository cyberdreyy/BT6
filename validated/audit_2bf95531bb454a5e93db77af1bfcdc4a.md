### Title
Tenure superseding decided solely on the FIRST block's timing incorrectly exempts an already-signed SECOND block from equivocation protection - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`SortitionData::check_parent_tenure_choice` only consults `signer_db.get_first_approved_block_in_tenure(&tenure.consensus_hash)` when deciding whether tenure `T1` was "poorly timed" and can be superseded, and it applies the resulting supersede record to the *entire tenure* via `record_superseded_tenure` / `mark_tenure_superseded`, not just to the first block. If `T1` has two locally-signed (but not globally-accepted) blocks, only the first block's `approved_time` is compared against `sortition_state_received_time`, so a differently-timed, validly-signed second block in `T1` is superseded along with it and silently loses its equivocation protection.

### Finding Description
In `check_parent_tenure_choice` (`stacks-signer/src/chainstate/mod.rs`):
- The only guard against reorging an already-progressed tenure is `get_globally_accepted_block_count_in_tenure(&tenure.consensus_hash) > 1` [1](#0-0) . This counts *globally accepted* blocks, not locally-signed ones, so a tenure with two locally-approved-but-not-yet-globally-accepted blocks passes this check with a count of 0 or 1.
- The timing check that follows fetches only `get_first_approved_block_in_tenure(&tenure.consensus_hash)` [2](#0-1)  and computes `proposal_to_sortition = sortition_state_received_time.saturating_sub(approved_at)` using that first block's `approved_time` alone, comparing it to `first_proposal_burn_block_timing` [3](#0-2) . There is no analogous check of the tenure's *last* or *any other* locally-signed block.
- If the first block is judged "poorly timed," the whole `tenure` entry (keyed by `tenure.consensus_hash`, a tenure-level identifier, not a block-level one) is pushed to `superseded_tenures` and later persisted tenure-wide via `record_superseded_tenure` → `signer_db.mark_tenure_superseded(&tenure.consensus_hash, ...)` [4](#0-3) .
- The doc comment on `check_parent_tenure_choice` itself states the intended invariant: reorged tenures "must either have produced zero blocks _or_ produced their first (and only) block very close to the burn block transition" [5](#0-4)  — i.e., the logic assumes at most one block exists once the >1-globally-accepted guard passes. That assumption is false whenever a signer has locally signed a second block that the network has not yet globally accepted; the code has no check to detect or reject that case, and the timing math only ever inspects the first block.

Because `mark_tenure_superseded` records the supersede relationship at the tenure/consensus-hash granularity, any downstream logic (`get_signed_conflicts`) that treats "this tenure is superseded by sortition X" as blanket permission to stop treating *any* of that tenure's previously-signed blocks as conflicts would then also exempt the second, differently-timed, validly-signed block — even though its own timing was never evaluated and might not qualify for the exemption on its own.

### Impact Explanation
If exploitable end-to-end, this breaks the equivocation/uniqueness guarantee: a signer that already signed a block at height H in tenure `T1` could be induced to also sign a conflicting sibling block at height H in a new tenure that claims to supersede `T1`, purely because `T1`'s *first* block (not the block actually being protected) looked "late." This is a chain-safety violation matching the Critical category (loss of the equivocation guard, enabling a signer to sign conflicting blocks). It is repeatable in principle any time a tenure accumulates ≥2 locally-signed-but-not-globally-accepted blocks with divergent timing relative to the next sortition.

### Likelihood Explanation
Preconditions are narrow but not implausible: tenure `T1` must have ≥2 blocks approved locally by the target signer while the network-wide globally-accepted count for `T1` stays ≤1 (e.g., due to slow global propagation/acceptance), and the *first* of those blocks must have been received close enough to the following sortition to trip the `first_proposal_burn_block_timing` threshold while the second block's approval timing differs. Achieving the precise "just under the threshold" gap for the first block specifically (as opposed to naturally occurring latency) requires influence over burn-block arrival timing relative to the victim signer, which is a soft precondition rather than a hard requirement — natural network jitter could also produce it. The attacker only needs a single miner slot to construct the superseding tenure and gossip a competing proposal; no signer majority, key compromise, or auth_token is required.

Note: I was not able to fully inspect `SignerDb::get_signed_conflicts`, `mark_tenure_superseded`, or `get_first_approved_block_in_tenure`'s implementations in `stacks-signer/src/signerdb.rs` within the available tool budget (only confirmed the call sites and their tenure-level keying from `chainstate/mod.rs`). The core logic gap — first-block-only timing check gating a tenure-wide supersede record — is confirmed directly in `chainstate/mod.rs`, but I could not fully verify from source that `get_signed_conflicts` treats the supersede record as blanket per-tenure exemption for all blocks (vs. re-deriving per-block timing itself). This should be verified against `stacks-signer/src/signerdb.rs` before treating the downstream conflict-exemption behavior as confirmed.

### Recommendation
When deciding whether a reorged tenure can be superseded, check the timing of **every** locally-approved block in that tenure (or at minimum the last one), not just the first, before adding the tenure to `superseded_tenures`. Alternatively, tighten the guard to require that the locally-known approved block count (not just globally-accepted count) is ≤1 before applying any timing-based exemption, so the "first (and only) block" assumption documented in the comment is actually enforced in code.

### Proof of Concept
Rust test plan (in `stacks-signer/src/chainstate/tests/`):
1. Construct a `SignerDb` and insert tenure `T1` with two `BlockInfo` entries at consecutive heights, both in `LocallyAccepted`/`Approved` state (not `GloballyAccepted`), with distinct `approved_time` values: block A `approved_time = t0` (chosen so `sortition_state_received_time - t0 < first_proposal_burn_block_timing`, i.e., "poorly timed"), block B `approved_time = t1` (chosen so the gap would be `>= first_proposal_burn_block_timing`, i.e., well-timed).
2. Ensure `get_globally_accepted_block_count_in_tenure(T1) <= 1` (e.g., 0).
3. Insert a burn block receive time via `insert_burn_block`/`get_burn_block_receive_time` matching the crafted `sortition_state_received_time`.
4. Call `SortitionData::check_parent_tenure_choice` for a new sortition whose `parent_tenure_id != prior_sortition`, with `tenures_reorged` containing `T1`.
5. Assert the call returns `Ok(true)` (reorg permitted) and that `T1` is now marked superseded in `signer_db`.
6. Call `signer_db.get_signed_conflicts` (or the equivalent public API) for a sibling block proposal at block B's height under the new superseding tenure.
7. Assert (showing the bug) that the sibling is *not* reported as a conflict — i.e., block B's signature no longer blocks a conflicting sibling — even though block B's own `approved_time` (`t1`) never crossed the "poorly timed" threshold and should still be protected.

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

**File:** stacks-signer/src/chainstate/mod.rs (L234-245)
```rust
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

**File:** stacks-signer/src/chainstate/mod.rs (L297-315)
```rust
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
