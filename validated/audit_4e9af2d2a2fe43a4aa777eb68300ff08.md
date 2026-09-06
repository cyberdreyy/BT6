### Title
Miner can indefinitely suppress `block_proposal_timeout` fallback by re-sending stale/conflicting proposals that keep bumping `last_activity_time` - (File: stacks-signer/src/chainstate/mod.rs)

### Summary
`SortitionData::check_latest_block_in_tenure` calls `signer_db.update_last_activity_time(...)` even when the proposal it is evaluating is *rejected* (stale, non-advancing, or conflicting with an already locally-accepted/pre-committed block), as long as that block is "recent enough." Because `is_timed_out` (the miner-inactivity check that triggers fallback to the prior miner) in `chainstate/v1.rs`/`v2.rs` reads this same `last_activity_time`, a single miner (the one-slot proposer for the tenure) can keep resetting the inactivity clock by repeatedly re-broadcasting a stale/non-progressing block proposal, without ever producing a new signable block. This is directly analogous to the referenced report's bug class: a cheap, no-op-like action (there, a 0-value token transfer; here, a rejected/duplicate block proposal) resets a freshness timestamp that gates a liveness-critical fallback, wedging the state machine so it can never fall back to a legitimate miner.

### Finding Description
`check_latest_block_in_tenure` (stacks-signer/src/chainstate/mod.rs:376-478) is the shared check run at proposal-arrival, validate-ok, and signing time. For a proposal that does *not* advance beyond what's already known in the tenure (`block.header.chain_length <= info.block.header.chain_length`), the function: [1](#0-0) 

