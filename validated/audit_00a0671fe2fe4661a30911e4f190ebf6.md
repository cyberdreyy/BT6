## Analog Found

### Title
Malicious current-tenure miner can spam non-advancing block proposals to permanently reset the signer's inactivity timer, wedging the state machine into never falling back to a valid miner - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
`SortitionData::check_latest_block_in_tenure` treats *any* block proposal from the current tenure's miner that fails to advance the chain (conflicts with an already-accepted or pre-committed block) as "valid miner activity" and calls `signer_db.update_last_activity_time`, provided it's within the freshness window. This activity timestamp is exactly what `SortitionState::is_timed_out` (v1/v2) uses to decide whether the miner has gone inactive and the signer set should fall back to the prior tenure. A single miner who currently holds the tenure slot can cheaply and repeatedly submit non-advancing/conflicting block proposals (gossip cost only, no PoW/stake cost) to keep resetting this timer forever, exactly mirroring the rate-limiter DOS pattern: an operation intended to be benign/rate-safe (counting activity to avoid unfairly abandoning a slow-but-honest miner) is abused via cheap repetition to permanently block a state transition (falling back to a legitimate miner), producing a liveness wedge.

### Finding Description
`check_latest_block_in_tenure` [1](#0-0)  is invoked on every proposal from the tenure's current miner. When the proposed block does not have a higher `chain_length` than the last known signed/accepted block in the tenure, the function rejects it (`Ok(false)`) but — as long as the conflicting block is still "fresh" (locally accepted with no `signed_group` time yet, or globally accepted less than `reorg_attempts_activity_timeout` ago) — it still calls `signer_db.update_last_activity_time(&block.header.consensus_hash, get_epoch_time_secs())` before returning.

A second path in the same function does the same for proposals that conflict with a merely pre-committed (unsigned) block [2](#0-1) .

`SortitionState::is_timed_out` (v1 and v2) is the sole gate for declaring a miner inactive and falling back to the prior tenure: it reads `get_last_activity_time` and compares elapsed time to `block_proposal_timeout` [3](#0-2) . The comment in this function explicitly documents that the whole design intent of the inactivity timeout is to let the signer set eventually abandon a stalled miner and recover the tenure — but a miner who can indefinitely refresh `last_activity_time` defeats that recovery path entirely.

Because both `check_latest_block_in_tenure` update paths are reachable purely from proposal validation logic (not requiring the block to be valid, signed, or advancing the chain), the current tenure's single miner — who controls when and how often to broadcast proposals — can:
1. Broadcast a genuine block proposal once (gets pre-committed/signed by the network normally, or not).
2. Continuously re-broadcast slightly different, non-advancing (same or lower `chain_length`, same consensus hash) signed block headers.
3. Each such rejected proposal still resets `last_activity_time` on every signer's local `SignerDb`, because the check runs before returning `false`.
4. `is_timed_out` therefore never returns `true`, so `check_miner_inactivity`/`handle_pending_update`'s fallback path [4](#0-3)  is never taken.

This breaks the liveness guarantee that a stalled/malicious miner will eventually be timed out (`block_proposal_timeout`) and superseded by the prior tenure's miner extending — the state machine is wedged on the malicious miner indefinitely, with only gas/signing cost to the attacker (cheaper even than the ETH rate-limiter deposit/withdraw cycling in the referenced report, since no funds ever move).

### Impact Explanation
This matches the "High" impact bucket: a signer (indeed, the whole signer set) is wedged into a state where it can never fall back to a valid alternative miner despite the current miner never producing an advancing/valid block. It stalls tenure progression network-wide as long as the malicious miner keeps gossiping cheap junk proposals, which is a concrete liveness break of the inactivity/fallback mechanism that the codebase itself documents as critical for recovery (see the explicit warning in `is_timed_out`'s doc comment about why pre-commits must not suppress the timeout, which shows the authors were aware of — but did not fully close — this class of issue for a different case).

### Likelihood Explanation
High likelihood: the only requirement is that the attacker currently holds the tenure (i.e., won the most recent sortition, a legitimate but adversarial miner), and the ability to broadcast signed block proposals — a capability every miner already has. No majority of signers, no other signer's key, and no auth token are required. The attack cost is limited to the miner's own gossip/signing overhead per proposal, comparable in principle to the "gas fee only" cost cited in the original report.

### Recommendation
`check_latest_block_in_tenure` should not refresh `last_activity_time` for proposals that fail to advance the tenure's chain length / conflict with an already (pre-)committed block from the same miner without some additional distinguishing signal (e.g., limit activity-counting resets to at most once per timeout window per tenure, or require the conflicting proposal to differ meaningfully — e.g., attempt a genuinely higher block — before counting as activity). Alternatively, cap the number of times a given tenure's `last_activity_time` can be refreshed by non-advancing proposals within a `block_proposal_timeout` window, so a real timeout is still reachable regardless of how many junk proposals are gossiped.

### Proof of Concept
1. Miner M wins the current tenure's sortition; signers start a `block_proposal_timeout` countdown via `last_activity_time` (initialized from burn-block-receipt time).
2. M proposes block A at height `chain_length = h` and gets it pre-committed/signed by the network (or even leaves it merely pre-committed).
3. Before `block_proposal_timeout` elapses, M crafts and broadcasts a new signed proposal B with `chain_length <= h` (or `chain_length` not exceeding the tracked tip), for the same tenure's consensus hash.
4. Each signer runs `check_latest_block_in_tenure`, finds B does not advance the chain, rejects it, but — since A's `signed_group`/`approved_time` is still "fresh" relative to `reorg_attempts_activity_timeout` / `tenure_last_block_proposal_timeout` — calls `update_last_activity_time` for M's tenure anyway.
5. M repeats step 3 just before each `block_proposal_timeout` window would otherwise expire.
6. `is_timed_out` never returns true; `check_miner_inactivity` never falls back to the prior miner; the tenure never advances past A, and the network stalls as long as M keeps repeating this cheap loop.

Note: I was unable to fully trace the v1 (`stacks-signer/src/chainstate/v1.rs`) equivalent of the reorg-attempt weighting logic (`>30% reject a reorg attempt` mentioned in the changelog) to confirm whether it independently mitigates this specific non-advancing-proposal activity reset in all code paths — this would need direct inspection of `v1.rs`'s `is_timed_out` and any additional guard added there, which I could not fully verify from the excerpts retrieved.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L376-419)
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
```

**File:** stacks-signer/src/chainstate/v2.rs (L46-80)
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
