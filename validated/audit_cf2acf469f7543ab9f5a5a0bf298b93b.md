Confirmed: `check_tenure_change_confirms_parent` (`stacks-signer/src/chainstate/mod.rs:488-504`) calls `check_latest_block_in_tenure` using `&tenure_change.prev_tenure_consensus_hash` — a field read directly from the miner-supplied `TenureChangePayload` inside the block, not the signer's independently-derived `parent_tenure_id`. That self-referential trust is exactly the bug-class analog to the vLLM report: a security decision keyed off attacker-supplied data that a *different, earlier* check-point cross-validated against a trusted source, but which a *later* check-point (reached via a different code path) re-derives from the untrusted value alone, letting the two "URLs" disagree.

### Title
Signer re-validation of tenure-change blocks trusts the miner-declared parent tenure instead of the sortition-derived one, allowing a signature over a block with a forged parent - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`GlobalStateView::check_proposal` (v2) and `SortitionsView::check_proposal` (v1) each validate a tenure-change block's *declared* parent (`tenure_change.prev_tenure_consensus_hash`) against a **trusted** value — the signer's own state-machine `parent_tenure_id` (v2, `stacks-signer/src/chainstate/v2.rs:314-325`) or the locally-tracked canonical tip / `check_parent_tenure_choice` (v1, `stacks-signer/src/chainstate/v1.rs`) — before calling `check_tenure_change_confirms_parent`. But this cross-check runs **only at proposal arrival**, as the docs explicitly note: "the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the pox bitvec, and tenure-extend rules **before delegating here**" [1](#0-0) .

At the two later re-validation points — `handle_block_validate_ok` (post node-validation) and `handle_block_pre_commit` (pre-commit threshold) — the signer calls `check_block_against_signer_db_state`, which invokes `SortitionData::check_tenure_change_confirms_parent(tenure_change, proposed_block, …)` directly [2](#0-1) . That helper derives the tenure to check purely from the payload's own field:

```rust
pub fn check_tenure_change_confirms_parent(
    tenure_change: &TenureChangePayload,
    block: &NakamotoBlock,
    ...
) -> Result<bool, ClientError> {
    Self::check_latest_block_in_tenure(
        &tenure_change.prev_tenure_consensus_hash,
        block, ...
    )
}
``` [3](#0-2) 

It never compares `prev_tenure_consensus_hash` against the trusted `parent_tenure_id`/`current_miner_pkh` context established at proposal time.

### Finding Description
The equality that must hold end-to-end is: *the parent tenure a tenure-change block claims == the parent tenure the signer's sortition/state-machine view says the active miner actually won against*. That equality is enforced once, at proposal arrival, in `validate_tenure_change_payload`:

```rust
if &tenure_change.prev_tenure_consensus_hash != parent_tenure_id {
    ... return Err(RejectReason::InvalidParentBlock);
}
``` [4](#0-3) 

`parent_tenure_id` here comes from `self.signer_state.current_miner`'s `MinerState::ActiveMiner` — i.e., the signer's own sortition-derived state, unforgeable by the miner [5](#0-4) .

However, a proposal can be re-evaluated later without this cross-check ever running again:
- If a node validation response (`handle_block_validate_ok`) arrives after the signer's local sortition/miner-state view has moved on (new burn block, capitulated miner view, etc.), `check_block_against_signer_db_state` re-derives correctness from `tenure_change.prev_tenure_consensus_hash` alone, not from the now-current `parent_tenure_id` [6](#0-5) .
- The same happens at pre-commit-threshold re-check (`handle_block_pre_commit` → `check_block_against_signer_db_state`) [7](#0-6) .

Because `check_latest_block_in_tenure`'s only real guard is "is there already a signed/pre-committed tip in `prev_tenure_consensus_hash`'s tenure at or above this height" [8](#0-7) , a miner that names a *stale or wrong but currently-empty/low* tenure as `prev_tenure_consensus_hash` can pass this re-check even though the block does not actually confirm the tenure the signer's live state machine currently recognizes as the true parent. The identity check that would catch this ("does the declared parent match the state-machine's parent_tenure_id") is skipped on every path except the very first proposal evaluation.

### Impact Explanation
This breaks the "approved-parent vs canonical" equality: a signer can end up placing its post-validation pre-commit/signature on a tenure-change block whose declared parent tenure was never cross-checked against the signer's trusted view at the moment the signature is actually produced, only against a possibly-stale snapshot from proposal time. If state has moved between proposal and pre-commit/signing (a realistic window, since pre-commit waits for 70% weight and node validation round-trips), this is a path to a signer endorsing a tenure-change block built on a parent tenure inconsistent with the current canonical view — a non-canonical/invalid-parent block getting signed, which is a Critical-class outcome per the rules.

### Likelihood Explanation
Reachable by a single miner (one slot) simply by proposing a tenure-change block whose `prev_tenure_consensus_hash` was valid relative to the signer's state at proposal time, then relying on the normal validation/pre-commit latency window (node round-trip + 70%-threshold wait) during which the signer's local miner-state view can change (burn block arrival, capitulation, etc.) without the parent-tenure identity re-check ever re-running. No majority collusion or key compromise is required — only timing relative to routine signer housekeeping.

### Recommendation
Re-run the `tenure_change.prev_tenure_consensus_hash == parent_tenure_id` (or v1's equivalent `check_parent_tenure_choice`) comparison inside `check_block_against_signer_db_state`, not just once at proposal arrival, using the signer's *current* trusted parent-tenure view at validate-ok and pre-commit time rather than trusting the payload's self-reported field in isolation.

### Proof of Concept
Conceptual (cannot be executed without a live multi-signer testnet, which is out of scope for static analysis):
1. Miner sends a tenure-change proposal with `prev_tenure_consensus_hash = T_valid`, matching the signer's `parent_tenure_id` at that instant; proposal-time check passes and the signer submits it for node validation.
2. Before validation returns, a new burn block arrives and the signer's state machine capitulates to recognize a different current tenure/parent (`parent_tenure_id` changes), while `T_valid` is now stale/incorrect relative to the live view.
3. `handle_block_validate_ok` fires; `check_block_against_signer_db_state` re-checks only via `check_tenure_change_confirms_parent(tenure_change, block, …)`, which is keyed to `tenure_change.prev_tenure_consensus_hash = T_valid` and finds no signed conflicting tip there, so it passes — without ever re-comparing `T_valid` to the now-current `parent_tenure_id`.
4. The block proceeds to pre-commit and signature despite no longer matching the signer's live trusted parent-tenure view.

Note: I could not fully trace whether `handle_pending_update`/state-machine capitulation timing realistically overlaps with the validate-ok/pre-commit window in existing integration tests, so the exact race window size is unverified from static reading alone; this would need runtime/timing verification in a Devin session with the full test harness.

### Citations

**File:** docs/signer-flows.md (L425-433)
```markdown
Two things belong to the proposal path only and are **not** re-run at validate-ok
or at signing:

- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
- the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the
  pox bitvec, and tenure-extend rules before delegating here.
```

**File:** stacks-signer/src/v0/signer.rs (L1345-1366)
```rust
        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
        {
            warn!(
                "{self}: Reached the pre-commit threshold for a block, but it no longer passes the chainstate checks. Rejecting.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "reject_code" => %block_rejection.reason_code,
                "reject_reason" => &block_rejection.reason,
            );
            if let Err(e) = block_info.mark_locally_rejected() {
                if !block_info.has_reached_consensus() {
                    warn!("{self}: Failed to mark block as locally rejected: {e:?}");
                }
            };
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.handle_block_rejection(&block_rejection, sortition_state);
            self.send_block_response(&block_info.block, block_rejection.into());
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1810-1826)
```rust
        if let Some(tenure_change) = proposed_block.get_tenure_change_tx_payload() {
            // Ensure that the tenure change block confirms the expected parent block
            match SortitionData::check_tenure_change_confirms_parent(
                tenure_change,
                proposed_block,
                &mut self.signer_db,
                stacks_client,
                self.proposal_config.tenure_last_block_proposal_timeout,
                self.proposal_config.reorg_attempts_activity_timeout,
            ) {
                Ok(true) => return None,
                Ok(false) => {
                    return Some(self.create_block_rejection(
                        RejectReason::SortitionViewMismatch,
                        proposed_block,
                    ))
                }
```

**File:** stacks-signer/src/v0/signer.rs (L1946-1959)
```rust
        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
        {
            // The signer db state has changed. We no longer view this block as valid. Override the validation response.
            if let Err(e) = block_info.mark_locally_rejected() {
                if !block_info.has_reached_consensus() {
                    warn!("{self}: Failed to mark block as locally rejected: {e:?}");
                }
            };
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.handle_block_rejection(&block_rejection, sortition_state);
            self.send_block_response(&block_info.block, block_rejection.into());
```

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

**File:** stacks-signer/src/chainstate/mod.rs (L488-504)
```rust
    pub fn check_tenure_change_confirms_parent(
        tenure_change: &TenureChangePayload,
        block: &NakamotoBlock,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        tenure_last_block_proposal_timeout: Duration,
        reorg_attempts_activity_timeout: Duration,
    ) -> Result<bool, ClientError> {
        Self::check_latest_block_in_tenure(
            &tenure_change.prev_tenure_consensus_hash,
            block,
            signer_db,
            client,
            tenure_last_block_proposal_timeout,
            reorg_attempts_activity_timeout,
        )
    }
```

**File:** stacks-signer/src/chainstate/v2.rs (L119-145)
```rust
        let MinerState::ActiveMiner {
            current_miner_pkh,
            tenure_id,
            parent_tenure_id,
            ..
        } = &self.signer_state.current_miner
        else {
            info!(
                "No valid current miner. Considering invalid.";
                "block_height" => block.header.chain_length,
                "signer_signature_hash" => %block.header.signer_signature_hash()
            );
            return Err(RejectReason::InvalidMiner);
        };
        if &block.header.consensus_hash != tenure_id {
            info!("Miner block proposal consensus hash does not match the current miner's tenure id. Considering invalid.";
                "block_height" => block.header.chain_length,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
                "block_consensus_hash" => %block.header.consensus_hash,
                "active_miner_tenure_id" => %tenure_id,
                "active_miner_parent_tenure_id" => %parent_tenure_id,
            );
            return Err(RejectReason::ConsensusHashMismatch {
                actual: block.header.consensus_hash.clone(),
                expected: tenure_id.clone(),
            });
        }
```

**File:** stacks-signer/src/chainstate/v2.rs (L314-325)
```rust
        // Check that the tenure change's prev_tenure matches the signer's known parent tenure.
        // This catches block commits with bad parent_block_ptr (e.g., vtxindex=0 exploit).
        if &tenure_change.prev_tenure_consensus_hash != parent_tenure_id {
            warn!(
                "Block commit parent tenure mismatch: the block commit's parent_block_ptr does not correspond to the actual parent tenure";
                "committed_parent_tenure" => %parent_tenure_id,
                "actual_parent_tenure" => %tenure_change.prev_tenure_consensus_hash,
                "consensus_hash" => %block.header.consensus_hash,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
            );
            return Err(RejectReason::InvalidParentBlock);
        }
```
