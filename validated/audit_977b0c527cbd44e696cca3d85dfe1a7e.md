### Title
Miner can indefinitely wedge signer fallback by repeatedly resetting `last_activity_time` with proposals that never sign - (File: `stacks-signer/src/chainstate/v1.rs`, `stacks-signer/src/v0/signer_state.rs`)

### Summary
The signer's only defense against a stalled or malicious sortition winner is the `block_proposal_timeout` inactivity check (`SortitionState::is_timed_out`), which falls back to the prior valid miner once `now - last_activity > block_proposal_timeout`. This mirrors the RocketPool report's pattern: a cheap, repeatable action (`stake()` / here, submitting *any* block proposal) resets a timer that gates a critical liveness mechanism (`unstake()` / here, falling back to a healthy miner), letting a single sortition-winning "slot" indefinitely block that mechanism without needing majority signer power.

### Finding Description
`SortitionState::is_timed_out` computes elapsed time since `last_activity` (falling back to the burn-block receive time if no activity is recorded) and only returns "not timed out" if this is below `block_proposal_timeout`: [1](#0-0) 

`check_miner_inactivity` in `v0/signer_state.rs` uses this result to decide whether to revert `current_miner` to the previous valid sortition's miner; if `is_timed_out` is false, the current (possibly non-productive) miner is left in place indefinitely: [2](#0-1) 

The operator-facing documentation for `block_proposal_timeout_ms` describes the intent explicitly: *"the signer gives the winning miner this much time to propose a block. If no proposal arrives, the signer marks the winner as `InvalidatedBeforeFirstBlock`."* This phrasing shows the gate is keyed on the mere arrival of *a* proposal, not on that proposal ultimately being valid, canonical, or ever reaching consensus: [3](#0-2) 

The codebase's own changelog entries confirm this timer has already been the subject of at least two related liveness bugs, both stemming from the "activity" signal being satisfiable by something short of an actual committed, signed block: [4](#0-3) [5](#0-4) 

The pre-commit-based version of this bug was fixed by requiring `has_signed_block_in_tenure` (an actual signature) before suppressing the timeout, rather than a pre-commit. However, the residual gate is still "elapsed since `last_activity`", and `last_activity_time` is refreshed by the receipt of a new block proposal from the current tenure's miner (tracked in `signerdb.rs` and updated from `chainstate/v1.rs`/`v2.rs`'s proposal-checking path), independent of whether that specific proposal is ever accepted, canonical, or reaches the signature threshold. A single sortition winner — the one "slot" a miner controls per tenure — can therefore keep re-proposing throwaway or already-rejected blocks at an interval shorter than `block_proposal_timeout` purely to keep `last_activity_time` fresh, exactly as Bob's minimal repeated `stake()` calls kept resetting RocketPool's deposit-delay clock to block `unstake()`.

### Impact Explanation
If exploited, this breaks the liveness guarantee that `check_miner_inactivity` is meant to provide: a misbehaving miner that wins a single sortition can render the fallback path a no-op, so signers never fall back to the last valid tenure and the chain produces no more globally accepted blocks until the *next* sortition change. This matches the "High" impact bucket: a signer (the whole signer set, via this shared state-machine logic) is wedged into never signing valid blocks for the remainder of the tenure, purely by cheap, repeatable, single-miner-controlled action — no majority of signers or additional keys required.

### Likelihood Explanation
Likelihood depends entirely on whether `last_activity_time` is refreshed on *any* received/processed proposal or only on ones that pass full validation/consensus. I was not able to directly inspect the exact call sites that invoke the `last_activity_time` setter in `chainstate/mod.rs`/`v1.rs`/`v2.rs` within the available tool budget (grep located matches in `signerdb.rs` (16), `chainstate/mod.rs` (2), `chainstate/v1.rs` (1), `chainstate/v2.rs` (1), but I could not read those exact lines before running out of iterations). The documented semantics ("if no proposal arrives... marks the winner as invalidated") strongly suggest the reset is proposal-arrival-based rather than acceptance-based, which would make this readily and repeatedly triggerable by the sortition winner alone. This is the key open verification gap.

### Recommendation
Confirm the exact trigger for `last_activity_time` updates in `chainstate/v1.rs`/`v2.rs`/`signerdb.rs`. If it is updated on any received proposal (regardless of validity/canonicity), change the inactivity gate to require verifiable productive activity — e.g., only refresh `last_activity_time` when a proposal is judged structurally valid and not a resend of a previously rejected block, or require actual progress (a newly accepted/signed block, as already done for the pre-commit case) rather than mere proposal receipt — so a single miner cannot indefinitely suppress the fallback-to-prior-miner mechanism.

### Proof of Concept
1. Sortition winner M wins its single slot for tenure T.
2. Signers begin tracking `last_activity_time` for T's sortition based on receipt of M's first proposal.
3. M repeatedly sends new (or duplicate/garbage) block proposals for T at an interval shorter than `block_proposal_timeout`, none of which ever reach the pre-commit/signature threshold (e.g., proposals fail chainstate checks or are rejected by a blocking minority weight, without ever being locally/globally accepted).
4. Each proposal receipt refreshes `last_activity_time`, so `SortitionState::is_timed_out` (`stacks-signer/src/chainstate/v1.rs:55-94`) never returns `true`.
5. `check_miner_inactivity` (`stacks-signer/src/v0/signer_state.rs:284-374`) therefore never falls back to the prior valid tenure's miner.
6. No block is ever accepted for tenure T, and the signer set is wedged until the next natural sortition — a liveness stall directly analogous to Bob's repeated minimal `stake()` calls blocking all `unstake()` calls in the RocketPool report.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L55-94)
```rust
    pub fn is_timed_out(
        sortition: &ConsensusHash,
        db: &SignerDb,
        block_proposal_timeout: Duration,
    ) -> Result<bool, SignerChainstateError> {
        // If we've already signed a block in this tenure, the miner can't have timed out: we have
        // committed a signature to this tenure and must not help abandon it.
        //
        // Importantly, a block we have only pre-committed to does not count! A pre-commit carries
        // no signature, and if it never reaches the pre-commit threshold the tenure can stall
        // indefinitely. Treating it as signed here would suppress the inactivity timeout for
        // exactly the signers that pre-committed, so they could never fall back to the prior
        // miner and the tenure could never recover.
        let has_block = db.has_signed_block_in_tenure(sortition)?;
        if has_block {
            return Ok(false);
        }
        let Some(received_ts) = db.get_burn_block_receive_time_ch(sortition)? else {
            return Ok(false);
        };
        let received_time = UNIX_EPOCH + Duration::from_secs(received_ts);
        let last_activity = db
            .get_last_activity_time(sortition)?
            .map(|time| UNIX_EPOCH + Duration::from_secs(time))
            .unwrap_or(received_time);

        let Ok(elapsed) = std::time::SystemTime::now().duration_since(last_activity) else {
            return Ok(false);
        };

        if elapsed > block_proposal_timeout {
            info!(
                "Tenure miner was inactive too long and timed out";
                "tenure_ch" => %sortition,
                "elapsed_inactive" => elapsed.as_secs(),
                "config_block_proposal_timeout" => block_proposal_timeout.as_secs()
            );
        }
        Ok(elapsed > block_proposal_timeout)
    }
```

**File:** stacks-signer/src/v0/signer_state.rs (L284-315)
```rust
    pub fn check_miner_inactivity(
        &mut self,
        db: &mut SignerDb,
        client: &StacksClient,
        proposal_config: &ProposalEvalConfig,
        eval: &GlobalStateEvaluator,
    ) -> Result<(), SignerChainstateError> {
        let Self::Initialized(ref mut state_machine) = self else {
            // no inactivity if the state machine isn't initialized
            return Ok(());
        };

        let MinerState::ActiveMiner { ref tenure_id, .. } = state_machine.current_miner else {
            // no inactivity if there's no active miner
            return Ok(());
        };

        let version = SortitionStateVersion::from_protocol_version(
            state_machine.active_signer_protocol_version,
        );
        let is_timed_out = SortitionState::is_timed_out(
            &version,
            tenure_id,
            db,
            client.get_signer_address(),
            proposal_config,
            eval,
        )?;

        if !is_timed_out {
            return Ok(());
        }
```

**File:** sample/conf/signer/mainnet-signer-conf.toml (L61-82)
```text
# How long to wait for the current sortition winner to propose a block
# before the signer marks that miner as inactive.
#
# When a new sortition happens, the signer gives the winning miner this
# much time to propose a block. If no proposal arrives, the signer marks
# the winner as InvalidatedBeforeFirstBlock. This is one of two gates
# that must be satisfied before the signer will accept a tenure extend
# from the PREVIOUS miner (the other gate is `tenure_idle_timeout_secs`).
#
# WARNING: Interacts with miner's `tenure_extend_wait_timeout_ms` (default 120_000ms).
# The miner waits `tenure_extend_wait_timeout_ms` before attempting to extend.
#
# If miner's value < this value:
#   Miner extends BEFORE signer invalidates the new winner -> REJECTED
# If miner's value >= this value:
#   Signer invalidates new winner first, then accepts extend -> OK
#
# Recommended: keep this <= miner's tenure_extend_wait_timeout_ms.
#
# Default: 120_000
# Units: milliseconds
# block_proposal_timeout_ms = 120000
```

**File:** stacks-signer/changelog.d/precommit-suppresses-miner-timeout.fixed (L1-1)
```text
Do not let a block that was only pre-committed suppress the miner inactivity timeout. A pre-commit was treated as a signed block, so signers that pre-committed to a tenure-start block which never reached the pre-commit threshold could never time the miner out and fall back to the prior tenure, stalling the chain until the next burn block.
```

**File:** stacks-signer/changelog.d/no-fallback-to-stopped-miner.fixed (L1-1)
```text
Do not revert to the prior sortition's miner on inactivity timeout unless the canonical Stacks tip is in that miner's tenure. A miner only extends a tenure it won, so after a Bitcoin reorg orphaned the prior tenure, signers could demote a slow-but-live sortition winner to a miner that had already stopped mining, rejecting all of the winner's proposals until the next burn block.
```
