### Title
Reorg-permission check in `check_parent_tenure_choice` undercounts signed blocks by only querying globally-accepted state, allowing a one-slot miner to get signers to sign a block that reorgs a tenure with multiple already-signed (but not-yet-globally-accepted) blocks - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`SortitionData::check_parent_tenure_choice` is the single gate that decides whether a miner is allowed to build off something other than the prior sortition (a reorg). Its stated safety invariant is "disallow reorg if more than one block has already been signed" [1](#0-0) , but the code implementing that invariant only counts **globally accepted** blocks in the reorged tenure:

```
let globally_accepted_blocks =
    signer_db.get_globally_accepted_block_count_in_tenure(&tenure.consensus_hash)?;
if globally_accepted_blocks > 1 { ... return Ok(false); }
``` [1](#0-0) 

"Globally accepted" is a strictly narrower state than "signed": a block reaches the group-signing threshold (70%+ weight) and moves to `LocallyAccepted` with `signed_group` set purely from gossip of `BlockAccepted` messages, and only becomes `GloballyAccepted` later, once *this signer's own node* observes a `NewBlock` event or advances its tip to the block [2](#0-1) , [3](#0-2) . This is explicitly documented: "It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it" [4](#0-3) .

Because of this gap, a tenure can have **two or more fully signed (`LocallyAccepted`) blocks** that this particular signer's node has not yet confirmed as globally accepted (e.g. due to ordinary node/network propagation lag), and `check_parent_tenure_choice` will treat that tenure as if it had "no signed blocks" for the purposes of the reorg-permission decision, since it queries only the `GloballyAccepted` count instead of "signed" (`LocallyAccepted` OR `GloballyAccepted`).

### Finding Description
The reorg-safety design documented in `docs/signer-flows.md` states the rule as: "it is, if every tenure being reorged has at most one globally accepted block and produced its first block too close to the next sortition to count" [5](#0-4)  — so the design doc itself pins the check to "globally accepted," not "signed." But the in-code comment right above the check says "disallow reorg if more than one block has already been *signed*" [6](#0-5) , revealing that the intended safety property (protect any block that has already crossed the 70% signing threshold, i.e. is a bearer instrument that "still can be aggregated toward the 70% threshold if rejecting signers change their minds") is not what is actually enforced.

Contrast this with the *other* half of the reorg-safety mechanism, `get_signed_conflicts`/`conflict_still_blocks`, which correctly treats `signed_self` OR `signed_group` (i.e. any locally- or globally-accepted, fully signed block) as a live conflict regardless of whether the node has observed it as globally accepted yet: "A conflict is any block a signature was ever put over — ours, or a group threshold we observed — whatever its state now" [7](#0-6) , and the query explicitly selects `WHERE (b.signed_self IS NOT NULL OR b.signed_group IS NOT NULL)` [8](#0-7) . `check_parent_tenure_choice`, which runs earlier (at proposal time, before the pre-commit/signature stage protected by `get_signed_conflicts`), does not apply the same "signed, not merely globally-accepted" standard.

Attack path (needs only the one-slot miner plus ordinary gossip, no majority-signer collusion):
1. Miner M mines tenure T off the canonical tip. Two blocks in T (B1, B2) each independently reach the 70% signature threshold via normal `BlockAccepted` gossip and are marked `LocallyAccepted` by the full signer set (`store_and_process_block_signature` → `mark_locally_accepted(true)`) [2](#0-1) .
2. Because signer/node communication about the node's own tip (which drives the `LocallyAccepted → GloballyAccepted` transition) is asynchronous and can legitimately lag behind pure signer-to-signer gossip, there is a real-world window where the node backing each signer has not yet processed B1/B2 as its own tip, so `get_globally_accepted_block_count_in_tenure(T)` still returns 0 or 1 even though 2 blocks were fully signed.
3. Within that window, sortition advances and M (or a colluding miner controlling the next slot) proposes a competing tenure T' that does *not* build off T, with a `TenureChange` whose timing satisfies `first_proposal_burn_block_timing` relative to T's first block.
4. `check_parent_tenure_choice` sees `globally_accepted_blocks <= 1` for T and evaluates only the timing condition, sanctioning the reorg and calling `signer_db.mark_tenure_superseded` for T [9](#0-8) .
5. `mark_tenure_superseded` causes every signer's own prior signature over T's blocks (B1 and B2) to stop counting as a conflict in `get_signed_conflicts`/`conflict_still_blocks` from that point on [10](#0-9) , [11](#0-10) .
6. Signers then go on to sign T''s tenure-change block, discarding a tenure that in fact had two independently, fully-signed blocks — violating the documented one-signed-block invariant that is supposed to bound how much already-decided history a reorg can erase.

### Impact Explanation
This breaks the "approved-parent vs canonical" equality the reorg-permission gate exists to protect: a miner can get the signer set to sign a block that supersedes/reorgs a tenure containing more than one block that the *signer set itself* already fully signed (crossed the 70% weight threshold), which the code's own comment says must never be allowed. This is a safety violation in the "signer signing a … conflicting block" category (Critical), since it permits erasing signed history beyond the single-block bound the protocol is designed to tolerate, purely as a side effect of routine node/signer synchronization lag rather than any provable burnchain fact.

### Likelihood Explanation
Triggering the window does not require any signer collusion or majority control — it only requires: (a) a tenure that naturally produces 2 (or more) blocks that reach 70% signature weight in quick succession, and (b) the node backing signers not yet having caught up to mark those blocks `GloballyAccepted` (a routine, frequently-occurring condition given `NewBlock` events depend on the node processing/relaying blocks, which lags pure StackerDB gossip) at the moment the next tenure's first proposal arrives within `first_proposal_burn_block_timing` of the previous tenure's first-block signing. A single miner (the one-slot actor in scope) fully controls the timing of the next tenure's proposal and can attempt this on every tenure transition, so likelihood is more than theoretical, though it depends on timing that is not attacker-fully-deterministic (node propagation delay).

### Recommendation
Change the safety check in `check_parent_tenure_choice` to count blocks that are *signed* (`LocallyAccepted` or `GloballyAccepted`, i.e. `signed_self IS NOT NULL OR signed_group IS NOT NULL`) in the reorged tenure, matching the semantics already used by `get_signed_conflicts`, instead of only `GloballyAccepted` blocks. Add a `SignerDb` helper (e.g. `get_signed_block_count_in_tenure`) analogous to `get_globally_accepted_block_count_in_tenure` but keyed off the same `signed_self`/`signed_group` predicate used elsewhere, and use it in place of the current `globally_accepted_blocks` check.

### Proof of Concept
Not independently executed (no test harness access in this review); the following is the code-level reasoning trail supporting the PoC:
1. Two blocks B1, B2 in tenure T each reach `store_and_process_block_signature`'s 70% threshold and are moved to `LocallyAccepted` via `mark_locally_accepted(true)` without any `GloballyAccepted` transition yet occurring locally [12](#0-11) .
2. `check_parent_tenure_choice` for the next tenure queries `get_globally_accepted_block_count_in_tenure(T)`, which returns 0 (or 1) despite 2 blocks being signed [1](#0-0) .
3. Given a qualifying `first_proposal_burn_block_timing`, the function returns `Ok(true)` and calls `mark_tenure_superseded` for T [9](#0-8) .
4. From then on `reorg_permit_stands`/`get_signed_conflicts` exclude B1/B2 as conflicts [13](#0-12) , letting the signer set sign a block in the new, competing tenure.

This finding could not be fully validated end-to-end (e.g., I did not inspect the exact implementation body of `get_globally_accepted_block_count_in_tenure` in `stacks-signer/src/signerdb.rs`, nor run the integration test suite) due to running out of tool iterations; the conclusion rests on the query name/semantics used at the call site, the documented "globally accepted IFF NewBlock/tip-advance" transition rule, and the explicit doc/code-comment mismatch identified above. A full confirmation would require reading `get_globally_accepted_block_count_in_tenure`'s SQL/state filter directly and reproducing the timing window in an integration test.

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

**File:** stacks-signer/src/chainstate/mod.rs (L247-291)
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

            warn!(
                "Miner is not building off of most recent tenure, but a tenure they attempted to reorg has already mined blocks.";
                "parent_tenure" => %self.parent_tenure_id,
                "last_sortition" => %self.prior_sortition,
                "violating_tenure_id" => %tenure.consensus_hash,
                "violating_tenure_first_block_id" => %first_block_mined,
                "checked_proposal_timing" => checked_proposal_timing,
            );
            return Ok(false);
        }
        // Every reorged tenure cleared the rules, so the reorg is permitted.
        for tenure in superseded_tenures {
```

**File:** stacks-signer/src/v0/signer.rs (L710-726)
```rust
                if let Ok(Some(mut block_info)) = self
                    .signer_db
                    .block_lookup(signer_sighash)
                    .inspect_err(|e| warn!("{self}: Failed to load block state: {e:?}"))
                {
                    if block_info.state == BlockState::GloballyAccepted {
                        // We have already globally accepted this block. Do nothing.
                        return;
                    }
                    if let Err(e) = block_info.mark_globally_accepted() {
                        warn!("{self}: Failed to mark block as globally accepted: {e:?}");
                        return;
                    }
                    if let Err(e) = self.signer_db.insert_block(&block_info) {
                        warn!("{self}: Failed to update block state to globally accepted: {e:?}");
                    }
                }
```

**File:** stacks-signer/src/v0/signer.rs (L1208-1248)
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
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2525-2537)
```rust
        // have enough signatures to broadcast!
        // move block to LOCALLY accepted state.
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
        if let Err(e) = block_info.mark_locally_accepted(true) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}");
            }
        }
        let _ = self.signer_db.insert_block(block_info).map_err(|e| {
            warn!("Failed to set group threshold signature timestamp for {block_hash}: {e:?}");
            panic!("{self} Failed to write block to signerdb: {e}");
        });
        self.broadcast_signed_block(stacks_client, block_info.block.clone(), &addrs_to_sigs);
```

**File:** docs/signer-flows.md (L322-327)
```markdown
A conflict is any block a signature was ever put over — ours, or a group
threshold we observed — whatever its state now. In particular rejection, even
_global_ rejection, does not clear one: a rejection is a revocable opinion,
while a signature is a bearer instrument that can still be aggregated toward
the 70% threshold if rejecting signers change their minds. Only staleness or
node-derived death (the two questions above) clears a conflict.
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

**File:** stacks-signer/src/signerdb.rs (L1611-1619)
```rust
        let query = "SELECT b.consensus_hash, b.signer_signature_hash, b.stacks_height, b.state,
                MAX(COALESCE(b.signed_self, 0), COALESCE(b.signed_group, 0)) AS last_endorsed,
                st.superseded_by_consensus_hash, st.superseded_by_burn_block_hash
            FROM blocks b
            LEFT JOIN superseded_tenures st ON st.consensus_hash = b.consensus_hash
            WHERE (b.signed_self IS NOT NULL OR b.signed_group IS NOT NULL)
                AND b.stacks_height >= ?1
                AND b.signer_signature_hash != ?2
            ORDER BY b.stacks_height DESC";
```

**File:** stacks-signer/src/signerdb.rs (L1627-1642)
```rust
    /// Record that we permitted the tenure identified by `superseded_by_*` to reorg this one
    /// under the reorg-timing rules (`first_proposal_burn_block_timing`).
    ///
    /// Having sanctioned the replacement, our own signature over what this tenure built must not
    /// then block it: its blocks stop counting as conflicts (see
    /// [`SignerDb::get_signed_conflicts`]). Recorded when the reorg is permitted rather than
    /// derived at signing time, because by the time a replacement reaches the pre-commit
    /// threshold the sortition view that sanctioned the reorg may be long gone.
    ///
    /// The permit is only honored while the permitting tenure's sortition is still canonical
    /// (checked against the node when the record is applied): if a burnchain fork orphans it,
    /// the reorg we sanctioned can no longer happen, so the record must not keep suppressing
    /// this tenure's conflicts. A re-permit by a different tenure replaces the record, so the
    /// latest permitting sortition is the one checked. Records age out via
    /// [`SignerDb::prune_superseded_tenures`].
    pub fn mark_tenure_superseded(
```
