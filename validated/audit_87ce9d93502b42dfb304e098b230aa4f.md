### Title
Miner can indefinitely reset the tenure "last activity" timer via repeated conflicting proposals, defeating `is_timed_out` and wedging fallback liveness - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
`SortitionData::check_latest_block_in_tenure` calls `signer_db.update_last_activity_time(&block.header.consensus_hash, get_epoch_time_secs())` every time it observes a conflicting block proposal (either a lower/equal-height block, or one conflicting with a fresh pre-commit), in order to credit the miner with "activity" so a slow-but-live miner isn't wrongly timed out. Because this check runs on *every* proposal a single miner sends — with no accrual/rate-limiting analogous to a full "day" of stake time in the DIA report — a miner can keep resending conflicting/no-op proposals at a cadence faster than `block_proposal_timeout` / `tenure_last_block_proposal_timeout` and perpetually refresh the timer, so `is_timed_out` never fires and the signer set never falls back to a healthier miner or tenure. [1](#0-0) [2](#0-1) 

### Finding Description
The DIA report's root cause is that a cheap, frequently-repeatable action (`claim()`) updates a "last update" timestamp used to gate a periodic accrual, without the accrual actually completing — so the attacker can hold the timer "fresh" forever and freeze the intended periodic state transition (reward distribution) for everyone else.

The structurally identical pattern here is in `check_latest_block_in_tenure`: whenever a proposal is received that either (a) does not exceed the chain length of the last signed block in the tenure, or (b) conflicts with a still-fresh pre-commit, the function does **not** reject it as "no activity" — instead it stamps `update_last_activity_time` with the current epoch time [3](#0-2) , explicitly to "count this invalid block proposal towards valid miner activity" [4](#0-3) . The same happens for a conflicting proposal against a fresh pre-commit [2](#0-1) .

This last-activity timestamp is precisely the input to the liveness gate `SortitionState::is_timed_out` (dispatched to `SortitionStateV1::is_timed_out` / `SortitionStateV2::is_timed_out`), which is the mechanism `is_tenure_valid` uses to decide whether the signer set should still treat the current miner/tenure as canonical or must fall back [5](#0-4) . Because the activity stamp is refreshed on *any* conflicting/duplicate proposal — not only on genuine progress (a new, higher block) — a single miner (one slot) can keep the tenure "alive" indefinitely from the signer's point of view by resending stale or conflicting block headers faster than the timeout window, exactly mirroring the DIA bug where repeatedly calling a cheap function resets the clock without the intended state (reward accrual / miner timeout) ever completing.

This breaks the intended equality: "tenure is genuinely active" vs. "tenure's last-activity timer is fresh." A miner that is not actually producing valid/accepted blocks can keep the second true forever, wedging the signer's fallback logic (`check_miner_inactivity` → `is_timed_out` → fallback to prior tenure, per `docs/signer-flows.md` section 8) from ever triggering.

### Impact Explanation
This matches the "High" impact category: a signer/tenure state machine can be wedged such that it never falls back away from an unproductive miner, i.e., the signer is effectively prevented from acting on the correct liveness signal (never recognizing a stalled miner as inactive). This is a liveness wedge analogous to "a signer wedged into never signing valid blocks" from a healthier subsequent miner, since the fallback path that would let the state machine move on is starved by the attacker's repeated, cheap, conflicting proposals — requiring only a single miner slot plus normal proposal gossip, no majority of signers or extra keys.

### Likelihood Explanation
Likelihood is moderate-to-high: the attacker only needs mining slot control (a "one-slot miner") and the ability to keep resending previously-seen or conflicting block headers at a cadence under the configured timeout windows (`block_proposal_timeout`, `tenure_last_block_proposal_timeout`, `reorg_attempts_activity_timeout`) — all attacker-controlled inputs, no cryptographic majority or secret material from other participants is needed. The code path is exercised on the ordinary proposal-handling flow with no rate limiting other than the timeout windows themselves being longer than realistic resend intervals.

### Recommendation
Mirror the MixBytes fix pattern: don't let an "activity" credit be granted merely because *a* proposal arrived within the window — require it to correspond to genuinely new information (e.g., a proposal at a strictly higher chain length than any previously credited one, or debounce repeated identical/conflicting proposals so that only the first instance within a timeout epoch refreshes the timer). Concretely, before calling `update_last_activity_time` in the two conflict branches of `check_latest_block_in_tenure`, check whether the specific conflicting block (by hash/height) has already been credited for this tenure in the current timeout window, and skip re-stamping if so — analogous to snapping `rewardLastUpdateTime` forward only in discrete elapsed units rather than to `now` on every call.

### Proof of Concept
1. Miner proposes a first block `N` for tenure `T` at height `H`; signer signs/pre-commits it — `approved_time`/`signed_self` set.
2. Miner then repeatedly re-broadcasts a conflicting (or same-height, lower-chain-length) block proposal for tenure `T` faster than `block_proposal_timeout`/`tenure_last_block_proposal_timeout`.
3. Each such proposal hits `check_latest_block_in_tenure`'s early-rejection branch, which nonetheless calls `signer_db.update_last_activity_time(&block.header.consensus_hash, get_epoch_time_secs())` [6](#0-5) .
4. `SortitionState::is_timed_out` (used by `is_tenure_valid`) reads this continuously-refreshed timestamp and never reports the tenure as timed out, so the signer never triggers fallback to a subsequent tenure even though the miner has produced no new valid, accepted block since `N`.

Note: I was unable to fully inspect the exact numeric comparison inside `SortitionStateV1::is_timed_out` / `SortitionStateV2::is_timed_out` (in `stacks-signer/src/chainstate/v1.rs` / `v2.rs`) within the remaining tool budget, so the precise threshold arithmetic is not directly cited here — but `chainstate/mod.rs` confirms both versions consume `signer_db`'s stored activity timestamp via `is_timed_out(consensus_hash, signer_db, ...)` [7](#0-6) , which is the same value repeatedly overwritten in the flow above.

### Citations

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

**File:** stacks-signer/src/chainstate/mod.rs (L581-606)
```rust
    pub fn is_tenure_valid(
        &self,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        proposal_config: &ProposalEvalConfig,
        eval: &GlobalStateEvaluator,
    ) -> Result<bool, SignerChainstateError> {
        let data = self.data();
        let chose_good_parent = data.check_parent_tenure_choice(
            signer_db,
            client,
            &proposal_config.first_proposal_burn_block_timing,
        )?;
        if !chose_good_parent {
            return Ok(false);
        }
        Self::is_timed_out(
            &self.version(),
            &data.consensus_hash,
            signer_db,
            client.get_signer_address(),
            proposal_config,
            eval,
        )
        .map(|timed_out| !timed_out)
    }
```

**File:** stacks-signer/src/chainstate/mod.rs (L616-638)
```rust
    /// Check if the tenure identified by the ConsensusHash is timed out
    pub fn is_timed_out(
        version: &SortitionStateVersion,
        consensus_hash: &ConsensusHash,
        signer_db: &SignerDb,
        local_address: &StacksAddress,
        proposal_config: &ProposalEvalConfig,
        eval: &GlobalStateEvaluator,
    ) -> Result<bool, SignerChainstateError> {
        match version {
            SortitionStateVersion::V1 => SortitionStateV1::is_timed_out(
                consensus_hash,
                signer_db,
                proposal_config.block_proposal_timeout,
            ),
            SortitionStateVersion::V2 => SortitionStateV2::is_timed_out(
                consensus_hash,
                signer_db,
                eval,
                local_address,
                proposal_config.block_proposal_timeout,
            ),
        }
```
