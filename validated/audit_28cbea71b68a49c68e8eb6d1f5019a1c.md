### Title
Miner can indefinitely suppress `is_timed_out` liveness fallback by repeatedly resubmitting non-advancing/pre-commit-conflicting proposals - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`SortitionData::check_latest_block_in_tenure` treats certain non-advancing, reorg-shaped block proposals as "miner activity" and refreshes `last_activity_time` via `signer_db.update_last_activity_time`, even though the proposal never reaches a signed state. Since `SortitionState::is_timed_out` (both v1 and v2) measures inactivity purely from this same `last_activity_time` field, a single miner-slot winner can keep resubmitting throwaway proposals faster than `block_proposal_timeout` and prevent `is_timed_out` from ever returning `true`, blocking the signer from ever invalidating the current miner and falling back to the prior sortition.

### Finding Description
`is_timed_out` explicitly special-cases the "already-signed" case (`has_signed_block_in_tenure`) to avoid abandoning a tenure the signer already committed to, and its own comment acknowledges that pre-committed-but-unsigned blocks must NOT count towards this exemption "because... the tenure can stall indefinitely" [1](#0-0) . However, the *activity-timer refresh* mechanism used by the timeout calculation is not protected by the same reasoning.

In `check_latest_block_in_tenure`, two branches update `last_activity_time` without ever requiring a proposal to reach a full signature:
- The first branch fires when a new proposal doesn't advance past the last *signed* block in the tenure but the existing signed block's `signed_group` timestamp is recent/absent (a "locally accepted" block); any competing lower/equal-height proposal is explicitly "count[ed]... towards valid miner activity" [2](#0-1) .
- The second branch fires when a proposal conflicts with a merely *pre-committed* block (no signature at all) that is still "fresh"; this is also explicitly counted as activity [3](#0-2) .

`is_timed_out` in both v1 and v2 reads exactly this field: `signer_db.get_last_activity_time(sortition)` [4](#0-3) [5](#0-4) . Nothing bounds how many times this refresh can occur, nor requires forward progress between refreshes — only that the interval between consecutive proposals stays under `block_proposal_timeout`.

Attack: the attacker wins one miner slot for the tenure. They get a single proposal to reach the local `PreCommitted` state (or a locally-accepted-but-not-globally-signed state) and then repeatedly submit alternate/garbage block proposals at the same or lower `chain_length`, timed just under `block_proposal_timeout`. Each submission takes the "activity" branch in `check_latest_block_in_tenure`, calling `update_last_activity_time` and resetting the clock, while `has_signed_block_in_tenure` remains `false` throughout because none of the garbage proposals ever complete a full signature. Consequently `is_timed_out` never observes `elapsed > block_proposal_timeout` and always returns `false`, so `SortitionState::is_tenure_valid` / `check_proposal` never flips the current miner to `InvalidatedBeforeFirstBlock`, and the signer never falls back to `last_sortition`.

### Impact Explanation
This breaks the bounded-liveness guarantee that `block_proposal_timeout` is supposed to provide: an inactive/misbehaving miner's tenure must eventually be treated as timed out so the signer set can safely process the prior sortition's miner instead. Because a single, unprivileged miner-slot holder can indefinitely refresh `last_activity_time` without ever producing a valid, fully-signed block, the signer set is wedged waiting on that tenure. This is a High-severity liveness issue (signer wedged into never signing valid blocks / never falling back), not a safety violation — no invalid block is signed.

### Likelihood Explanation
Preconditions are met purely with attacker resources already in scope of the threat model: winning one Bitcoin-based miner slot and being able to gossip self-signed `BlockProposal`s. No majority of signers, no compromised keys, and no node/local access are required. The attack is fully repeatable — the attacker only needs to keep the proposal cadence under `block_proposal_timeout`, indefinitely, for as long as they wish to stall fallback.

### Recommendation
Decouple the "counts as activity" heuristic from unrestricted re-triggering: bound the number of activity-refreshes per tenure per proposer, or require that a proposal actually make forward progress (e.g., higher `chain_length`, or a genuinely new signature attempt) before it is allowed to refresh `last_activity_time`. Alternatively, cap the total extension time a tenure can receive from repeated non-signed "activity" resets (e.g., track a separate "first-activity" timestamp and enforce an absolute ceiling in addition to the rolling inactivity window), ensuring `is_timed_out` still fires once that ceiling is reached even if activity keeps resetting.

### Proof of Concept
Rust test plan in `stacks-signer/src/chainstate/tests` (or an equivalent test harness driving `SignerDb` + `SortitionData`):
1. Set up a `SignerDb` with a tenure `consensus_hash` `ch`, `block_proposal_timeout = T`.
2. Insert a block `B0` at `chain_length = N` and mark it `PreCommitted` with `approved_time = t0` (no signature; `has_signed_block_in_tenure(ch)` stays `false`).
3. Loop `k` times: at time `t0 + k*(T - ε)`, call `SortitionData::check_latest_block_in_tenure(&ch, &B_reorg, ...)` where `B_reorg.chain_length <= N`, so it hits the pre-commit "activity" branch and calls `update_last_activity_time`.
4. After each iteration, assert `SortitionState::is_timed_out(&SortitionStateVersion::V1, &ch, &signer_db, ..., Duration::from_secs(T))` returns `Ok(false)`, and `signer_db.has_signed_block_in_tenure(&ch)` remains `false`.
5. Run the loop for a duration `> 10*T` real/simulated time, asserting `is_timed_out` never returns `true`, demonstrating the tenure never times out despite no block ever being signed — confirming the liveness wedge.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L60-71)
```rust
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
```

**File:** stacks-signer/src/chainstate/v1.rs (L76-93)
```rust
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
```

**File:** stacks-signer/src/chainstate/mod.rs (L403-417)
```rust
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

**File:** stacks-signer/src/chainstate/mod.rs (L427-447)
```rust
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

**File:** stacks-signer/src/chainstate/v2.rs (L73-88)
```rust
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
```
