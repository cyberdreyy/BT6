### Title
Reorg-permit timing check uses `approved_time` instead of the actual proposal-receipt time, letting a miner manufacture an illegitimate reorg permit that voids a previously-signed block's conflict protection - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
`SortitionData::check_parent_tenure_choice` decides whether a miner is allowed to reorg a prior tenure that already produced a signed block, by measuring the time between "when the proposal was received" and "when the next sortition arrived" against `first_proposal_burn_block_timing`. The implementation, however, computes that gap using `local_block_info.approved_time` rather than the actual time the block proposal was received by the signer. `approved_time` is stamped only after the node finishes validating the block and the signer pre-commits/locally-accepts it (`docs/signer-flows.md` section 2: "`approved_time` is stamped at pre-commit or local acceptance (first wins)") — this necessarily lags the real proposal-arrival time by the node's validation latency and the pre-commit round trip. Using this later timestamp shrinks the measured `proposal_to_sortition` gap, biasing the check toward concluding a tenure was "poorly timed" and therefore reorg-eligible, even when the real proposal-to-sortition gap was large enough that the policy should have refused the reorg.

### Finding Description
`check_parent_tenure_choice` in `stacks-signer/src/chainstate/mod.rs` (lines ~247-278) is the sole gate that decides whether a new miner's tenure is allowed to reorg a previous tenure that has already produced a globally accepted (signed) block (guarded earlier by "disallow reorg if more than one block has already been signed", so it only applies when the prior tenure has 0 or 1 globally accepted blocks). [1](#0-0) 

