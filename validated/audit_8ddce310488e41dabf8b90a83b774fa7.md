### Title
Malicious tenure-holder can indefinitely refresh its own inactivity timer via rejected reorg-attempt proposals, permanently blocking signer fallback to the next miner - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
The signer's miner-inactivity mechanism (`SortitionState::is_timed_out` in `stacks-signer/src/chainstate/v2.rs`) is the equivalent of the report's "fallback operator" recovery path: it lets signers stop waiting on an unresponsive miner and hand off to another miner once `block_proposal_timeout` has elapsed since the miner's last recorded activity. That activity clock, however, can be refreshed by the very miner it is meant to police, using proposals that are simultaneously rejected. This mirrors the reported pattern where the entity whose inactivity triggers a fallback can itself repeatedly reset the timer that guards the fallback, stalling recovery indefinitely.

### Finding Description
`SortitionState::is_timed_out` computes elapsed time since `signer_db.get_last_activity_time(sortition)` (falling back to burn-block-received time), and only returns `true` (miner timed out, eligible for fallback) once that elapsed time exceeds `block_proposal_timeout`: [1](#0-0) 

`last_activity_time` is bumped by `SortitionData::check_latest_block_in_tenure` in two places, both of which apply to proposals that are ultimately *rejected*: [2](#0-1) [3](#0-2) 

The comment justifying the first refresh is explicit about the tradeoff: "The miner may just be slow, so count this invalid block proposal towards valid miner activity." This is a deliberate design choice to avoid false timeouts for a merely slow miner, but it means *any* proposal from the current tenure's miner that conflicts with an already-signed or pre-committed block — i.e. a proposal that will be rejected with `RejectReason::InvalidParentBlock` — still resets the inactivity clock as a side effect. The miner does not need to ever produce a block that advances the chain; it only needs to keep submitting distinguishable, cheaply-constructed proposals referencing its own tenure often enough (well within `block_proposal_timeout`, and again within `reorg_attempts_activity_timeout` for the freshness check at lines 403-405) to keep tripping this branch.

Because `is_timed_out` never returns `true`, `SortitionsView::check_proposal` (v1) / `GlobalStateView` miner-state logic (v2) never marks the current miner `InvalidatedBeforeFirstBlock`, so:
- signers never fall back to the prior tenure's miner, and
- the current miner's tenure never times out to allow the next sortition winner's proposals to be treated as valid.

This is the equality/wedge being broken: the intended invariant "a miner that stops producing valid blocks becomes ineligible after `block_proposal_timeout`" no longer holds, because "activity" is defined too broadly (rejected reorg-attempt proposals count) and is entirely self-reported by the party being timed out.

### Impact Explanation
This is a liveness wedge matching the "signer wedged into never signing valid blocks" impact category: signers become stuck waiting indefinitely on a tenure-holder who produces no valid, chain-advancing block, because that same actor can indefinitely suppress the only mechanism (`is_timed_out`) that would let the network fail over to another miner. Unlike the already-fixed `precommit-suppresses-miner-timeout` issue (where a *pre-commit* falsely counted as suppressing the timeout) and `no-fallback-to-stopped-miner` (fallback across a Bitcoin reorg), this path uses ordinary rejected block proposals — a mechanism that remains live in the current code — to achieve the same indefinite-stall effect.

### Likelihood Explanation
Only requires control of the current sortition slot (a "one-slot miner"), which is the exact actor `is_timed_out`/fallback is meant to protect against. No cooperation from other signers or miners is needed; the attacker only needs to gossip cheap, self-conflicting block proposals (e.g., proposals with lower `chain_length` than an already locally-accepted/pre-committed block in the same tenure) at an interval shorter than `block_proposal_timeout`/`reorg_attempts_activity_timeout`.

### Recommendation
Do not let a proposal that is rejected (and specifically one that never crosses the pre-commit/signature threshold) refresh `last_activity_time` indefinitely. Consider bounding the total extension the "invalid activity" grace period can grant per tenure (e.g., cap cumulative refreshes, or require monotonic progress such as a strictly-increasing `chain_length` or a successful pre-commit crossing threshold) so that a miner producing only rejected proposals still times out eventually.

### Proof of Concept
1. Miner M wins a sortition and starts tenure T; signers pre-commit/sign an initial block at height h in T.
2. M repeatedly gossips new block proposals for tenure T at height ≤ h (or otherwise conflicting with the already-signed/pre-committed block), spaced less than `block_proposal_timeout` and `reorg_attempts_activity_timeout` apart.
3. Each such proposal is rejected via `check_latest_block_in_tenure` returning `Ok(false)`, but before returning, `signer_db.update_last_activity_time(...)` is called (`stacks-signer/src/chainstate/mod.rs` lines 411-416 / 442-446), resetting the clock `is_timed_out` measures from.
4. `SortitionState::is_timed_out` (`stacks-signer/src/chainstate/v2.rs` lines 73-88) never observes elapsed time exceeding `block_proposal_timeout`, so the current miner is never marked invalid, and signers never fail over to the next sortition winner — the tenure stalls indefinitely with no valid block produced.

### Citations

**File:** stacks-signer/src/chainstate/v2.rs (L63-89)
```rust
        let has_block = signer_db.has_signed_block_in_tenure(sortition)?;
        if has_block {
            return Ok(false);
        }
        let Some(received_ts) =
            signer_db.get_burn_block_received_time_from_signers(eval, sortition, local_address)?
        else {
            return Ok(false);
        };
        let received_time = UNIX_EPOCH + Duration::from_secs(received_ts);
        let last_activity = signer_db
            .get_last_activity_time(sortition)?
            .map(|time| UNIX_EPOCH + Duration::from_secs(time))
            .unwrap_or(received_time);

        let Ok(elapsed) = std::time::SystemTime::now().duration_since(last_activity) else {
            return Ok(false);
        };
        if elapsed > timeout {
            info!("Sortition has timed out";
                "sorition" => %sortition,
                "timeout" => %timeout.as_secs(),
                "elapsed" => %elapsed.as_secs()
            )
        }
        Ok(elapsed > timeout)
    }
```

**File:** stacks-signer/src/chainstate/mod.rs (L395-417)
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
```

**File:** stacks-signer/src/chainstate/mod.rs (L422-447)
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
```
