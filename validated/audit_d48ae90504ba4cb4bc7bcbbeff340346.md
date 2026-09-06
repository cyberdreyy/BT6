### Title
Cheap low-height block proposals let a tenure's miner indefinitely refresh `last_activity_time` and suppress the inactivity timeout, wedging signers from ever falling back to a replacement miner - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`check_latest_block_in_tenure()` refreshes a per-tenure `last_activity_time` in SignerDB whenever a proposal is judged to be "valid miner activity," even when the proposal itself is rejected for not extending the chain. Because this timestamp is the sole input (together with a fallback of the burn-block-received time) to `SortitionState::is_timed_out()`, a miner who never actually finishes a tenure can keep resetting the inactivity clock with cheap, doomed block proposals, permanently suppressing the `check_miner_inactivity` fallback path that would otherwise let signers switch back to the prior (legitimate) tenure and its miner. This is structurally the same bug class as the mVeNFT report: an action that is supposed to be incidental bookkeeping (`_poke` updating `lastVotedTimestamps`, here `check_latest_block_in_tenure` updating `last_activity_time`) instead re-arms a delay/gate that blocks the very state transition the system relies on for liveness, and it can be triggered repeatedly and cheaply by a single actor.

### Finding Description
`check_latest_block_in_tenure` is called from the block-proposal path (`docs/signer-flows.md` section 3, `check_block_against_state` → v1/v2 `check_proposal`) whenever a signer evaluates whether a proposed block extends the highest known block in its claimed parent tenure. [1](#0-0) 

When the proposal does **not** extend the known chain length, instead of simply being rejected, the code decides whether this attempt still counts as "valid miner activity" and, if so, calls `signer_db.update_last_activity_time(&block.header.consensus_hash, get_epoch_time_secs())`: [2](#0-1) 

The same update is repeated for the "pre-committed but not yet globally accepted" branch just below it: [3](#0-2) 

`update_last_activity_time` is a blind upsert with no rate limit and no requirement that the proposal be otherwise sane beyond a valid miner signature over the header: [4](#0-3) 

That timestamp is the input to the miner-inactivity check that governs fallback to the prior tenure. In v2 chainstate, `is_timed_out` uses `get_last_activity_time` (falling back to burn-block-received time only if no activity record exists) compared against `block_proposal_timeout`: [5](#0-4) 

This feeds `check_miner_inactivity`, which is the only mechanism (per `docs/signer-flows.md` section 8) that lets the signer state machine fall back to the prior tenure's miner when the current miner has gone silent: [6](#0-5) 

Because a valid miner (the sortition winner, a single actor, i.e. a "one-slot miner") can sign and gossip arbitrarily many block proposals at negligible cost — each carrying a lower `chain_length` than the tenure's already-known tip, or conflicting with an already pre-committed block — every such proposal is treated as "activity" and rearms `last_activity_time`. The equality this breaks is: *`last_activity_time` should reflect genuine progress/liveness of the tenure's miner, but instead it is settable by an unbounded stream of proposals that are known, at evaluation time, not to extend the chain.* The signer's local state machine (`check_miner_inactivity` in `signer_state.rs`) therefore never reaches its `is_timed_out == true` branch, and the `FALL` transition to `make_miner_state(prior sortition)` in section 8 of the flow never fires.

### Impact Explanation
This is a liveness wedge on the signer state machine: signers can be kept waiting indefinitely for a miner that never produces a canonical extension, because the very inactivity signal meant to detect and route around such a miner is refreshable by the miner itself via proposals that are rejected on their merits. The practical effect is that the network cannot fall back to the previous (or any alternate) tenure, so no new valid block gets signed for as long as the attack continues — matching the "High: a signer wedged into never signing valid blocks" impact category. It does not directly cause a signature over an invalid/non-canonical block, so it sits at the liveness-wedge end of the impact spectrum rather than a signing-integrity break.

### Likelihood Explanation
The trigger requires only the current tenure's single miner (the sortition winner already holding block-signing authority for that tenure) plus ordinary StackerDB/network propagation of block proposals — no signer collusion, no majority, and no additional privileges. Producing a signed header with a stale/low `chain_length` is cheap and can be repeated at will before `block_proposal_timeout` elapses, resetting the clock each time. Likelihood is therefore reasonably high whenever a miner's incentive is to stall rather than yield the tenure (e.g., to protect an already-mined but not-yet-accepted competing block, or to grief).

### Recommendation
Do not treat proposals that fail the chain-length/height check as unconditional evidence of liveness. Options: (1) bound how often a single tenure's `last_activity_time` can be advanced by rejected/non-extending proposals (e.g., only count the first such proposal per timeout window, or require monotonically increasing proposal content/timestamp rather than wall-clock receipt time); (2) decouple "the miner is present" from "the miner is making progress" by requiring at least a strictly increasing candidate chain length or a distinct nonce/timestamp per counted activity event; (3) cap the maximum extension `last_activity_time` can grant relative to the original burn-block-received time, so a determined miner cannot postpone the fallback indefinitely.

### Proof of Concept
1. Miner M wins the sortition for tenure T and proposes an initial block B0 that is accepted/pre-committed by signers (establishing a last-block/tenure record).
2. M then repeatedly signs and gossips new block proposals B1, B2, … for tenure T, each with a `chain_length` no greater than the last known height (or conflicting with the pre-committed block), spaced just under `block_proposal_timeout` apart.
3. On each arrival, `check_latest_block_in_tenure` rejects the proposal (`chain_length <= known chain_length`) but, because the previously signed/pre-committed block's `signed_group`/`approved_time` is not yet stale relative to `reorg_attempts_activity_timeout`, it calls `update_last_activity_time(consensus_hash, now)`.
4. `SortitionState::is_timed_out` for tenure T therefore always sees `elapsed <= timeout`, so `check_miner_inactivity`'s `FALL` branch (falling back to the prior tenure) never executes.
5. Signers remain wedged waiting on tenure T; no new canonical block can be signed until M chooses to stop, even though M is not making genuine progress.

*(Verification limited by index coverage: I could not view the full body of `check_miner_inactivity` in `stacks-signer/src/v0/signer_state.rs` before the session ended, so the exact call-site wiring from `is_timed_out` to the `FALL` transition is taken from `docs/signer-flows.md` and the `chainstate` modules rather than direct inspection of that function's source. A Devin session with full file access would be needed to confirm the precise guard conditions in `check_miner_inactivity` and any rate-limiting already present there.)*

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L376-420)
```rust
    pub fn check_latest_block_in_tenure(
        tenure_id: &ConsensusHash,
        block: &NakamotoBlock,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        tenure_last_block_proposal_timeout: Duration,
        reorg_attempts_activity_timeout: Duration,
    ) -> Result<bool, ClientError> {
        let last_block_info = SortitionData::get_tenure_last_block_info(
            tenure_id,
            signer_db,
            tenure_last_block_proposal_timeout,
        )?;

        if let Some(info) = last_block_info {
            // N.B. this block might not be the last globally accepted block across the network;
            // it's just the highest one in this tenure that we know about.  If this given block is
            // no higher than it, then it's definitely no higher than the last globally accepted
            // block across the network, so we can do an early rejection here.
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

**File:** stacks-signer/src/signerdb.rs (L2248-2257)
```rust
    /// Update the tenure (identified by consensus_hash) last activity timestamp
    pub fn update_last_activity_time(
        &mut self,
        tenure: &ConsensusHash,
        last_activity_time: u64,
    ) -> Result<(), DBError> {
        debug!("Updating last activity for tenure"; "consensus_hash" => %tenure, "last_activity_time" => last_activity_time);
        self.db.execute("INSERT OR REPLACE INTO tenure_activity (consensus_hash, last_activity_time) VALUES (?1, ?2)", params![tenure, u64_to_sql(last_activity_time)?])?;
        Ok(())
    }
```

**File:** stacks-signer/src/chainstate/v2.rs (L45-89)
```rust
impl SortitionState {
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

**File:** docs/signer-flows.md (L464-468)
```markdown
    HPU["housekeeping:<br/>handle_pending_update"] --> PEND{"a pending BurnBlock<br/>update to settle?"}
    PEND -- yes --> ARR
    PEND -- no --> TO{"current tenure timed out?<br/>check_miner_inactivity →<br/>v1/v2 SortitionState::is_timed_out"}
    TO -- "signed a block in tenure?<br/>has_signed_block_in_tenure" --> NEVER(["never times out —<br/>we committed a signature"])
    TO -- "no signed block, and inactive<br/>past block_proposal_timeout" --> FALL["fall back to prior tenure:<br/>make_miner_state(prior sortition)"]
```