still calls `signer_db.update_last_activity_time(&block.header.consensus_hash, get_epoch_time_secs())` before returning `Ok(false)` (i.e., the check fails / the proposal is rejected), as long as `info.signed_group` is either absent (locally-but-not-globally accepted) or recent (within `reorg_attempts_activity_timeout`). The same activity-bump-on-rejection pattern occurs for a fresh pre-commit conflict: [2](#0-1) 

`update_last_activity_time` writes the timestamp consumed by `SortitionState::is_timed_out` in both protocol versions: [3](#0-2) [4](#0-3) 

`is_timed_out` is the sole gate deciding whether the signer's local miner-view state machine falls back to the prior miner after `block_proposal_timeout` (docs/signer-flows.md section 8): [5](#0-4) 

Because the check that touches `update_last_activity_time` runs on *every* incoming proposal (including ones that fail because they are stale, duplicate, or lower than an already-known block in the same tenure), a miner who has already had one block locally accepted or pre-committed in a tenure can repeatedly rebroadcast that same block (or any non-advancing variant), which costs it nothing beyond issuing another block header, and each such rebroadcast resets `last_activity_time`. This defeats the purpose of `block_proposal_timeout`: rather than measuring "time since real forward progress," it measures "time since any proposal, even a rejected one, arrived."

### Impact Explanation
This breaks the liveness guarantee the timeout is meant to provide: `is_timed_out` guards fallback to the prior miner when the current miner stops making progress (docs/signer-flows.md: "no signed block, and inactive past block_proposal_timeout → fall back to prior tenure"). A miner that wins a sortition, gets one block locally accepted or pre-committed, and then stalls (refuses to build further, e.g. due to a stuck mempool, malice, or to grief the network) can keep the signer set from ever timing it out — and thus from ever tenure-extending the previous miner — simply by resending the same or a non-advancing block proposal near the timeout boundary. This is a High-severity liveness wedge: "a signer wedged into never signing valid blocks" (acting on the stale/stuck miner state indefinitely), achievable by a single miner (the one-slot proposer role explicitly allowed by the rules) without needing a majority of signers or another signer's key.

### Likelihood Explanation
Reachable by any block-proposing miner without cooperation from other signers: it only requires the ability to submit `BlockProposal` messages for a tenure it has already won (which it always has, since it is the current sortition winner), and to have already gotten one block locally accepted or pre-committed (the common case in any real tenure). Re-sending a stale/non-advancing proposal periodically (before `block_proposal_timeout` elapses) is a cheap, on-demand, or front-runnable action — mirroring the referenced report's "front-run to reset timestamp" pattern.

### Recommendation
Do not treat a *rejected* (non-advancing/conflicting) proposal as miner activity for the purposes of `is_timed_out`/`block_proposal_timeout`. Distinguish "the miner is alive but proposing something invalid/stale" from "the miner is making real forward progress": either stop calling `update_last_activity_time` on the non-advancing branches of `check_latest_block_in_tenure`, or introduce a separate, rate-limited/monotonic "genuine progress" timestamp (e.g., only updated when a *new*, higher block height is proposed, distinct from the reorg-detection activity bump) that specifically feeds `is_timed_out`, so it cannot be reset indefinitely by resubmitting the same or an already-superseded block.

### Proof of Concept
Conceptual walk-through (code paths cited above), since a live Foundry/Rust harness is out of scope here:
1. Miner M wins tenure T. M proposes block B0, which gets locally accepted (`BlockState` recorded via `signer_db`), or pre-committed under `mark_pre_committed` (approved_time stamped).
2. M refuses to build B1, but every `reorg_attempts_activity_timeout` (or before `tenure_last_block_proposal_timeout`) window, M rebroadcasts B0 (or any block with `chain_length <= B0.chain_length`) as a "new" proposal.
3. Each rebroadcast reaches `check_latest_block_in_tenure`; since `block.header.chain_length <= info.block.header.chain_length`, it hits the branch at stacks-signer/src/chainstate/mod.rs:395-419 (or the pre-commit branch at 422-448), calls `update_last_activity_time(consensus_hash, now)`, and returns `Ok(false)` — the proposal itself is rejected, but the side effect persists.
4. Meanwhile `is_timed_out` (v1.rs:55-94 / v2.rs:46-89) computes `elapsed = now - last_activity`, which never exceeds `block_proposal_timeout` because it keeps getting reset.
5. `handle_pending_update`'s `check_miner_inactivity` (docs/signer-flows.md:466-468) therefore never transitions the state machine to fall back to the prior miner, even though M is not producing any new signed blocks — a stall that persists as long as M keeps sending the cheap, rejected re-proposals.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L395-419)
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
            }
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

**File:** stacks-signer/src/chainstate/v2.rs (L46-89)
```rust
    /// Check if the sortition identified by the ConsensusHash is timed out based on
    /// the blocks within the signer db and the block proposal timeout
    pub fn is_timed_out(
        sortition: &ConsensusHash,
        signer_db: &SignerDb,
        eval: &GlobalStateEvaluator,
        local_address: &StacksAddress,
        timeout: Duration,
    ) -> Result<bool, SignerChainstateError> {
        // If we've already signed a block in this tenure, the miner can't have timed out: we have
        // committed a signature to this tenure and must not help abandon it.
        //
        // Importantly, a block we have only pre-committed to does not count! A pre-commit carries
        // no signature, and if it never reaches the pre-commit threshold the tenure can stall
        // indefinitely. Treating it as signed here would suppress the inactivity timeout for
        // exactly the signers that pre-committed, so they could never fall back to the prior
        // miner and the tenure could never recover.
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

**File:** docs/signer-flows.md (L466-468)
```markdown
    PEND -- no --> TO{"current tenure timed out?<br/>check_miner_inactivity →<br/>v1/v2 SortitionState::is_timed_out"}
    TO -- "signed a block in tenure?<br/>has_signed_block_in_tenure" --> NEVER(["never times out —<br/>we committed a signature"])
    TO -- "no signed block, and inactive<br/>past block_proposal_timeout" --> FALL["fall back to prior tenure:<br/>make_miner_state(prior sortition)"]
```
