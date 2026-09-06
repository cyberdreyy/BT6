### Title
A single miner can indefinitely refresh tenure "activity" with unsigned, unresolved block proposals, wedging the signer's inactivity fallback - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`check_latest_block_in_tenure` refreshes a tenure's liveness timer (`update_last_activity_time`) whenever it sees a competing/duplicate proposal that merely matches a *locally pre-committed* block's height — a pre-commit state any signer reaches unilaterally after node validation, with no signer-set quorum required. Because a miner can keep generating new proposal hashes (e.g. by bumping the timestamp) at or below that pre-committed height, they can perpetually push `last_activity_time` forward and thereby prevent `SortitionState::is_timed_out` from ever returning `true`, blocking the state machine's fallback to the prior/next miner.

### Finding Description
`SortitionState::is_timed_out` is designed to let signers safely detect an inactive miner and fall back to the prior tenure, but it deliberately treats a locally pre-committed (unsigned) block as *not* proof of liveness for the purposes of `has_signed_block_in_tenure`: [1](#0-0) 

Instead, liveness is judged by `last_activity_time` (falling back to burn-block received time): [2](#0-1) 

However, `check_latest_block_in_tenure`, which runs for *every new proposal* (proposal arrival, validate-ok, and pre-commit re-evaluation via `check_block_against_signer_db_state`), explicitly updates `last_activity_time` for a proposal that merely conflicts with a *fresh pre-commit* — i.e. no group signature or threshold is required, just this one signer's own earlier pre-commit: [3](#0-2) 

The comment on the function even states the rejection here is "supersedable" and must not veto — this is the CARVE/ACT branch documented in `docs/signer-flows.md` ("a pre-commit never vetoes ... update_last_activity_time"): [4](#0-3) 

Because a resend of the *exact same* already-tracked proposal is short-circuited earlier (it is re-evaluated via `should_reevaluate_block`/`handle_block_pre_commit` and never reaches `check_latest_block_in_tenure` again): [5](#0-4) 

...a miner only needs to mutate the proposal (e.g. bump `timestamp`, as shown in the test harness itself) to obtain a fresh `signer_signature_hash`. That fresh proposal is treated as new (`block_proposal.clone()` → `BlockInfo::from`), flows through `check_block_against_state` → `check_latest_block_in_tenure`, hits the CARVE branch again, and refreshes `update_last_activity_time` — again, again, indefinitely — without ever needing the pre-commit or signature threshold to be met by anyone else: [6](#0-5) 

The signerdb write itself is a trivial upsert: [7](#0-6) 

The project's own test (`pre_committed_block_does_not_veto_replacement`) directly demonstrates the mechanics: a competing proposal at the same height as a pre-committed (never-signed) block both (a) passes the height check and (b) marks the tenure as having fresh activity — precisely the two properties an attacker needs to repeat forever: [8](#0-7) 

This mirrors the referenced report's bug class: a cheap, unilaterally-triggerable action (sending "dust" — here, a slightly-mutated unsigned proposal) repeatedly resets a guard (`investedAssets() == 0` / `last_activity_time`) that a state transition (`setStrategy` / miner-fallback) depends on, permanently blocking that transition as long as the griefer keeps acting.

### Impact Explanation
This breaks the liveness guard that `is_timed_out` / miner-inactivity fallback (section 8 of `docs/signer-flows.md`, `check_miner_inactivity` → `SortitionState::is_timed_out` → fallback to `make_miner_state(prior sortition)`) exists to provide. A miner that got only a minority of signers to pre-commit to a tenure-start block (never enough to reach the 70% pre-commit/signature threshold) can nonetheless keep every signer that did pre-commit permanently "seeing activity" for that tenure by resubmitting trivially mutated proposals within `tenure_last_block_proposal_timeout`. Those signers' local state machines never time out the stalled tenure and never fall back to the next miner, wedging block production — a High-severity liveness wedge ("a signer wedged into never signing valid blocks" / never advancing past a stalled tenure), reachable by a single miner (StackerDB writer) plus normal gossip, with no majority-signer cooperation and no node collusion required.

### Likelihood Explanation
Likelihood is high for any signer that pre-committed to the stalling block: the attack requires only a valid Stacks private key to produce syntactically valid block proposals (mutate `timestamp`, re-sign), a capability every miner already has, and no cooperation from other signers or the node beyond ordinary proposal submission/validation. The attacker does not need to win block production continuously — a single successful pre-commit-inducing proposal, followed by a stream of trivially-mutated re-proposals, sustains the wedge as long as desired.

### Recommendation
Do not refresh `last_activity_time` based solely on a locally pre-committed (unsigned) competing proposal reaching the height of an already pre-committed block; either exclude the CARVE branch's activity update entirely (a pre-commit never vetoes, but it should not perpetually count as fresh activity either), or bound the number of times a tenure's activity can be refreshed by non-signed/non-globally-observed proposals within a timeout window (e.g. require distinct genuinely new proposals rate-limited, or require the refresh to come from advancing chain state observed independently of the local signer's own pre-commit bookkeeping).

### Proof of Concept
1. Miner M proposes tenure-start block B at height H; some subset of signers validate it and reach `BlockState::PreCommitted` locally (no majority needed) — `mark_pre_committed` is called per-signer after `check_block_against_signer_db_state` passes (`stacks-signer/src/v0/signer.rs:1946-1970`), never reaching the 70% pre-commit threshold to actually sign.
2. M crafts B' = B with `timestamp += 1` (new `signer_signature_hash`, still height H) and broadcasts it via StackerDB.
3. Each pre-committed signer treats B' as a new proposal (`handle_block_proposal` → `check_block_against_state` → `confirms_latest_block_in_same_tenure` → `check_latest_block_in_tenure`), hits the CARVE branch (`stacks-signer/src/chainstate/mod.rs:427-447`), and calls `update_last_activity_time` for the tenure.
4. M repeats step 2 with B'', B''', ... at an interval less than `tenure_last_block_proposal_timeout`/`timeout` used in `SortitionState::is_timed_out`.
5. `is_timed_out` (`stacks-signer/src/chainstate/v2.rs:48-89`) never observes `elapsed > timeout` for this tenure at these signers, so the state machine never falls back to the prior miner, wedging tenure progression indefinitely while M's tenure-start block never actually gets signed.

### Citations

**File:** stacks-signer/src/chainstate/v2.rs (L54-66)
```rust
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
```

**File:** stacks-signer/src/chainstate/v2.rs (L67-88)
```rust
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

**File:** docs/signer-flows.md (L407-411)
```markdown
    CLB --> LSB{"fresh SIGNED tip in that tenure?<br/>get_tenure_last_block_info =<br/>get_last_signed_block + freshness from<br/>the last signature time<br/>(tenure_last_block_proposal_timeout)"}
    LSB -- "yes, and proposal not higher" --> RA["fails the check<br/>(a reorg attempt within<br/>reorg_attempts_activity_timeout still<br/>counts as miner activity:<br/>update_last_activity_time)"]:::bad
    LSB -- "no signed tip, or proposal higher" --> CARVE{"fresh PRE-COMMITTED block<br/>at ≥ this height?<br/>get_last_accepted_block"}
    CARVE -- yes --> ACT["count miner activity only —<br/>a pre-commit never vetoes<br/>update_last_activity_time"]
    CARVE -- no --> NODE
```

**File:** stacks-signer/src/v0/signer.rs (L1505-1529)
```rust
        if !should_reevaluate_reject_reason(block_info) {
            if block_info.state == BlockState::PreCommitted {
                // We validated this block but haven't signed it. Signing requires the
                // pre-commit threshold and the conflict checks in `handle_block_pre_commit`.
                // Re-broadcast our pre-commit and re-run that evaluation instead of
                // responding with a signature directly, so a re-proposed block can't
                // bypass those checks.
                info!(
                    "{self}: received a block proposal for a block we have pre-committed to but not signed. Re-evaluating the pre-commit.";
                    "signer_signature_hash" => %signer_signature_hash,
                    "block_id" => %block_info.block.block_id(),
                    "block_height" => block_info.block.header.chain_length,
                    "burn_height" => block_proposal.burn_height,
                    "consensus_hash" => %block_info.block.header.consensus_hash
                );
                self.send_block_pre_commit(signer_signature_hash.clone());
                let address = self.stacks_address.clone();
                self.handle_block_pre_commit(
                    stacks_client,
                    sortition_state,
                    &address,
                    &signer_signature_hash,
                );
                return false;
            }
```

**File:** stacks-signer/src/v0/signer.rs (L1630-1672)
```rust
        let pending_responses = if prior_block_info.is_some() {
            PendingBlockResponses::empty()
        } else {
            info!(
                "{self}: received a block proposal for a new block.";
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_proposal.block.block_id(),
                "block_height" => block_proposal.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "consensus_hash" => %block_proposal.block.header.consensus_hash,
            );
            self.signer_db
                .drain_pending_block_responses(&signer_signature_hash)
                .unwrap_or_else(|e| {
                    warn!(
                        "{self}: Failed to drain pending block responses for block proposal: {e:?}";
                        "signer_signature_hash" => %signer_signature_hash,
                        "block_id" => %block_proposal.block.block_id(),
                    );
                    PendingBlockResponses::empty()
                })
        };
        crate::monitoring::actions::increment_block_proposals_received();
        // Creating a new proposal will overwrite any prior proposal info on the block if it exists, e.g. validity, signed_timestamps, etc.
        let mut block_info = BlockInfo::from(block_proposal.clone());

        // Get sortition view if we don't have it
        if sortition_state.is_none() {
            *sortition_state =
                SortitionsView::fetch_view(self.proposal_config.clone(), stacks_client)
                    .inspect_err(|e| {
                        warn!(
                            "{self}: Failed to update sortition view: {e:?}";
                            "signer_signature_hash" => %signer_signature_hash,
                            "block_id" => %block_proposal.block.block_id(),
                        )
                    })
                    .ok();
        }

        // Check if proposal can be rejected now if not valid against sortition view
        let block_rejection =
            self.check_block_against_state(stacks_client, sortition_state, &block_info);
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

**File:** stacks-signer/src/chainstate/tests/v2.rs (L985-1017)
```rust

    // A replacement block at the same height.
    let mut replacement = block.clone();
    replacement.header.timestamp += 1;
    assert_ne!(
        replacement.header.signer_signature_hash(),
        block.header.signer_signature_hash()
    );

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
