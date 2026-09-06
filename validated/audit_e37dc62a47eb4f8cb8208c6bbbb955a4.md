### Title
Reorg-timing check in `check_parent_tenure_choice` uses local proposal-receipt wall-clock time instead of the block's own header timestamp, letting a miner manipulate propagation delay to unlock a "poorly timed" reorg exception and replace an already-confirmed single-block tenure - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
`SortitionData::check_parent_tenure_choice` decides whether a new tenure is allowed to build on (i.e., reorg away) a prior tenure that already produced a globally-accepted block. The "is this reorg tolerable" decision hinges on `proposal_to_sortition = sortition_state_received_time.saturating_sub(approved_at)`, i.e. the *signer's local wall-clock delta* between when it locally pre-committed to the prior tenure's block and when it later saw the new sortition's burn block. If this delta is smaller than the configured `first_proposal_burn_block_timing`, the reorg is *allowed* on the theory that "the tenure was poorly timed" and the incoming miner had no fair chance to beat it with a normal block-commit. Because `approved_at` is a locally-recorded receipt/processing timestamp rather than the immutable, network-verifiable block header timestamp, a miner who controls delivery timing of the prior tenure's block proposal to the signer set can compress this delta artificially and unlock the reorg exception for a tenure that was, by real elapsed time, fully established. [1](#0-0) 

### Finding Description
`check_parent_tenure_choice` is the signer-side gate that governs whether a new tenure's declared parent (`self.parent_tenure_id`) is allowed to differ from the true previous sortition (`self.prior_sortition`), which is the core "may this miner replace the main-chain tenure" equality check. [2](#0-1) 

When the prior sortition's tenure already produced more than one globally-accepted block, the reorg is unconditionally rejected: [3](#0-2) 

But for a *single-block* tenure, the code falls back to a timing heuristic: it computes `proposal_to_sortition` as the signer's own recorded `approved_time` (stamped locally, at pre-commit, per the doc-comment in `get_tenure_last_block_info`: "`approved_time` is ... stamped at pre-commit") subtracted from `sortition_state_received_time` (the signer's local receive time for the next burn block). If that gap is below `first_proposal_burn_block_timing`, the reorg is treated as tolerable "miner activity" and permitted, superseding the previously-accepted tenure: [1](#0-0) [4](#0-3) 

The problem: both timestamps in this comparison are *local, receipt-time* measurements of the signer's own node, not the immutable block header timestamp (`block.header.timestamp`) that is itself validated elsewhere against parent/future bounds by the node (`postblock_proposal.rs`, `validate timestamp` checks). The signer's own `approved_time` reflects when *that signer* happened to finish processing/pre-committing the proposal — a value that is influenced by:
- how promptly the miner of the (soon-to-be-superseded) tenure broadcasts/propagates its block to the signer set, and
- ordinary validation/propagation latency that a miner can deliberately amplify by delaying or throttling delivery of the block (or delaying/holding the StackerDB proposal chunk) to signers.

Because this comparison is not anchored to a tamper-evident, network-agreed timestamp, a single actor who controls the timing of block delivery to the signer network (the miner who mined the block, or a miner colluding with/impersonating slow delivery) can shrink the observed `proposal_to_sortition` window across the whole signer set, making a fully-confirmed one-block tenure appear "poorly timed" at the moment the next sortition's burn block is observed — even though in real elapsed time the tenure had already stood for well beyond the configured grace window. This satisfies the "manipulation of time-difference values to achieve replacement of main-chain blocks" bug class from the CVE: instead of manipulating block timestamps directly (which are cross-checked against parent/future bounds), the attack manipulates the *side-channel local receipt-time delta* that this reorg-tolerance rule actually keys off of, which has no equivalent tamper-evidence.

This is a one-slot-miner-triggerable path: only the miner producing/propagating the tenure-A block (or a miner able to influence when the tenure-A proposal reaches the signer set) needs to act; no majority of signers or additional keys are required, and each signer independently applies the rule to its own local clock readings, so a network-wide propagation delay against the whole signer set consistently biases every signer's decision the same way.

### Impact Explanation
If this heuristic is fooled, a signer will happily sign a block that reorgs away an already-globally-accepted, single-block tenure — i.e., a signer ends up signing a conflicting/non-canonical replacement of a block it (and the network) had already treated as confirmed. This falls under the Critical impact bucket defined by the scope rules: "a signer signing an invalid, non-canonical, or conflicting block." Because the check runs independently per-signer against local wall-clock state, and the delay vector (miner-controlled block propagation timing) applies symmetrically to the whole signer set, the effect is not confined to one signer's local view; it can bias the whole set's evaluation of the same tenure the same way, undermining the intended chain-continuity guarantee that `check_parent_tenure_choice` is supposed to enforce.

### Likelihood Explanation
Likelihood is moderate: it requires precise timing control over propagation of a proposal (or the associated pre-commit round) relative to the arrival of the next sortition's burn block, which a miner (who already controls its own block-broadcast timing and, in principle, StackerDB write timing for its own proposal) is naturally positioned to influence. It does not require a majority of signers, another signer's key, or auth_token/local access — only control over the timing of the miner's own message delivery, which is exactly the "manipulation of time-difference values" primitive highlighted in the CVE analog. The main constraint narrowing likelihood is that the target tenure must have exactly one globally-accepted block (the `globally_accepted_blocks > 1` check blocks the multi-block case), and the attacker must win (or collude with the winner of) the immediately following sortition.

### Recommendation
Anchor the "poorly timed" determination to a value that cannot be skewed by controlling message delivery timing to the signer set: e.g., use the reorged tenure's block header `timestamp` (already validated by the node against parent/future bounds) or the burn-block-anchored sortition time recorded by the node, rather than the signer's local receipt/pre-commit wall-clock timestamp (`approved_time`) diffed against the signer's local burn-block receive time (`sortition_state_received_time`). At minimum, cross-check the locally observed timing against a second, harder-to-manipulate reference (e.g., corroborate with other signers' recorded times, or the node's own tenure/sortition timing data) before permitting a reorg of an already globally-accepted block.

### Proof of Concept
1. Miner A wins a sortition and mines the sole block of tenure A. Instead of broadcasting the block proposal / StackerDB write promptly, Miner A deliberately delays delivery to the signer set until just before the next Bitcoin block (sortition) is likely to land, ensuring the signer set's locally recorded `approved_time` for tenure A's block sits very close to `sortition_state_received_time` for the following sortition.
2. Because signers stamp `approved_time` at local pre-commit (see `get_tenure_last_block_info`'s doc comment) rather than at the immutable block header timestamp, every signer computes a small `proposal_to_sortition` value in `check_parent_tenure_choice` (`stacks-signer/src/chainstate/mod.rs:251-258`), even though tenure A had, in real elapsed time, already stood long enough for a normal competing block-commit to have replaced it.
3. Miner B (or Miner A operating under a second identity/sortition win) wins the next sortition and proposes a tenure that does not build on tenure A.
4. Each signer runs `check_parent_tenure_choice`; since `proposal_to_sortition < first_proposal_burn_block_timing` for all of them (due to the manipulated delivery timing), the check logs "the block was poorly timed, allowing the reorg" and returns `Ok(true)`, permitting the reorg (`stacks-signer/src/chainstate/mod.rs:259-278`), marking tenure A as superseded via `record_superseded_tenure`.
5. Signers proceed to sign the new tenure's blocks, resulting in the network replacing an already globally-accepted block with Miner B's chain — the CVE-analog "replacement of main-chain blocks" via manipulated time-difference values.

(Direct dynamic confirmation of the exact delivery-delay magnitude achievable in this codebase was not performed; the finding is based on static analysis of the local-timestamp semantics documented in `get_tenure_last_block_info` and used in `check_parent_tenure_choice`.)

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L170-199)
```rust
    pub fn check_parent_tenure_choice(
        &self,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        first_proposal_burn_block_timing: &Duration,
    ) -> Result<bool, SignerChainstateError> {
        // if the parent tenure is the last sortition, it is a valid choice.
        // if the parent tenure is a reorg, then all of the reorged sortitions
        //  must either have produced zero blocks _or_ produced their first (and only) block
        //  very close to the burn block transition.
        if self.prior_sortition == self.parent_tenure_id {
            return Ok(true);
        }
        info!(
            "Most recent miner's tenure does not build off the prior sortition, checking if this is valid behavior";
            "sortition_state.consensus_hash" => %self.consensus_hash,
            "sortition_state.prior_sortition" => %self.prior_sortition,
            "sortition_state.parent_tenure_id" => %self.parent_tenure_id,
        );

        let tenures_reorged =
            client.get_tenure_forking_info(&self.parent_tenure_id, &self.prior_sortition)?;
        if tenures_reorged.is_empty() {
            warn!("Miner is not building off of most recent tenure, but stacks node was unable to return information about the relevant sortitions. Marking miner invalid.");
            return Ok(false);
        }

        // this value *should* always be some, but try to do the best we can if it isn't
        let sortition_state_received_time =
            signer_db.get_burn_block_receive_time(&self.burn_block_hash)?;
```

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

**File:** stacks-signer/src/chainstate/mod.rs (L317-330)
```rust
    /// Get the last signed block from the given tenure if it has not timed out.
    /// Even globally accepted blocks are allowed to be timed out, as that
    /// triggers the signer to consult the Stacks node for the latest globally
    /// accepted block. This is needed to handle Bitcoin reorgs correctly.
    ///
    /// The timeout window is measured from the last time a signature actually covered the
    /// block: our own (`signed_self`) or the observed group/global acceptance
    /// (`signed_group`), whichever is later, matching how `get_signed_conflicts` measures
    /// endorsement freshness. `approved_time` is deliberately not used: it is stamped at
    /// pre-commit, which carries no signature, so it would close the window early. This also
    /// means a globally accepted block we never signed ourselves gets a full window from the
    /// time its acceptance was observed, rather than timing out instantly for lack of a
    /// timestamp.
    pub fn get_tenure_last_block_info(
```
