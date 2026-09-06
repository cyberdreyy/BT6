This confirms the mechanism precisely, and there's an existing test (`pre_committed_block_does_not_veto_replacement`, `stacks-signer/src/chainstate/tests/v2.rs:955-1040`) that directly demonstrates: a same-height "replacement" proposal conflicting with a fresh pre-commit passes the height check AND calls `update_last_activity_time`, confirmed by the assertion `signer_db.get_last_activity_time(&tenure_id).unwrap().is_some()` right after the check. This is called from `check_block_against_signer_db_state` at every proposal arrival, validate-ok, and signing pass, independent of whether signers ever sign anything. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Title
Winning miner perpetually re-arms the inactivity clock by re-proposing same-height conflicting blocks, blocking fallback to the prior tenure - (File: stacks-signer/src/chainstate/mod.rs)

### Summary
`SortitionState::is_timed_out` (v1.rs/v2.rs) correctly excludes pre-commit-only activity from the "already signed" guard (`has_signed_block_in_tenure`), but the elapsed-time fallback it uses is fed by `get_last_activity_time`, which `check_latest_block_in_tenure` refreshes via `update_last_activity_time` on *any* same-height conflicting proposal that collides with a still-fresh pre-commit — not just legitimate re-tries. A winning miner who never lets a block cross the 70% signature threshold can keep proposing same-height siblings faster than `tenure_last_block_proposal_timeout`/`block_proposal_timeout`, perpetually refreshing this timestamp and preventing `is_timed_out` from ever returning `true`.

### Finding Description
`SortitionState::is_timed_out` (`stacks-signer/src/chainstate/v1.rs:55-93`, `v2.rs:48-89`) first checks `has_signed_block_in_tenure`, which by design excludes pre-commit-only blocks so a stalled pre-commit round cannot suppress the timeout. If no signature exists, it falls back to comparing `elapsed = now - last_activity` against `block_proposal_timeout`, where `last_activity` comes from `SignerDb::get_last_activity_time`.

That activity timestamp is written by `SortitionData::check_latest_block_in_tenure` (`stacks-signer/src/chainstate/mod.rs:376-478`), which runs on every proposal arrival, validate-ok, and signing check (`check_block_against_signer_db_state`, `stacks-signer/src/v0/signer.rs:1803-1880`). Two branches call `update_last_activity_time`:
- the "reorg attempt against a signed tip" branch (lines 395-419), and
- the CARVE branch (lines 422-448): when there is no fresh *signed* tip but there is a fresh *pre-committed* block, and the incoming proposal's `chain_length <= info.block.header.chain_length`, activity is refreshed "but not rejected."

This is proven directly by the existing unit test `pre_committed_block_does_not_veto_replacement` (`stacks-signer/src/chainstate/tests/v2.rs:955-1017`), which shows a same-height "replacement" block passes the height check and `get_last_activity_time` becomes `Some(...)` purely from a fresh pre-commit conflict — with no signature ever placed.

The attacker (the sortition winner) exploits this legitimately-reachable code path: repeatedly gossip a new `BlockProposal` at the same chain height as their own most-recent pre-committed block (never letting one reach 70% signatures), spaced closer than `min(tenure_last_block_proposal_timeout, block_proposal_timeout)`. Each proposal is evaluated by `check_block_against_signer_db_state` → `check_latest_block_in_tenure`, hits the CARVE branch, and calls `update_last_activity_time`. `has_signed_block_in_tenure` stays `false` forever (no signature ever forms), but `last_activity` is refreshed on every round, so `elapsed` never exceeds `block_proposal_timeout` and `is_timed_out` never returns `true`. `check_miner_inactivity`/`capitulate_viewpoint` therefore never trigger the fallback to the honest prior tenure (`stacks-signer/src/v0/signer_state.rs:284-374`).

### Impact Explanation
This breaks the liveness guarantee described in `docs/signer-flows.md:466-468` and `481`: "no signed block, and inactive past `block_proposal_timeout`" is supposed to be a bounded condition that triggers fallback to the prior miner. Instead, an uncooperative sortition winner who never lets a proposal reach the 70% signature threshold can indefinitely prevent that fallback by continuously feeding same-height conflicting proposals, stalling new signed blocks for the tenure. This matches the "High" category: a signer set wedged into never signing valid blocks/never falling back, i.e., a liveness stall driven by a single uncooperative miner — no chain-safety violation, no signature misuse, just indefinite stall.

