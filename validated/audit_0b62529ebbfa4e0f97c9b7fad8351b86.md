### Title
Miner-controlled activity-timer refresh via repeated invalid/conflicting proposals lets a single miner wedge the signer's inactivity fallback - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
The external report describes `maxNotionalSwap`/`maxGamma` as caps meant to bound a cumulative effect (pool drainage), but the checks are only evaluated per call, so an attacker defeats the cap by chunking a single large action into many small ones that each individually pass. The stacks-signer analog is `check_latest_block_in_tenure`, whose job is to gate a miner's tenure liveness: it treats *any* rejected/conflicting proposal it sees — even ones it itself refuses — as valid "miner activity" and unconditionally refreshes the tenure's activity timestamp. Because the volume and cadence of proposals a sortition-winning miner emits is entirely under that miner's own control, a single miner (no majority of signers needed) can keep re-arming its own activity timer forever by sending a stream of trivially rejected/lower/conflicting proposals, without ever producing a block that reaches consensus. This blocks the signer set's inactivity fallback to the prior tenure — a liveness wedge, structurally the same "per-event check defeated by chunking" flaw as the WOO report.

### Finding Description
`check_latest_block_in_tenure` is the shared chainstate check invoked at proposal arrival, at validate-ok, and at signing time [1](#0-0) . When a proposed block does not confirm as many blocks as expected (i.e. it is not higher than the last signed block for the tenure), the function still calls `signer_db.update_last_activity_time(...)` to record this rejected attempt as legitimate miner activity, rather than doing nothing: [2](#0-1) 

The same pattern repeats for proposals that conflict with a merely pre-committed (unsigned) block: the check still refuses the proposal, but again stamps activity: [3](#0-2) 

This activity timestamp is exactly what gates the signer's inactivity fallback path. Per the flow documentation, `check_miner_inactivity` / `is_timed_out` only falls back to the prior tenure's miner when the current miner has been inactive past `block_proposal_timeout` and has not signed a block in the tenure: [4](#0-3) 

There is no per-source rate limit, deduplication, or cap on how often a single miner's rejected proposals can refresh this timestamp — every rejected/conflicting proposal (a message entirely of the miner's own making, at a cadence and volume it fully controls) counts. This mirrors the WOO bug class exactly: a control meant to bound a *cumulative* condition (inactivity over a window) is implemented as a per-event check that resets on every event, so an actor who can generate an unbounded stream of individually-harmless (here: individually-rejected) events can defeat the intended aggregate limit.

### Impact Explanation
A single sortition-winning miner — who need not control any signer key, and needs no cooperation from other signers — can flood the signer set with a stream of proposals that are guaranteed to fail `check_latest_block_in_tenure` (e.g., proposals at or below the currently pre-committed/signed height, or reproposals conflicting with a pre-commit) while never producing a proposal that can actually reach the 70% pre-commit/signature threshold. As long as this stream arrives faster than `block_proposal_timeout`, `is_timed_out` never observes a gap, so `check_miner_inactivity` never triggers the fallback to the prior tenure's miner. The signer set is thereby wedged into perpetually waiting on a miner who is deliberately never producing a valid, confirmable block — a liveness stall of tenure production reachable by a single non-signer actor. This matches the "High" impact class of a signer (here, the whole signer set) being wedged such that valid blocks (from the legitimate fallback miner) can never be signed.

### Likelihood Explanation
The attack requires only the ability to win a sortition (become the tenure's miner) and to submit block proposals — a capability inherent to mining, not requiring any signer collusion, signer key, or majority. Generating a stream of trivially-rejected proposals (e.g. re-proposing at the same/lower height, or proposals that conflict with an already pre-committed block) is cheap and fully under the miner's control, and the check that would otherwise gate this (`check_latest_block_in_tenure`) is exactly the one being used to (mis)count these attempts as "activity." This makes the wedge realistically triggerable by any single miner willing to stall.

### Recommendation
Do not treat every rejected/conflicting proposal as unconditionally refreshing activity. Instead, bound how much credit repeated attempts from the same miner/tenure can contribute towards activity within a rolling window (e.g., only the first rejection in some sub-interval counts, or cap the number of resets per `block_proposal_timeout` window), so that liveness fallback cannot be indefinitely deferred by a miner who never produces a signable block. Alternatively, gate the fallback decision on whether *any* proposal from the tenure has made forward progress (e.g., reached pre-commit) rather than on raw "message seen" activity.

### Proof of Concept
1. Miner M wins the sortition for tenure T (single miner, no signer collusion needed).
2. M sends a block proposal B1 at height h that the signer set pre-commits to (but never signs to consensus, e.g., by withholding continuation).
3. Before `block_proposal_timeout` elapses, M sends B2 at height h (same or lower), which fails the check at [2](#0-1)  — it is rejected, but `update_last_activity_time` is still called, resetting the clock.
4. M repeats step 3 indefinitely (B3, B4, ...), each individually rejected, each still refreshing `last_activity_time` per [5](#0-4)  / [6](#0-5) .
5. Because `is_timed_out`/`check_miner_inactivity` never see a gap exceeding `block_proposal_timeout` (per [4](#0-3) ), the signer set never falls back to the prior tenure's miner, and tenure T never produces a globally accepted block — a liveness wedge sustained entirely by M's own cheap, individually-rejected proposals.

### Citations

**File:** docs/signer-flows.md (L391-398)
```markdown
`check_latest_block_in_tenure` answers "does this block confirm the tip we
expect?" and it runs in three places: at proposal arrival (inside
`check_proposal`), at validate-ok, and at the moment of signing. _Which_ tenure
it is asked about depends on the block: a tenure-change block is checked against
its **parent** tenure, every other block against its **own**. Never both. The
pivotal helper is `get_tenure_last_block_info`, which considers only blocks that
carry a signature (`get_last_signed_block`): a pre-commit never vetoes anything,
it only counts as miner activity.
```

**File:** docs/signer-flows.md (L464-468)
```markdown
    HPU["housekeeping:<br/>handle_pending_update"] --> PEND{"a pending BurnBlock<br/>update to settle?"}
    PEND -- yes --> ARR
    PEND -- no --> TO{"current tenure timed out?<br/>check_miner_inactivity →<br/>v1/v2 SortitionState::is_timed_out"}
    TO -- "signed a block in tenure?<br/>has_signed_block_in_tenure" --> NEVER(["never times out —<br/>we committed a signature"])
    TO -- "no signed block, and inactive<br/>past block_proposal_timeout" --> FALL["fall back to prior tenure:<br/>make_miner_state(prior sortition)"]
```

**File:** stacks-signer/src/chainstate/mod.rs (L395-418)
```rust
            if block.header.chain_length <= info.block.header.chain_length {
                warn!(
                    "Miner's block proposal does not confirm as many blocks as we expect";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "proposed_chain_length" => block.header.chain_length,
                    "expected_at_least" => info.block.header.chain_length + 1,
                );
                if info.signed_group.is_none_or(|signed_time| {
                    signed_time + reorg_attempts_activity_timeout.as_secs() > get_epoch_time_secs()
                }) {
                    // Note if there is no signed_group time, this is a locally accepted block (i.e. tenure_last_block_proposal_timeout has not been exceeded).
                    // Treat any attempt to reorg a locally accepted block as valid miner activity.
                    // If the call returns a globally accepted block, check its globally accepted time against a quarter of the block_proposal_timeout
                    // to give the miner some extra buffer time to wait for its chain tip to advance
                    // The miner may just be slow, so count this invalid block proposal towards valid miner activity.
                    if let Err(e) = signer_db.update_last_activity_time(
                        &block.header.consensus_hash,
                        get_epoch_time_secs(),
                    ) {
                        warn!("Failed to update last activity time: {e}");
                    }
                }
                return Ok(false);
```

**File:** stacks-signer/src/chainstate/mod.rs (L422-448)
```rust
        // A block we have only pre-committed to must NOT veto this proposal, but, similar to above
        // this should still count as activity for the miner.
        let last_accepted_block = signer_db
            .get_last_accepted_block(tenure_id)
            .map_err(|e| ClientError::InvalidResponse(e.to_string()))?;
        if let Some(info) = last_accepted_block {
            let is_fresh_pre_commit = info.state == BlockState::PreCommitted
                && info.approved_time.is_some_and(|approved_time| {
                    approved_time.saturating_add(tenure_last_block_proposal_timeout.as_secs())
                        > get_epoch_time_secs()
                });
            if is_fresh_pre_commit && block.header.chain_length <= info.block.header.chain_length {
                info!(
                    "Miner's block proposal conflicts with a block we have only pre-committed to. Counting it as miner activity, but not rejecting the proposal.";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "proposed_chain_length" => block.header.chain_length,
                    "pre_committed_signer_signature_hash" => %info.block.header.signer_signature_hash(),
                    "pre_committed_chain_length" => info.block.header.chain_length,
                );
                if let Err(e) = signer_db
                    .update_last_activity_time(&block.header.consensus_hash, get_epoch_time_secs())
                {
                    warn!("Failed to update last activity time: {e}");
                }
            }
        }
```