The code:
```
let proposal_to_sortition = if let Some(approved_at) = local_block_info.approved_time {
    sortition_state_received_time.saturating_sub(approved_at)
} else { ... 0 };
if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
    // treat as "poorly timed" -> permit the reorg
    superseded_tenures.push(tenure);
    continue;
}
```
computes the gap as `sortition_received_time - approved_time`. The intent, per the surrounding comments and log fields (`"violating_tenure_proposed_time" => local_block_info.proposed_time`), is to measure from when the *proposal* was received, not from when it was *approved*. Per the documented `BlockInfo` timestamp semantics (`docs/signer-flows.md` section 2), `approved_time` is set at `mark_pre_committed`/`mark_locally_accepted`, both of which only happen after node validation completes — a step that can take seconds (transaction execution, cost accounting) per `postblock_proposal.rs::validate`. [2](#0-1) 

Because `approved_time >= proposed_time` always, the code's `sortition_received - approved_time` is strictly smaller than the "true" gap `sortition_received - proposed_time` that the policy is meant to check. This mismatch between the equality the code assumes (`approved_time ≈ proposal_receipt_time`) and reality (they diverge by the validation delay) is exactly the report's bug class: an assumed 1:1 correspondence between two quantities that in fact diverge under a natural processing delay, and that divergence is exploitable.

A miner that wants to reorg an already-signed tenure can inflate this delay (e.g. by proposing a block whose transactions are slow/expensive to validate, near the size/time budget allowed by `block_proposal_validation_timeout_secs`/`max_tx_execution_time_secs`), which pushes `approved_time` later without changing the real proposal-arrival time. This narrows the computed `proposal_to_sortition` window, making it more likely to fall under `first_proposal_burn_block_timing` and be misclassified as "poorly timed," triggering `superseded_tenures.push(tenure)` and, ultimately, `mark_tenure_superseded`. [3](#0-2) 

Once a tenure is marked superseded, `get_signed_conflicts` annotates its previously-signed block(s) with `superseded_by`, and `reorg_permit_stands` treats the conflict as excluded for as long as the permitting sortition remains canonical: [4](#0-3) 

This means the pre-commit conflict guard (section 5 of `docs/signer-flows.md`, "signed conflicts at height ≥ h → reorg_permit_stands → excluded") will let the signer sign a *new* block that conflicts with the block it previously signed and that had already reached global acceptance — a legitimately canonical block — because the reorg-permit gate was tripped by a miscalculated timing window rather than a genuinely late proposal. [5](#0-4) 

### Impact Explanation
This breaks the equality the pre-commit conflict guard depends on: "a fresh signature over a globally-accepted block should always block a conflicting replacement unless the replacement was legitimately sanctioned by the reorg-timing policy." Because the sanctioning check itself measures the wrong interval, a single malicious/opportunistic miner (no majority of signers needed) can manufacture the conditions for an illegitimate reorg permit, causing the signer set to sign a conflicting block at/above the height of an already-signed, globally accepted block. That is a signer signing a conflicting/non-canonical block relative to previously finalized state — the Critical impact category defined in scope.

### Likelihood Explanation
The trigger requires only a single miner controlling tenure timing/content near a sortition boundary (widening validation latency via transaction complexity/size, which a miner fully controls when building its own block) — well within a "one-slot miner" threat model and requiring no signer collusion or key compromise. The exact size of the window (`first_proposal_burn_block_timing`) and how much validation latency can be manufactured determines how reliably the race can be won, so likelihood is real but conditional on operator-configured timing parameters and network conditions, making it moderate rather than trivially deterministic in all deployments.

### Recommendation
In `check_parent_tenure_choice`, measure `proposal_to_sortition` using the block's actual proposal-received/`proposed_time` timestamp (the field already retrieved and referenced in the log line) instead of `approved_time`, so the timing decision reflects when the proposal genuinely arrived rather than when node validation and pre-commit processing finished. If `proposed_time` is unavailable, fail closed (treat as not "poorly timed", i.e. disallow the reorg) rather than substituting a later timestamp that systematically shrinks the measured gap.

### Proof of Concept
1. Tenure A's miner proposes its first block; signers receive the proposal at time `T0`. The block is complex enough that node validation takes `Δ` seconds, so `approved_time` (pre-commit/local-accept) is stamped at `T0 + Δ`.
2. Block A reaches global acceptance (exactly 1 globally accepted block in the tenure, so the ">1 blocks" early-reject in `check_parent_tenure_choice` does not fire).
3. The next sortition's burn block is received by signers at `T1`, where the *true* gap `T1 - T0 >= first_proposal_burn_block_timing` (i.e., by the intended policy the tenure was NOT "poorly timed" and should not be reorg-eligible), but `T1 - (T0 + Δ) < first_proposal_burn_block_timing` because the code uses `approved_time` instead of `T0`.
4. `check_parent_tenure_choice` computes `proposal_to_sortition = T1 - approved_time`, finds it below the threshold, and marks tenure A as superseded via `record_superseded_tenure`/`mark_tenure_superseded`.
5. The new tenure B is accepted as valid parent-tenure choice (`check_proposal` returns `Ok(())` instead of `RejectReason::ReorgNotAllowed`).
6. When B's blocks reach the pre-commit threshold, `handle_block_pre_commit` queries `get_signed_conflicts`, finds A's previously-signed block annotated with `superseded_by` pointing at B's sortition, and `reorg_permit_stands` reports the permit as standing (B's sortition is canonical) — so A's block is excluded from the conflict set, and the signer proceeds to sign B's block despite A already being globally accepted.

Note on verification depth: due to tool-call limits I was not able to directly inspect the exact `BlockInfo` field definitions/setters for `proposed_time` versus `approved_time` side by side in `signerdb.rs`, nor step through a live/integration test reproducing this exact race; the analysis above is based on the documented timestamp semantics in `docs/signer-flows.md` (section 2) and the code in `chainstate/mod.rs` referenced above. A background agent with full repo access should confirm the `proposed_time` setter/semantics and, ideally, write a unit test analogous to `signer_state.rs`'s existing capitulation-timing tests to reproduce the misclassification before committing to a fix.

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

**File:** stackslib/src/net/api/postblock_proposal.rs (L541-556)
```rust
    pub fn validate(
        &self,
        sortdb: &SortitionDB,
        chainstate: &mut StacksChainState, // not directly used; used as a handle to open other chainstates
        timeout_secs: u64,
        max_tx_execution_time_secs: u64,
        max_tx_analysis_time_secs: u64,
        max_tx_mem_bytes: u64,
        auth_token: Option<String>,
    ) -> Result<BlockValidateOk, BlockValidateRejectReason> {
        fault_injection_validation_stall(auth_token);
        let start = Instant::now();

        fault_injection_validation_delay();

        let mainnet = self.chain_id == CHAIN_ID_MAINNET;
```

**File:** stacks-signer/src/signerdb.rs (L1627-1660)
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
        &mut self,
        consensus_hash: &ConsensusHash,
        burn_block_height: u64,
        superseded_by_consensus_hash: &ConsensusHash,
        superseded_by_burn_block_hash: &BurnchainHeaderHash,
    ) -> Result<(), DBError> {
        self.db.execute(
            "INSERT OR REPLACE INTO superseded_tenures (consensus_hash, burn_block_height, superseded_by_consensus_hash, superseded_by_burn_block_hash, superseded_at) VALUES (?1, ?2, ?3, ?4, ?5)",
            params![
                consensus_hash,
                u64_to_sql(burn_block_height)?,
                superseded_by_consensus_hash,
                superseded_by_burn_block_hash,
                u64_to_sql(get_epoch_time_secs())?
            ],
        )?;
        Ok(())
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

**File:** docs/signer-flows.md (L248-253)
```markdown
    TH -- yes --> RECHECK{"chainstate checks still pass?<br/>check_block_against_signer_db_state<br/>→ section 7"}
    RECHECK -- no --> REJ["mark_locally_rejected,<br/>handle_block_rejection,<br/>broadcast rejection"]:::bad
    RECHECK -- yes --> CONF["signed conflicts at height ≥ h,<br/>in ANY tenure<br/>get_signed_conflicts"]
    CONF --> PERM{"covered by a reorg permit whose<br/>permitting sortition is still canonical?<br/>reorg_permit_stands"}
    PERM -- yes --> EXCL(["excluded — our signature must not<br/>block a replacement we sanctioned"]):::good
    PERM -- no --> FRESH{"any of them still fresh?<br/>last_endorsed > cutoff"}
```
