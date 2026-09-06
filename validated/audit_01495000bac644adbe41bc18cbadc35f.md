### Title
Malicious miner can indefinitely suppress its own tenure timeout by re-proposing always-invalid blocks, wedging the signer set into waiting forever - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
The activity-timer mechanism that lets signers detect an inactive/stalling miner and fall back to the prior tenure is fed by `check_latest_block_in_tenure`, which treats *any* rejected re-proposal (as long as no block has yet been globally signed in the tenure) as legitimate miner activity and resets the timeout clock. A single miner who never intends to produce a valid block can exploit this to keep resetting the clock forever, exactly analogous to the referenced report's pattern of repeatedly perturbing a fluctuating, self-reported quantity (there: an exchange rate; here: a self-reported "last activity" timestamp) so that a downstream safety check that depends on that quantity's freshness never fires.

### Finding Description
`check_latest_block_in_tenure` in `stacks-signer/src/chainstate/mod.rs` is invoked from `check_proposal` (v1) each time a new proposal is evaluated for a tenure [1](#0-0) . When the incoming proposal is not higher than the tenure's last known block, the function nonetheless calls `signer_db.update_last_activity_time(...)` with the current wall-clock time as long as `info.signed_group` is `None` (i.e., no block in the tenure has yet been globally accepted): [2](#0-1) 

That timestamp is later read back by `SortitionState::is_timed_out` in `stacks-signer/src/chainstate/v1.rs`, which computes `elapsed = now - last_activity` and only declares the miner timed out once `elapsed > block_proposal_timeout`: [3](#0-2) 

Per `docs/signer-flows.md`, this timeout is what drives the fallback logic ("no signed block, and inactive past `block_proposal_timeout`: fall back to prior tenure") in the burn-block/miner-view state machine: [4](#0-3) 

Because the "activity" update only requires a *fresh, distinct* proposal that still fails the height check (not a valid or even plausible block), a single miner who has won the current tenure slot can:
1. Never build/broadcast a valid, higher block.
2. Periodically broadcast a trivially-mutated (e.g. different timestamp) but still-invalid/lower-height block proposal, well within `block_proposal_timeout`.
3. Each such fresh proposal is evaluated via `handle_block_proposal` → `check_block_against_state` → `check_proposal` → `check_latest_block_in_tenure`, and — since `signed_group` is still `None` for the tenure — resets `last_activity_time` to "now" every time.
4. `is_timed_out` therefore never returns `true`, so the signer never falls back to the prior tenure's miner, and the tenure can stall indefinitely while never producing a block the network can build on.

This breaks the intended invariant that `is_timed_out`'s liveness guarantee is tied to genuine miner progress; instead it is tied to an easily-forgeable, attacker-controlled signal (a "fresh-looking" proposal), which is the direct structural analogue of the referenced report's flaw where a value meant to reflect real economic state (an exchange rate) could instead be transiently manipulated by the attacker before being consumed by a downstream check.

### Impact Explanation
This is a liveness wedge triggerable unilaterally by the tenure's own miner (no majority-signer collusion, no auth token, no other signer's key needed): the signer set is kept waiting on a miner that will never produce a signable block, and the timeout/fallback mechanism intended to route around exactly this situation is neutralized. This matches the specified High-severity impact class: "a signer wedged into never signing valid blocks... or acting on a stale reward set/threshold."

### Likelihood Explanation
The only capability required is that of the current tenure's miner (a role a single byzantine/faulty miner naturally holds once it wins a sortition), plus the ability to gossip cheap, distinct-but-invalid block proposals at a rate lower than `block_proposal_timeout`. No cryptographic breaks, no majority signer coordination, and no volumetric flooding are needed — a low-rate stream of proposals easily fits under `block_proposal_timeout` (which is on the order of minutes), making this practically reachable by any miner wishing to stall the chain during its own tenure.

### Recommendation
Decouple the liveness "activity" signal from proposals that are already known to be invalid/non-advancing. Concretely:
- Only treat a re-proposal as activity if it is materially different in a way that indicates genuine progress (e.g., builds on a block/height not previously rejected, or comes from a distinct, previously-unseen tenure state), or
- Bound the number of times `update_last_activity_time` can be refreshed by a rejected/non-advancing proposal within a `block_proposal_timeout` window, or
- Base the timeout window on the *sortition's* burn-block-receive time only (ignore attacker-controlled "last activity" resets entirely) once a configurable number of consecutive non-advancing proposals have been seen from the same tenure.

### Proof of Concept
1. Miner M wins the sortition for tenure T and never gets a block globally signed in T (`signed_group` stays `None` for the tenure).
2. M crafts a block proposal `B0` for T that is deliberately invalid or at a height ≤ the tenure's last known block (e.g., reproposing height 1 while a higher block already exists, or reusing an already-rejected shape with a bumped timestamp so it is treated as a "fresh" proposal by `should_reevaluate_block`).
3. Signers evaluate `B0` via `handle_block_proposal` → `check_block_against_local_state`/`check_block_against_global_state` → `check_proposal` → `check_latest_block_in_tenure`. Since `block.header.chain_length <= info.block.header.chain_length` and `info.signed_group.is_none()`, `update_last_activity_time(T, now)` fires [5](#0-4) .
4. M waits just under `block_proposal_timeout`, then sends `B1` (same idea, different timestamp), triggering the same reset.
5. Repeating step 4 indefinitely keeps `SortitionState::is_timed_out(T, ...)` false forever [6](#0-5) , so the miner-view state machine's fallback-to-prior-tenure path in `docs/signer-flows.md` §8 never activates, and the chain stalls under M's tenure with no valid block ever produced or accepted.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L376-389)
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

```

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

**File:** docs/signer-flows.md (L466-469)
```markdown
    PEND -- no --> TO{"current tenure timed out?<br/>check_miner_inactivity →<br/>v1/v2 SortitionState::is_timed_out"}
    TO -- "signed a block in tenure?<br/>has_signed_block_in_tenure" --> NEVER(["never times out —<br/>we committed a signature"])
    TO -- "no signed block, and inactive<br/>past block_proposal_timeout" --> FALL["fall back to prior tenure:<br/>make_miner_state(prior sortition)"]
    TICK["housekeeping:<br/>capitulate_viewpoint<br/>(rate-limited by<br/>capitulate_miner_view_timeout)"] --> UPD["update_parent_tenure_last_block:<br/>adopt newer node tip or drop a<br/>signed view that went stale"]
```
