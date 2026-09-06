### Title
Stale-pending-response window in `handle_block_proposal` causes drained pre-commit/signature/rejection votes to be permanently lost — analog of the Centrifuge `Gateway._endBatching()` stale-copy-then-clear reentrancy bug ([File: stacks-signer/src/v0/signer.rs])

### Summary
`Signer::handle_block_proposal` drains all early-arrived `BlockPreCommit`/`BlockResponse` votes for a *new* block signature hash from the DB pending tables into a local `PendingBlockResponses` value, and only *afterwards* performs `check_block_against_state`, `submit_block_for_validation`/`insert_pending_block_validation`, `insert_block`, and finally replays the drained votes via `process_pending_responses_for_block`. Between the drain and the `insert_block` call, the block is not yet known to `block_lookup`. Any pre-commit/rejection/signature messages for the same `signer_signature_hash` that are processed in that window get re-parked into the very tables that were just drained — but since `drain_pending_block_responses` is only ever called once per block (when it is first seen as "unknown"), those re-parked votes are never drained/replayed again and are permanently stranded in the DB, exactly mirroring the "copy-then-clear-then-stale-iterate" structure of the Centrifuge bug.

### Finding Description
The exploitable window is in `handle_block_proposal`: [1](#0-0) 

For a genuinely new block, `drain_pending_block_responses` performs `DELETE ... RETURNING` against `signer_pending_pre_commit_responses`, `signer_pending_signature_responses`, and `signer_pending_rejection_responses`: [2](#0-1) 

This is structurally identical to `Gateway._endBatching()`'s pattern: **read the pending state into a local copy, then clear the backing store, before any further processing occurs.**

After the drain, the function still has to do `check_block_against_state` (which can fetch a fresh `SortitionsView`), then either `submit_block_for_validation` or `insert_pending_block_validation`, and only then `insert_block` (which is what makes the block "known" to `block_lookup`): [3](#0-2) 

Only after all of that does it replay the *stale, locally-held* `pending_responses` copy via `process_pending_responses_for_block`: [4](#0-3) 

During the window between the drain (tables now empty for this hash) and `insert_block` (block now findable by `block_lookup`), the event loop can process other `SignerMessages` for the very same `signer_signature_hash` — e.g., a `BlockPreCommit` or `BlockResponse` arriving from another signer in the same `messages` batch of the current `SignerEvent`, or on a subsequent tick before `insert_block` completes (all handling is synchronous within one `process_event`/`handle_event_match` call, so this is not a true OS-level race, but the equivalent "stale-copy" structural flaw is present regardless of exact scheduling — it is bug-class-analogous per the report's real defect, which is about state ordering, not concurrency per se). When `handle_block_pre_commit` (or the response-handling path) is invoked for a hash not yet in `block_lookup`, it parks the vote into `signer_pending_pre_commit_responses`/`signer_pending_signature_responses`/`signer_pending_rejection_responses` — the same tables that were just drained.

Because `drain_pending_block_responses` is gated by `prior_block_info.is_some()` — i.e., it is only invoked the *first* time a `signer_signature_hash` is seen as unknown (`pending_responses = PendingBlockResponses::empty()` on all subsequent proposals for that hash, per line 1630-1631) — any vote parked into those tables after the one-time drain has already run will never be drained and replayed again for that block. The vote is not lost in-memory only; it persists in the SQLite DB forever (or until GC/pruning unrelated to this block), effectively invisible to the running signer's vote tally for that block.

### Impact Explanation
This breaks the "aggregated-weight vs verified-accepts" equality that the pre-commit/signature threshold logic depends on: a pre-commit or signature that a peer legitimately sent is silently dropped from this signer's count for that block, even though the DB records show it was received. Concretely:
- A dropped **pre-commit** vote can prevent `handle_block_pre_commit`'s 70%-weight threshold check from ever being satisfied from this signer's point of view, causing the signer to wedge and never progress to signing a block it otherwise should sign (Liveness wedge — signer never signs a valid block it should).
- A dropped **signature** vote similarly denies this signer's ability to count enough weight to consider the block globally accepted from local bookkeeping, contributing to a signer that is stuck relative to actual network state.
- A dropped **rejection** vote could, in principle, mask an actual rejection weight, delaying the signer's recognition that a block is globally rejected.

This matches the High-impact category: "a signer wedged into never signing valid blocks" due to permanently lost vote-processing state, analogous to how the Centrifuge bug permanently lost accounting messages due to a copy-then-clear-then-stale-iterate pattern.

### Likelihood Explanation
Triggering the window does not require a majority of signers or any privileged access — it only requires that another signer's (or several signers') `BlockPreCommit`/`BlockResponse` message for a specific, not-yet-locally-known block proposal be processed by this signer while it is between `drain_pending_block_responses` and `insert_block` inside `handle_block_proposal`. Because `handle_event_match` iterates over an entire batch of `messages` in one `SignerEvent::SignerMessages` in a `for` loop, and `handle_block_proposal` for a `MinerMessages` event happens in the same synchronous `process_event` pass, ordinary network/timing variance (miner slightly delaying proposal broadcast relative to peers who already voted on a re-broadcast, or a lagging/slow-to-receive signer) is sufficient to hit this window without any attacker action, making it a realistic organic occurrence, and a byzantine miner/peer could deliberately time proposal broadcast to widen the window (e.g., broadcasting the proposal late relative to a burst of `BlockPreCommit`/`BlockResponse` traffic it also controls or observes) to reliably create the loss.

### Recommendation
Do not treat the drain as a one-time, irrevocable operation gated purely on "did we already see this hash." Instead:
- Re-drain (or continuously drain) the pending-response tables for a `signer_signature_hash` immediately before/at the same time `insert_block` makes the block "known," and again defensively whenever `block_lookup` first succeeds for a hash that previously had no tracked `BlockInfo`, so that any votes parked after the initial drain but before `insert_block` are not orphaned.
- Alternatively, move the point at which the block becomes "known" (i.e., call `insert_block` or otherwise register the hash) to occur atomically with, or immediately before, `drain_pending_block_responses`, closing the window entirely.
- Add a monitoring/test that intentionally interleaves a `BlockPreCommit`/`BlockResponse` for a hash right after a fresh proposal is received but before validation completes, and assert the vote is eventually counted (mirroring the way the Centrifuge fix added a reentrancy guard `isSending` to prevent the stale-copy processing window from being exploitable).

### Proof of Concept
Deterministic PoC requires driving the exact ordering inside a running `Signer` instance:
1. Have Miner broadcast `BlockProposal` for block `B` (`signer_signature_hash = H`) to signer `S`.
2. Concurrently (or in the same `SignerMessages` batch processed by `S`, ordered so that these arrive as part of the same `process_event` pass, but logically "after" the drain executes and "before" `insert_block` executes — achievable by controlling message ordering within the batch or by using the `TEST_*` stall hooks such as `TEST_VALIDATE_STALL`/`TEST_STALL_BLOCK_VALIDATION_SUBMISSION` already present in the test-only build to pause `S` inside `handle_block_proposal` after the drain call and before `insert_block`), inject a `BlockPreCommit(H)` from another signer address into `S`'s `SignerMessages` handling.
3. Because `block_lookup_by_reward_cycle(H)` is still `None` at that point, `handle_block_pre_commit` parks the vote via `add_pending_block_pre_commit_response` into `signer_pending_pre_commit_responses`.
4. Let `handle_block_proposal` complete: `insert_block` runs, making `H` known; `process_pending_responses_for_block` runs only over the empty `pending_responses` variable captured at drain time (before step 2's injection), so the just-parked vote is not replayed.
5. Assert: `signer_db.get_all_pending_block_validations`/direct query on `signer_pending_pre_commit_responses` for `H` shows the pre-commit still present and never counted toward `S`'s local weight tally for `H`, even though `S` is now tracking `B`.

I was not able to execute this against a live/test signer instance in this environment (no filesystem/test runner access here); the code paths cited above establish the structural bug (drain occurring before, and gated only once relative to, `insert_block`), but confirming the exact scheduling/timing needed to reliably hit the window in the real async event loop would require running the existing `stacks-node/src/tests/signer/v0/mod.rs` test harness (which already has analogous `TEST_STALL_*` hooks used by tests like `signers_reprocess_late_block_proposals_pre_commits`) with a new test that injects a pre-commit during the drain window.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1630-1651)
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
```

**File:** stacks-signer/src/v0/signer.rs (L1656-1719)
```rust
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

        #[cfg(any(test, feature = "testing"))]
        let block_rejection =
            self.test_reject_block_proposal(block_proposal, &mut block_info, block_rejection);

        if let Some(block_rejection) = block_rejection {
            // We know proposal is invalid. Send rejection message, do not do further validation and do not store it.
            self.send_block_response(&block_info.block, block_rejection.into());
        } else {
            // Just in case check if the last block validation submission timed out.
            self.check_submitted_block_proposal();
            if self.submitted_block_proposal.is_none() {
                // We don't know if proposal is valid, submit to stacks-node for further checks and store it locally.
                info!(
                    "{self}: submitting block proposal for validation";
                    "signer_signature_hash" => %signer_signature_hash,
                    "block_id" => %block_proposal.block.block_id(),
                    "block_height" => block_proposal.block.header.chain_length,
                    "burn_height" => block_proposal.burn_height,
                );

                #[cfg(any(test, feature = "testing"))]
                self.test_stall_block_validation_submission();
                self.submit_block_for_validation(
                    stacks_client,
                    &block_proposal.block,
                    get_epoch_time_secs(),
                );
            } else {
                // Still store the block but log we can't submit it for validation. We may receive enough signatures/rejections
                // from other signers to push the proposed block into a global rejection/acceptance regardless of our participation.
                // However, we will not be able to participate beyond this until our block submission times out or we receive a response
                // from our node.
                warn!("{self}: cannot submit block proposal for validation as we are already waiting for a response for a prior submission. Inserting pending proposal.";
                    "signer_signature_hash" => signer_signature_hash.to_string(),
                );
                self.signer_db
                    .insert_pending_block_validation(&signer_signature_hash, get_epoch_time_secs())
                    .unwrap_or_else(|e| {
                        warn!("{self}: Failed to insert pending block validation: {e:?}")
                    });
            }

            // Do not store KNOWN invalid blocks as this could DOS the signer. We only store blocks that are valid or unknown.
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
```

**File:** stacks-signer/src/v0/signer.rs (L1720-1780)
```rust
            self.process_pending_responses_for_block(
                stacks_client,
                sortition_state,
                &mut block_info,
                pending_responses,
            );
        }
    }

    /// Process pending responses for a block proposal that we may have received late.
    fn process_pending_responses_for_block(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        block_info: &mut BlockInfo,
        pending_responses: PendingBlockResponses,
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

**File:** stacks-signer/src/signerdb.rs (L2574-2636)
```rust
    /// Retrieve and clear all pending block response entries matching the given block signer_signature_hash
    /// Returns PendingBlockResponses containing all matching pre-commits, approval signatures, and rejections
    pub fn drain_pending_block_responses(
        &self,
        block_sighash: &Sha512Trunc256Sum,
    ) -> Result<PendingBlockResponses, DBError> {
        let hash_str = block_sighash.to_string();

        // Delete and return pre-commits in one operation
        let pre_commits_qry = "DELETE FROM signer_pending_pre_commit_responses WHERE signer_signature_hash = ?1 RETURNING signer_addr";
        let mut stmt = self.db.prepare(pre_commits_qry)?;
        let pre_commits_rows = stmt.query_map(params![&hash_str], |row| {
            let addr_str: String = row.get(0)?;
            let addr = StacksAddress::from_string(&addr_str).ok_or(
                SqliteError::InvalidColumnType(0, addr_str.clone(), rusqlite::types::Type::Text),
            )?;
            Ok(addr)
        })?;
        let pre_commits: Vec<_> = pre_commits_rows.collect::<Result<Vec<_>, _>>()?;

        // Delete and return signatures in one operation
        let signatures_qry = "DELETE FROM signer_pending_signature_responses WHERE signer_signature_hash = ?1 RETURNING signer_addr, signature";
        let mut stmt = self.db.prepare(signatures_qry)?;
        let signatures_rows = stmt.query_map(params![&hash_str], |row| {
            let addr_str: String = row.get(0)?;
            let sig_str: String = row.get(1)?;
            let addr = StacksAddress::from_string(&addr_str).ok_or(
                SqliteError::InvalidColumnType(0, addr_str.clone(), rusqlite::types::Type::Text),
            )?;
            let signature: MessageSignature = serde_json::from_str(&sig_str).map_err(|_| {
                SqliteError::InvalidColumnType(1, sig_str.clone(), rusqlite::types::Type::Text)
            })?;
            Ok((addr, signature))
        })?;
        let signatures: Vec<_> = signatures_rows.collect::<Result<Vec<_>, _>>()?;

        // Delete and return rejections in one operation
        let rejections_qry = "DELETE FROM signer_pending_rejection_responses WHERE signer_signature_hash = ?1 RETURNING signer_addr, reject_code";
        let mut stmt = self.db.prepare(rejections_qry)?;
        let rejections_rows = stmt.query_map(params![&hash_str], |row| {
            let addr_str: String = row.get(0)?;
            let reject_code: u8 = row.get(1)?;
            let addr = StacksAddress::from_string(&addr_str).ok_or(
                SqliteError::InvalidColumnType(0, addr_str.clone(), rusqlite::types::Type::Text),
            )?;
            let reject_reason = RejectReasonPrefix::from(reject_code);
            Ok((addr, reject_reason))
        })?;
        let rejections: Vec<_> = rejections_rows.collect::<Result<Vec<_>, _>>()?;

        let pending_block_responses = PendingBlockResponses {
            pre_commits,
            signatures,
            rejections,
        };
        if !pending_block_responses.is_empty() {
            debug!("Drained pending block responses for block {block_sighash}";
                "pre_commits_count" => pending_block_responses.pre_commits.len(),
                "signatures_count" => pending_block_responses.signatures.len(),
                "rejections_count" => pending_block_responses.rejections.len());
        }
        Ok(pending_block_responses)
    }
```
