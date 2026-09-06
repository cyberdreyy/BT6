## Title
Stale in-memory `BlockInfo` write-back in `process_pending_responses_for_block` can revert an in-loop signature/rejection decision — ([File: stacks-signer/src/v0/signer.rs])

## Summary
`process_pending_responses_for_block` reads a single `block_info` snapshot once and then replays pending pre-commits, rejections, and signatures against it in sequence. The pre-commit replay path (`handle_block_pre_commit`) fetches and writes its *own* fresh copy of `BlockInfo` from the DB — independently of the `block_info` reference held by the caller — while the subsequent rejection/signature replay steps (`store_and_process_block_rejection`, `store_and_process_block_signature`) keep mutating and persisting the caller's now-stale copy. This is the same "borrowed value can go dangling when the owning state changes underneath it" bug class as the PyO3 weak-reference issue: a read snapshot is treated as authoritative and written back after the canonical (DB) state has already moved on.

## Finding Description
In `stacks-signer/src/v0/signer.rs`, `handle_block_proposal` builds a fresh `block_info` local variable, inserts it into `signer_db`, and calls: [1](#0-0) 

`process_pending_responses_for_block` then iterates over `pending_responses` in this fixed order — pre-commits, then rejections, then signatures — all operating nominally on the same `&mut block_info`: [2](#0-1) 

For each pending pre-commit, it calls `self.handle_block_pre_commit(...)`, which does **not** take `block_info` as a parameter. Instead it independently re-fetches its own local copy from the DB via `block_lookup_by_reward_cycle`, mutates that local copy (potentially calling `mark_locally_accepted` and `insert_block`, i.e. actually signing the block if enough replayed pre-commits cross the ≥70% threshold), and persists it: [3](#0-2) [4](#0-3) 

After that pre-commit loop finishes, the *same* function proceeds to process pending rejections and signatures using the **original** `block_info` reference captured before any pre-commit replay ran — it was never refreshed from the DB after `handle_block_pre_commit` potentially advanced the block's state (e.g. to `LocallyAccepted`/`signed_self = Some(...)`). `store_and_process_block_rejection` and `store_and_process_block_signature` both unconditionally write this stale `block_info` back to the DB via `self.signer_db.insert_block(block_info)`: [5](#0-4) [6](#0-5) 

This is a lost-update / TOCTOU pattern: `insert_block` unconditionally serializes whatever in-memory `BlockInfo` is handed to it and overwrites the DB row keyed by `(reward_cycle, signer_signature_hash)`, with no optimistic-concurrency check against the row that `handle_block_pre_commit` may have just written: [7](#0-6) 

If, within one replay pass, the pre-commit loop causes the signer to actually sign the block (`signed_self` set, state moved to `LocallyAccepted`), and the rejection loop that runs immediately after processes a pending rejection against the stale (unsigned) copy and it happens to cross the global-rejection threshold, `store_and_process_block_rejection` calls `block_info.mark_globally_rejected()` on the stale copy and overwrites the DB row — clobbering the `signed_self` timestamp and the `LocallyAccepted` state that had just been durably recorded by the pre-commit path. `BlockInfo::check_state` does not prevent this because the *in-memory* state being transitioned is `Unprocessed`/`PreCommitted` (the stale snapshot), not the DB's true, newer state: [8](#0-7) 

## Impact Explanation
This breaks the "signed vs validated" / "one-per-height decision" equality: the durable, self-consistent decision recorded by the pre-commit-threshold path can be silently overwritten in-memory by a stale snapshot from the same event-processing pass, producing a signer_db record that contradicts what was actually signed and broadcast a few statements earlier in the same function call. Depending on which path wins the final `insert_block` call, the signer's persisted view of the block's state (and hence its subsequent re-evaluation behavior on process restart or reproposal, per `should_reevaluate_block`) can diverge from the signature it already emitted onto the wire, which is exactly the "signed vs validated" equality the design docs (`docs/signer-flows.md`) go to great lengths to protect elsewhere. This falls in the High-impact bucket described in scope ("a signer wedged... or losing the equivocation guard"): a corrupted local record can cause the signer to re-decide inconsistently with a signature it has already broadcast.

## Likelihood Explanation
Triggering this requires only a single miner plus normal gossip timing that this codebase explicitly anticipates and tests for elsewhere (see the "early votes" mechanism and reproposal handling documented in `docs/signer-flows.md` lines 196-198, and the sibling-race tests in `stacks-signer/src/v0/tests.rs`): a pending pre-commit and a pending rejection/signature for the *same* block arriving before the proposal itself is known, so they are queued (`add_pending_block_pre_commit_response`, `add_pending_block_rejection_response`/`add_pending_block_signature_response`) and replayed together the moment the proposal lands. No majority of signers or privileged access is needed — an attacker (or just adversarial network timing controlled by one miner racing message delivery) only needs to get one pre-commit and one rejection/signature for the same block queued before the proposal arrives to force this exact interleaving in `process_pending_responses_for_block`.

## Recommendation
Have `process_pending_responses_for_block`'s three replay loops operate on a single, continuously-refreshed source of truth rather than a private, stale snapshot: either (a) pass `block_info` by reference into `handle_block_pre_commit` (removing its independent internal `block_lookup_by_reward_cycle` fetch) so all three loops mutate the same in-memory struct and only one final `insert_block` occurs, or (b) re-fetch `block_info` from `signer_db` at the start of the rejection and signature loops (and between each iteration) so writes are always based on the latest persisted state, mirroring the "re-check chainstate before signing" discipline already used in `handle_block_pre_commit` and `handle_block_validate_ok`.

## Proof of Concept
1. Miner mines block `B`. Before the `BlockProposal` for `B` reaches signer `S`, `S` receives (via gossip) both a `BlockPreCommit` from enough weight to reach the 70% pre-commit threshold and a conflicting `BlockRejection`/`BlockResponse::Rejected` for `B` from enough weight to reach the reject threshold (both plausible with staggered peer delivery timing).
2. Both are stored via `add_pending_block_pre_commit_response` / `add_pending_block_rejection_response` since `block_lookup_by_reward_cycle` returns `None` at that point: [9](#0-8) 
3. The `BlockProposal` for `B` arrives; `handle_block_proposal` creates `block_info`, inserts it, and calls `process_pending_responses_for_block(&mut block_info, pending_responses)`.
4. In the pre-commit loop, `handle_block_pre_commit` re-fetches its own copy from the DB, finds the pre-commit threshold met, calls `mark_locally_accepted`, and persists `signed_self = Some(t)`, state `LocallyAccepted` — and broadcasts the signature.
5. In the very next loop (rejections), `store_and_process_block_rejection` runs on the caller's original stale `block_info` (state `Unprocessed`), finds the rejection threshold met, calls `mark_globally_rejected()`, and calls `self.signer_db.insert_block(block_info)`, overwriting the row that step 4 just wrote — the persisted record for `B` now shows `GloballyRejected` with no `signed_self`, even though `S` already broadcast a signature over `B` moments earlier.

I could not fully trace whether a concrete integration test in `stacks-node/src/tests/signer/v0/` already exercises this exact ordering (pre-commit-then-reject replay within one `process_pending_responses_for_block` call) to confirm the interleaving is reachable in practice versus merely reachable in the code path as written; a Devin session with full repo/test access would be needed to run or add a targeted unit test to confirm the clobbering behavior end-to-end.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1250-1345)
```rust
    /// Handle pre-commit message from another signer
    fn handle_block_pre_commit(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        stacker_address: &StacksAddress,
        block_hash: &Sha512Trunc256Sum,
    ) {
        let Some(mut block_info) = self.block_lookup_by_reward_cycle(block_hash) else {
            // A pre-commit for a block we have not seen proposed yet means the proposal
            // has not reached us. Log it at INFO: it is a direct signal that our view of
            // the proposal stream is behind the rest of the signer set.
            info!("{self}: Received block pre-commit for an unknown block, storing as pending";
                "signer_address" => %stacker_address,
                "signer_signature_hash" => %block_hash,
                "signer_weight" => self.signer_weights.get(stacker_address).copied().unwrap_or(0),
            );
            if let Err(e) = self
                .signer_db
                .add_pending_block_pre_commit_response(block_hash, stacker_address)
            {
                warn!("{self}: Failed to save pending block pre-commit response: {e:?}");
            }
            return;
        };
        // Always save the pre-commit - we will need to store signer responses for determining which
        // are misbehaving, offline, etc.
        // commit message is from a valid sender! store it
        self.signer_db
            .add_block_pre_commit(block_hash, stacker_address)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block pre-commit"));

        let block_hash = block_info.block.header.signer_signature_hash();
        // do we have enough pre-commits to reach consensus?
        // i.e. is the threshold reached?
        //
        // Tally this up front, before the early returns below, so that every pre-commit we
        // receive can be logged with the running weight. Crossing this threshold is what
        // triggers our block response, so without it the wait for the threshold, which can
        // be minutes and is the bulk of a stalled block's latency, leaves no trace at all.
        let committers = self
            .signer_db
            .get_block_pre_committers(&block_hash)
            .unwrap_or_else(|_| panic!("{self}: Failed to load block commits"));

        let commit_weight = self.compute_signature_signing_weight(committers.iter());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });

        info!("{self}: Received block pre-commit";
            "signer_address" => %stacker_address,
            "signer_signature_hash" => %block_hash,
            "consensus_hash" => %block_info.block.header.consensus_hash,
            "block_height" => block_info.block.header.chain_length,
            "signer_weight" => self.signer_weights.get(stacker_address).copied().unwrap_or(0),
            "pre_commit_weight" => commit_weight,
            "pre_commit_weight_required" => min_weight,
            "total_weight" => total_weight,
            "pre_commit_threshold_reached" => commit_weight >= min_weight,
            "already_signed" => block_info.signed_self.is_some(),
        );

        if block_info.signed_self.is_some() {
            debug!(
                "{self}: Received pre-commit for a block that we have already signed. Doing nothing...",
            );
            return;
        }

        if !block_info.valid.unwrap_or(false) {
            // We received a pre-commit for a block that we have not validated or we have already marked this block as invalid.
            // We should not do anything further as we do not know what our response should be and we do not change our votes on rejected
            // blocks unless we receive a new block proposal for it and the reject reason allows us to reconsider.
            debug!(
                "{self}: Received a pre-commit for a block that we have not determined to be valid: {:?}. Doing nothing...", block_info.valid
            );
            return;
        }

        if min_weight > commit_weight {
            debug!(
                "{self}: Not enough pre-committed to block {block_hash} (have {commit_weight}, need at least {min_weight}/{total_weight})"
            );
            return;
        }

        // The chain and signer db state may have changed materially since this block passed the
        // proposal-time checks (e.g. between validation and reaching the pre-commit threshold we
        // may have signed a block that this one would reorg). Re-run the chainstate checks
        // before putting a signature over the block, and respond with a rejection if they no
        // longer pass, just as the block validation response handler does.
        if let Some(block_rejection) =
```

**File:** stacks-signer/src/v0/signer.rs (L1466-1474)
```rust
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
        if let Err(e) = block_info.mark_locally_accepted(false) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}",);
            }
        }
        self.signer_db
            .insert_block(&block_info)
            .unwrap_or_else(|e| self.handle_insert_block_error(e));
```

**File:** stacks-signer/src/v0/signer.rs (L1720-1725)
```rust
            self.process_pending_responses_for_block(
                stacks_client,
                sortition_state,
                &mut block_info,
                pending_responses,
            );
```

**File:** stacks-signer/src/v0/signer.rs (L1736-1780)
```rust
    ) {
        let signer_signature_hash = block_info.block.header.signer_signature_hash();
        for stacker_address in pending_responses.pre_commits {
            debug!("{self}: Processing pending pre-commit.";
                "stacker_address" => %stacker_address,
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_info.block.block_id(),
            );
            self.handle_block_pre_commit(
                stacks_client,
                sortition_state,
                &stacker_address,
                &signer_signature_hash,
            );
        }
        for (stacker_address, reject_reason) in pending_responses.rejections {
            debug!("{self}: Processing pending rejection.";
                "stacker_address" => %stacker_address,
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_info.block.block_id(),
                "reject_reason" => ?reject_reason,
            );
            self.store_and_process_block_rejection(
                sortition_state,
                block_info,
                &stacker_address,
                reject_reason,
            );
        }
        let block_id = block_info.block.block_id();
        for (stackers_address, signature) in pending_responses.signatures {
            debug!("{self}: Processing pending signature.";
                "stacker_address" => %stackers_address,
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_id,
            );
            self.store_and_process_block_signature(
                stacks_client,
                sortition_state,
                block_info,
                &stackers_address,
                &signature,
            );
        }
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2335-2341)
```rust
        if let Err(e) = block_info.mark_globally_rejected() {
            warn!("{self}: Failed to mark block as globally rejected: {e:?}",);
        }
        if let Err(e) = self.signer_db.insert_block(block_info) {
            error!("{self}: Failed to update block state: {e:?}",);
            panic!("{self} Failed to update block state: {e}");
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2528-2536)
```rust
        if let Err(e) = block_info.mark_locally_accepted(true) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}");
            }
        }
        let _ = self.signer_db.insert_block(block_info).map_err(|e| {
            warn!("Failed to set group threshold signature timestamp for {block_hash}: {e:?}");
            panic!("{self} Failed to write block to signerdb: {e}");
        });
```

**File:** stacks-signer/src/signerdb.rs (L313-329)
```rust
    /// Check if the block state transition is valid
    fn check_state(&self, state: BlockState) -> bool {
        let prev_state = &self.state;
        if *prev_state == state {
            return true;
        }
        match state {
            BlockState::Unprocessed => false,
            BlockState::LocallyAccepted | BlockState::LocallyRejected => !matches!(
                prev_state,
                BlockState::GloballyRejected | BlockState::GloballyAccepted
            ),
            BlockState::GloballyAccepted => !matches!(prev_state, BlockState::GloballyRejected),
            BlockState::GloballyRejected => !matches!(prev_state, BlockState::GloballyAccepted),
            BlockState::PreCommitted => matches!(prev_state, BlockState::Unprocessed),
        }
    }
```

**File:** stacks-signer/src/signerdb.rs (L391-401)
```rust
static CREATE_BLOCKS_TABLE_1: &str = "
CREATE TABLE IF NOT EXISTS blocks (
    reward_cycle INTEGER NOT NULL,
    signer_signature_hash TEXT NOT NULL,
    block_info TEXT NOT NULL,
    consensus_hash TEXT NOT NULL,
    signed_over INTEGER NOT NULL,
    stacks_height INTEGER NOT NULL,
    burn_block_height INTEGER NOT NULL,
    PRIMARY KEY (reward_cycle, signer_signature_hash)
) STRICT";
```