### Likelihood Explanation
The attacker needs only to win one sortition (their own BTC) and gossip `BlockProposal` messages at a rate faster than the configured `tenure_last_block_proposal_timeout`/`block_proposal_timeout` (defaults are on the order of 30-120s per `sample/conf/signer/mainnet-signer-conf.toml:61-82`). No signer collusion, no auth token, no local access, and no majority weight is required — this exactly matches the constrained attacker model (one miner slot + gossip). It is fully repeatable for as long as the attacker keeps up the proposal cadence.

### Recommendation
Do not treat a pre-commit-vs-pre-commit (or pre-commit-vs-signed) height conflict as unconditional proof of "miner activity" for the purposes of the inactivity clock that gates fallback to the prior tenure. Either: (a) bound the number of times/duration for which pre-commit-only conflicts can refresh `last_activity` within a single tenure (e.g., track first-activity time and cap total extension), or (b) require that at least one block in the tenure reach the pre-commit *threshold* progression (height strictly increasing and never regressing) before counting a same-height resubmission as activity, or (c) fold this into `has_signed_block_in_tenure`'s semantics so that indefinite same-height churn without a signature is itself detected and forced through the timeout path.

### Proof of Concept
Rust test in `stacks-signer/src/chainstate/tests/v2.rs` (or `v1.rs`) modeled on `pre_committed_block_does_not_veto_replacement` and `check_sortition_timeout`:
1. Set `block_proposal_timeout = Duration::from_secs(2)`, `tenure_last_block_proposal_timeout = Duration::from_secs(5)`.
2. Insert burn block for `consensus_hash`. Insert a pre-committed block A at height H (never sign it).
3. Loop N rounds (N * step < some bound, each step < 2s): call `SortitionData::check_latest_block_in_tenure(tenure_id, &sibling_block_at_height_H, &mut signer_db, &client, ..., ...)` with a new sibling block each time (never marking it as pre-committed/signed) — this simulates the miner re-proposing.
4. After each round, assert `SortitionState::is_timed_out(&consensus_hash, &signer_db, block_proposal_timeout).unwrap() == false` even though more than `block_proposal_timeout` has elapsed since burn-block receipt and no block was ever signed (`has_signed_block_in_tenure(&consensus_hash).unwrap() == false`).
5. Assert that without the repeated resubmission (i.e., waiting `block_proposal_timeout` with no further proposals), `is_timed_out` returns `true`, proving the resubmission — not genuine miner progress — is what suppressed the timeout.

### Citations

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

**File:** stacks-signer/src/chainstate/tests/v2.rs (L994-1016)
```rust
    assert!(signer_db
        .get_last_activity_time(&tenure_id)
        .unwrap()
        .is_none());

    // The replacement passes the height check. (The stacks-node call inside fails since nothing
    // is listening, which makes the check fall back to assuming the proposal is higher; the
    // point here is that the pre-committed block does not early-reject it.)
    assert!(SortitionData::check_latest_block_in_tenure(
        &tenure_id,
        &replacement,
        &mut signer_db,
        &stacks_client,
        Duration::from_secs(30),
        Duration::from_secs(3),
    )
    .unwrap());

    // But conflicting with a fresh pre-commit still counts as miner activity.
    assert!(signer_db
        .get_last_activity_time(&tenure_id)
        .unwrap()
        .is_some());
```

**File:** stacks-signer/src/chainstate/v1.rs (L55-93)
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
```

**File:** stacks-signer/src/v0/signer.rs (L1842-1850)
```rust
        // Ensure that the block is the last block in the chain of its current tenure.
        match SortitionData::check_latest_block_in_tenure(
            &proposed_block.header.consensus_hash,
            proposed_block,
            &mut self.signer_db,
            stacks_client,
            self.proposal_config.tenure_last_block_proposal_timeout,
            self.proposal_config.reorg_attempts_activity_timeout,
        ) {
```
