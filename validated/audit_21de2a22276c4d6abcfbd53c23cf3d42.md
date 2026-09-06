### Title
Miner-triggered `last_activity_time` refresh via cheap conflicting/invalid proposals wedges the tenure-timeout fallback - (File: stacks-signer/src/chainstate/mod.rs)

### Summary
The external report's bug class is a cheap, attacker-controlled action that resets a timer meant to eventually trigger an escape hatch (liquidation), letting the attacker perpetually dodge the safety mechanism. The reachable analog in this repo is `SortitionData::check_latest_block_in_tenure`, which treats *any* conflicting or duplicate block proposal — including ones that never get signed, are below the tenure's already-known height, or merely conflict with a pre-commit — as "miner activity" and refreshes `last_activity_time` for the tenure via `signer_db.update_last_activity_time`. That timestamp is exactly what the miner-inactivity/timeout logic (`is_timed_out` / `check_miner_inactivity`) uses to decide whether to fall back to the prior tenure's miner. A malicious current-tenure miner can therefore keep sending disposable, invalid/conflicting proposals — no majority of signers, no valid signature, no cooperation from anyone else required — to keep refreshing the activity timer forever, exactly the way Alice's dust deposit refreshed `cooldownExpiration` to dodge `liquidateUser`.

### Finding Description
`check_latest_block_in_tenure` (`stacks-signer/src/chainstate/mod.rs`) is the shared check every proposal path (v1 and v2) runs to decide whether a proposed block is confirmed by/above the tenure's known last block. Two of its branches do not merely reject a bad proposal — they also call `update_last_activity_time`, purely because a proposal arrived that conflicts with the current tenure state: [1](#0-0) 

and: [2](#0-1) 

Both branches fire on a proposal that is *not going to be signed* (its height doesn't exceed what's already known, or it merely conflicts with an unsigned pre-commit) — the equivalent of Alice's minimum-viable "dust" deposit that costs almost nothing but still touches the gating variable. The comments in the code even acknowledge the intent ("the miner may just be slow, so count this invalid block proposal towards valid miner activity"), but they do not bound how many times, or how cheaply, this can be triggered.

`last_activity_time` is the same value the tenure-timeout fallback logic consults (documented in `docs/signer-flows.md` §8: `check_miner_inactivity → v1/v2 SortitionState::is_timed_out`, `"no signed block, and inactive past block_proposal_timeout" --> fall back to prior tenure"`): [3](#0-2) 

So the same one-slot actor (the current sortition winner, who alone controls what tenure_id it proposes under) that is supposed to be timed out for failing to produce a valid, signable block can indefinitely postpone that timeout by resubmitting cheap, invalid/duplicate/conflicting proposals — each one re-arms the "is this miner still active" clock — without ever having to get a single signature. This is analogous to `_increaseUserShare`'s `user.cooldownExpiration = block.timestamp + stakingConfig.modificationCooldown()` being re-armed by a minimal deposit that front-runs `liquidateUser`.

### Impact Explanation
This is a liveness wedge in the "signer wedged into never signing valid blocks" category: as long as the malicious miner keeps sending disposable proposals fast enough to beat `block_proposal_timeout`, `check_miner_inactivity` never concludes the miner is inactive, and the signer set's `LocalStateMachine` never falls back to `make_miner_state(prior sortition)`. The network is stuck waiting on a miner that will never produce a valid, signable block, and signers have no other path to progress since the fallback path is precisely the mechanism gated by this timer. This matches the High-impact criterion "a signer wedged into never signing valid blocks... or acting on a stale reward set/threshold" via a liveness stall, achievable by a single miner/proposer with no majority-signer cooperation and no key compromise.

### Likelihood Explanation
The action is cheap and fully under the attacker's control: the current sortition winner can author as many syntactically-conflicting block proposals as it wants (e.g., proposals confirming fewer blocks than already known, or a lower/duplicate `chain_length` colliding with an existing pre-commit), and each such proposal is explicitly coded to still "count as miner activity" even though it is otherwise rejected. No signer cooperation, no valid signature, and no majority are required — only a steady drip of invalid proposals from the tenure's own miner, cheaper than actually building a valid block.

### Recommendation
Do not let a proposal that is rejected/ignored on chainstate grounds (below known height, or conflicting with only a pre-commit) refresh `last_activity_time` unconditionally. Either bound the number/rate of such "activity" refreshes per tenure, or require the refreshed activity to correspond to actual chain progress (e.g., only refresh from proposals that at least match or advance the tenure's already-known height, not ones that are stale/duplicate/rejected), so a miner cannot indefinitely stall the timeout-driven fallback with disposable invalid proposals.

### Proof of Concept
1. Miner M wins the sortition for tenure T.
2. M proposes block B1 with `chain_length` at or below the tenure's already-known last block (or one that merely conflicts with an existing pre-commit signers hold for T).
3. Each signer's `check_latest_block_in_tenure` rejects B1 for consensus purposes but still calls `update_last_activity_time(T, now)` per `stacks-signer/src/chainstate/mod.rs:411-417` / `442-446`.
4. M repeats step 2 with B2, B3, … at an interval shorter than `block_proposal_timeout`.
5. `check_miner_inactivity`/`is_timed_out` (per `docs/signer-flows.md` §8) never observes activity older than `block_proposal_timeout`, so it never falls back to `make_miner_state(prior sortition)`.
6. M never proposes a valid, signable block; the network stalls for as long as M keeps this proposal spam running.

### Citations

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

**File:** docs/signer-flows.md (L464-469)
```markdown
    HPU["housekeeping:<br/>handle_pending_update"] --> PEND{"a pending BurnBlock<br/>update to settle?"}
    PEND -- yes --> ARR
    PEND -- no --> TO{"current tenure timed out?<br/>check_miner_inactivity →<br/>v1/v2 SortitionState::is_timed_out"}
    TO -- "signed a block in tenure?<br/>has_signed_block_in_tenure" --> NEVER(["never times out —<br/>we committed a signature"])
    TO -- "no signed block, and inactive<br/>past block_proposal_timeout" --> FALL["fall back to prior tenure:<br/>make_miner_state(prior sortition)"]
    TICK["housekeeping:<br/>capitulate_viewpoint<br/>(rate-limited by<br/>capitulate_miner_view_timeout)"] --> UPD["update_parent_tenure_last_block:<br/>adopt newer node tip or drop a<br/>signed view that went stale"]
```
