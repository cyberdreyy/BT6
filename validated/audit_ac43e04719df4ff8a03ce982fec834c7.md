### Title
Unbounded activity-refresh via unsigned PreCommitted siblings lets a miner indefinitely block `check_miner_inactivity`'s fallback — (`stacks-signer/src/chainstate/mod.rs`)

### Summary
`check_latest_block_in_tenure`'s "fresh pre-commit" branch calls `signer_db.update_last_activity_time` for *any* conflicting/duplicate proposal that arrives while a prior proposal in the same tenure is only `PreCommitted` (never signed), and this call runs unconditionally at proposal arrival (inside `check_proposal`), independent of whether the new proposal is ultimately validated or accepted. Because `is_timed_out` in `chainstate/v1.rs`/`v2.rs` measures inactivity from `get_last_activity_time` once `has_signed_block_in_tenure` returns false, a miner can keep resetting that clock by repeatedly gossiping distinct competing proposals that never cross the 70% signing threshold, indefinitely defeating `check_miner_inactivity`'s fallback to the prior miner even though no block is ever signed.

### Finding Description
The relevant wedge: **"PreCommitted-only activity refresh with no signature ever produced" blocks the fallback path**.

Path:
1. A miner (attacker, one slot, own BTC) proposes a tenure-start block A. It passes node validation, and the victim signer's local `check_block_against_signer_db_state` marks it `PreCommitted` (`mark_pre_committed`, stamping `approved_time`) — this is a *local per-signer* decision requiring no signer-weight threshold at all, per docs section 4/5.
2. The attacker then gossips a second, distinct proposal B at the same/lower `chain_length` (different timestamp ⇒ different `signer_signature_hash`, same `consensus_hash`/parent). This proposal reaches `check_proposal` → `confirms_latest_block_in_same_tenure` → `check_latest_block_in_tenure`:
   - `get_tenure_last_block_info` finds no *signed* block (`get_last_signed_block` only considers signed blocks), so the first "signed tip" branch is skipped.
   - `get_last_accepted_block` finds A, still `PreCommitted` and within `tenure_last_block_proposal_timeout` (`is_fresh_pre_commit == true`). Because B's height `<=` A's height, the code logs "counting it as miner activity" and calls `signer_db.update_last_activity_time(&block.header.consensus_hash, now)` [1](#0-0) .
   - This call happens regardless of B's eventual fate — the function does not return here, it falls through to the node-tip comparison and may still reject B; the activity refresh already happened as a side effect [2](#0-1) .
3. `is_timed_out` (both `v1.rs` and `v2.rs`) explicitly excludes `PreCommitted` blocks from `has_signed_block_in_tenure` — this is the fix for the previously-known `precommit-suppresses-miner-timeout` bug — but it then falls back to `elapsed = now - last_activity_time` [3](#0-2) [4](#0-3) . Since `last_activity_time` was just refreshed in step 2, `elapsed` never exceeds `block_proposal_timeout`, so `is_timed_out` returns `false` on every check.
4. `check_miner_inactivity` short-circuits on `!is_timed_out` and never attempts the fallback to the prior sortition's miner [5](#0-4) .

The already-shipped fix (`precommit-suppresses-miner-timeout.fixed`, and the `has_signed_block_in_tenure` gate) closed the direct path where a pre-commit was treated as "signed." It did **not** close this second, independent path: the activity timer itself is refreshed by pre-commit conflicts, which achieves the same practical effect (never times out) through a different mechanism that the fix did not touch.

### Impact Explanation
This breaks the bounded-liveness guarantee that a tenure must eventually either produce a signed block or fall back to a prior miner. As long as the attacker (a single miner slot, no signer privileges, no majority weight) keeps injecting new competing/duplicate proposals faster than `block_proposal_timeout`, the honest signer set is wedged in the current tenure: it cannot sign (no proposal ever reaches 70% pre-commit/signature weight) and it cannot fall back (`check_miner_inactivity` never fires). This matches the High severity "signer wedged into never signing valid blocks (liveness)" category. The wedge is naturally bounded by tenure/burn-block cadence (a new sortition eventually starts a new tenure and a fresh state machine), so it is not literally infinite across all time, but it defeats the inactivity/fallback mechanism for the full duration of the current tenure, which is exactly the window that mechanism exists to protect.

### Likelihood Explanation
- Attacker cost: one won miner slot (their own BTC) plus the ability to craft and gossip `BlockProposal` messages — matches the allowed unprivileged attacker model.
- No majority signer weight or auth token needed: the initial `PreCommitted` mark is a *local, single-signer* decision after node validation of the attacker's own legitimately-mined block; subsequent conflicting proposals need not even be individually validated to trigger the refresh, since `update_last_activity_time` fires at `check_proposal` (proposal-arrival) time.
- Precondition: the natural 30%+ signer disagreement that already exists whenever a set of siblings splits (as covered by the repo's own `pre_commit_50_50_split_agrees_on_node_tip` scenario) is sufficient; no attacker-controlled second signer set is strictly required.
- Repeatable indefinitely within `tenure_last_block_proposal_timeout`/`block_proposal_timeout` windows, bounded only by burn-block cadence.

### Recommendation
Do not let `is_fresh_pre_commit`-triggered activity updates in `check_latest_block_in_tenure` feed the same `last_activity_time` counter that `is_timed_out` uses to gate the miner-inactivity fallback. Either: (a) track pre-commit-only activity in a separate timestamp that is *not* consulted by `is_timed_out`, or (b) require `is_timed_out` to independently confirm forward signing progress (e.g., increasing pre-commit weight, or a bound on how many times a "fresh pre-commit" refresh may occur without a corresponding weight increase) before treating pre-commit churn as evidence the miner is alive rather than stalling.

### Proof of Concept
Rust test plan (extends `stacks-signer/src/chainstate/tests/v2.rs` / `stacks-signer/src/v0/tests.rs` patterns already present, e.g. `pre_committed_block_does_not_veto_replacement` and `check_tenure_change_accepts_when_only_pre_committed_block_exists`):

```rust
#[test]
fn precommit_only_activity_refresh_blocks_inactivity_fallback() {
    // 1. setup_test_environment; mark block A PreCommitted in tenure T (approved_time = now).
    // 2. assert SortitionState::is_timed_out(..., block_proposal_timeout) == false initially.
    // 3. Loop N times with sleep < tenure_last_block_proposal_timeout each iteration:
    //    - build block B_i = tenure_start(..., timestamp = now + i) at same chain_length as A
    //    - call SortitionData::check_latest_block_in_tenure(&tenure_id, &B_i, &mut signer_db, &client, ...)
    //    - assert signer_db.get_last_activity_time(&tenure_id) advances to ~now each time
    // 4. After looping past what would have been block_proposal_timeout (no single gap exceeds it),
    //    assert has_signed_block_in_tenure(&tenure_id) == false (nothing ever signed)
    //    assert SortitionState::is_timed_out(&tenure_id, &signer_db, ..., block_proposal_timeout) == false
    // 5. Contrast: run the same loop but WITHOUT the periodic re-proposals (just wait past timeout)
    //    and assert is_timed_out == true, proving the re-proposals are what suppress the fallback.
}
```
Assertions on both sides of the equality: with periodic never-threshold-crossing re-proposals, `is_timed_out` stays `false` and `check_miner_inactivity` never calls `make_miner_state` for the prior sortition, despite `has_signed_block_in_tenure` correctly returning `false` throughout.

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

**File:** stacks-signer/src/chainstate/mod.rs (L450-478)
```rust
        let tip = match client.get_tenure_tip(tenure_id) {
            Ok(tip) => tip.anchored_header,
            Err(e) => {
                warn!(
                    "Failed to fetch the tenure tip for the parent tenure: {e:?}. Assuming proposal is higher than the parent tenure for now.";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "parent_tenure" => %tenure_id,
                );
                return Ok(true);
            }
        };
        if let Some(nakamoto_tip) = tip.as_stacks_nakamoto() {
            // If we have seen this block already, make sure its state is updated to globally accepted.
            // Otherwise, don't worry about it.
            if let Ok(Some(mut block_info)) =
                signer_db.block_lookup(&nakamoto_tip.signer_signature_hash())
            {
                if block_info.state != BlockState::GloballyAccepted {
                    if let Err(e) = block_info.mark_globally_accepted() {
                        warn!("Failed to mark block as globally accepted: {e}");
                    } else if let Err(e) = signer_db.insert_block(&block_info) {
                        warn!("Failed to update block info in db: {e}");
                    }
                }
            }
        }
        Ok(tip.height() < block.header.chain_length)
    }
```

**File:** stacks-signer/src/chainstate/v1.rs (L60-93)
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

**File:** stacks-signer/src/chainstate/v2.rs (L55-89)
```rust
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

**File:** stacks-signer/src/v0/signer_state.rs (L304-315)
```rust
        let is_timed_out = SortitionState::is_timed_out(
            &version,
            tenure_id,
            db,
            client.get_signer_address(),
            proposal_config,
            eval,
        )?;

        if !is_timed_out {
            return Ok(());
        }
```
