### Title
Miner can indefinitely reset the tenure activity clock with never-signed pre-commit-only proposals, wedging tenure-extend liveness - (File: stacks-signer/src/chainstate/mod.rs)

### Summary
`check_latest_block_in_tenure` in `stacks-signer/src/chainstate/mod.rs` treats any block proposal that reaches only a **pre-commit** (no signature) as valid "miner activity" and resets the tenure's last-activity timer, even though the proposal never advances the chain (never gets 70%-signed, never becomes locally/globally accepted). A single miner (with the help of ordinary gossip, no majority-signer collusion needed) can therefore keep proposing blocks that reliably reach pre-commit but never get pushed past it, resetting `update_last_activity_time` on every cycle and permanently suppressing the idle-timeout condition that is supposed to make signers refuse further extension when no real progress is happening.

### Finding Description
`check_latest_block_in_tenure` (`stacks-signer/src/chainstate/mod.rs:376-478`) is the function used both at proposal time and at the pre-commit/signing re-check to decide whether a proposed block "confirms" enough of the tenure. Two branches inside it deliberately count *non-committing* signals as valid miner activity:

- Lines 403-417: if a proposal doesn't out-height the last **signed** block but that signed block's `signed_group` is not yet stale, the code still calls `signer_db.update_last_activity_time(...)`, explicitly commented as counting "this invalid block proposal towards valid miner activity."
- Lines 424-448: if the proposal conflicts only with a block the signer has **pre-committed** to (state `PreCommitted`, not yet signed) and that pre-commit is still fresh, the code again calls `update_last_activity_time`, explicitly noting "a block we have only pre-committed to must NOT veto this proposal, but ... this should still count as activity."

`PreCommitted` carries no signature at all — per `docs/signer-flows.md:132-134`, it just means "validated, willing to sign if the pre-commit threshold is met." Reaching `PreCommitted` requires only that the signer's own node accepts the block as statically/execution valid (`handle_block_validate_ok` → `mark_pre_committed`, `stacks-signer/src/v0/signer.rs:1888-1984`) — it does **not** require any other signer's cooperation, and a miner fully controls whether its own proposal is well-formed enough to pass local validation.

The equality this breaks: `update_last_activity_time` is meant to be a proxy for "the miner is doing real, forward work in this tenure" and feeds `tenure_idle_timeout` / `check_miner_inactivity`, which gates whether the tenure is considered live or should be timed out and superseded. By repeatedly emitting throwaway, non-conflicting-enough-to-sign proposals that only ever reach `PreCommitted`, a lone miner can keep resetting this clock indefinitely without ever producing a block that reaches the 70% *signature* threshold, i.e. without any consensus-visible progress. The activity signal becomes decoupled from actual chain progress.

### Impact Explanation
This matches the "High" bucket: a signer (indeed, the whole signer set observing the same activity-reset behavior) is effectively wedged from ever timing out an unproductive/malicious tenure and moving on (`tenure_idle_timeout` never elapses because `update_last_activity_time` keeps being called), which is a liveness wedge on the tenure-extend mechanism described in `docs/signer-flows.md:391-449`. Unlike genuine progress, this requires no signature threshold and no cooperation from any other signer or miner key — a single miner can trigger it purely by crafting proposals that validate locally but are designed to conflict with (and thus never override) a fresh pre-committed/signed sibling.

### Likelihood Explanation
The mechanism is directly reachable: a miner only needs to (1) know the current pre-committed/signed block at a given height in its own tenure, and (2) submit an alternate block proposal at the same or lower height that its own node validation still accepts (an easy bar — the same block engine that would accept the real block). No cross-signer collusion, no majority weight, and no auth token access is required — this is squarely a "one miner (plus gossip)" primitive. The two comments in the code ("Treat any attempt to reorg a locally accepted block as valid miner activity" and "Counting it as miner activity, but not rejecting the proposal") show this behavior is intentional design, which raises the bar for whether it is a "bug" versus tolerated design tension — but as coded, there is no bound on how many times a miner may do this, nor any check that the "activity" corresponds to a proposal capable of reaching full signature.

### Recommendation
Bound how much a single non-signed pre-commit-triggering proposal can extend the activity clock (e.g., require monotonically increasing distinct proposals, cap the number of activity resets per tenure that are not backed by an eventual signature, or only count activity resets from proposals that at least reach pre-commit threshold weight, not just local validation). Alternatively, decouple `tenure_idle_timeout` more strictly from `PreCommitted`-only states and require it to reset only on `mark_locally_accepted`/`mark_globally_accepted` transitions, which already carry real signature weight.

### Proof of Concept
1. Miner M starts a tenure and gets block `B1` at height `h` locally accepted/pre-committed by signer S (fresh `approved_time`).
2. M crafts block `B2` at the same height `h`, differing only in some inert field, ensuring the node's `/v3/block_proposal` endpoint still returns `Ok` (this only requires B2 be well-formed and executable — fully within M's control since M builds it).
3. S runs `check_latest_block_in_tenure` on `B2`: since `B2.chain_length <= B1.chain_length` and `B1`'s pre-commit/signed-group is still fresh, the branch at `stacks-signer/src/chainstate/mod.rs:403-417` or `:424-448` fires and calls `signer_db.update_last_activity_time`.
4. M repeats step 2-3 with `B3, B4, ...` before each preceding activity window elapses (well within `tenure_idle_timeout`).
5. The tenure's last-activity timestamp is perpetually refreshed even though no additional block ever crosses the 70% signature threshold, so `tenure_idle_timeout`/`check_miner_inactivity` (`stacks-signer/src/v0/signer_state.rs`, referenced in `docs/signer-flows.md:81-128`) never fires, wedging the mechanism meant to move past a stalled tenure. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** docs/signer-flows.md (L130-134)
```markdown
## 2. Block lifecycle (`BlockState`)

Every proposal tracked in the signer DB carries a `BlockState`. **`PreCommitted`
carries no signature**: it means "validated, willing to sign if the pre-commit
threshold is met." The first signature appears at `mark_locally_accepted`.
```

**File:** stacks-signer/src/v0/signer.rs (L1960-1984)
```rust
        } else {
            if let Err(e) = block_info.mark_pre_committed() {
                // The block may have reached enough signatures before we validated the block so should fail to mark pre-committed
                // but still call to make sure the timestamps and validity are updated correctly.
                if !block_info.has_reached_consensus()
                    && block_info.state != BlockState::LocallyAccepted
                {
                    warn!("{self}: Failed to mark block as approved: {e:?}",);
                    return;
                }
            }

            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.send_block_pre_commit(signer_signature_hash.clone());
            // have to save the signature _after_ the block info
            let address = self.stacks_address.clone();
            self.handle_block_pre_commit(
                stacks_client,
                sortition_state,
                &address,
                signer_signature_hash,
            );
        }
```
