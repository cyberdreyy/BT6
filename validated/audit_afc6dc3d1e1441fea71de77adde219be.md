### Title
Attacker-crafted proposals bypass the FIFO validation queue and starve an honest, already-queued proposal - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`handle_block_proposal` frees an expired validation slot via `check_submitted_block_proposal` and then immediately submits the *currently-arriving* proposal for validation instead of first draining the FIFO `block_validations_pending` table via `check_pending_block_validations`. An attacker who repeatedly submits fresh, never-answered proposals can perpetually re-claim the single validation slot the instant it frees, so an honest proposal parked earlier via `insert_pending_block_validation` is never dequeued for the whole tenure.

### Finding Description
The intended dequeue path is: a submission's response arrives (or times out) → `check_pending_block_validations` pops the oldest entry from `block_validations_pending` (ordered by `added_time ASC`, see `get_and_remove_pending_block_validation` in `stacks-signer/src/signerdb.rs:2056-2070`) → that oldest queued proposal is submitted next.

But `handle_block_proposal` has a second, competing path when a *new* proposal message arrives (`stacks-signer/src/v0/signer.rs:1670-1727`):
```rust
self.check_submitted_block_proposal();
if self.submitted_block_proposal.is_none() {
    self.submit_block_for_validation(stacks_client, &block_proposal.block, get_epoch_time_secs());
} else {
    self.signer_db.insert_pending_block_validation(&signer_signature_hash, get_epoch_time_secs());
}
```
`check_submitted_block_proposal` (`signer.rs:2116-2173`) frees `self.submitted_block_proposal` to `None` as a side effect when the prior submission has exceeded `block_proposal_validation_timeout` — it does not consult or drain `block_validations_pending`. Immediately afterward, the code checks `submitted_block_proposal.is_none()` and, if true, submits **the newly-arrived proposal itself**, not the oldest queued entry. `check_pending_block_validations` (the actual FIFO-respecting dequeue function, `signer.rs:2083-2112`) is only invoked from `handle_block_validate_response` after a real response arrives — it is never called from this "slot freed by timeout" branch of `handle_block_proposal`.

Exploit flow:
1. Attacker (single miner slot) submits proposal `A`. Signer submits `A` for validation, occupying the sole slot (`self.submitted_block_proposal = Some((A, now))`).
2. Honest miner's proposal `B` arrives while the slot is busy → queued via `insert_pending_block_validation(B, t1)` (FIFO position 1).
3. Attacker's node never answers the validation request for `A` (a network-timing choice fully within attacker control since they only need to delay their own proposal's validation response path from the node they control the timing of, or simply craft a proposal whose validation the node stalls on before responding — the timeout is client-observed, not authenticated).
4. Once `block_proposal_validation_timeout` elapses, attacker sends new proposal `C`. `handle_block_proposal(C)` calls `check_submitted_block_proposal()`, which frees the slot (rejects `A`) — then, in the same call, since the slot is now free, submits `C` directly, occupying the slot again, *ahead of* `B` which remains sitting in `block_validations_pending`.
5. Attacker repeats with `D`, `E`, ... each new proposal re-claims the freed slot the instant it frees, because the "new proposal" code path always wins the race against the FIFO queue drain, which only happens on a genuine validate response.
6. `B` is never popped by `check_pending_block_validations` because that function is only reached when a validate response actually arrives for the slot occupant — and the slot occupant is always the attacker's newest proposal, which never responds either.

The FIFO guarantee documented in `insert_pending_block_validation`/`get_and_remove_pending_block_validation` (ordered by `added_time`) is therefore not actually enforced against newly-arriving proposals competing for a freshly-freed slot; new arrivals have an unconditional priority path that bypasses the queue entirely.

### Impact Explanation
This is a liveness break matching the "High" category: a signer can be wedged into never validating/signing an honest miner's valid block for an entire tenure, purely through proposal timing by a single attacker-controlled miner slot. No signer majority, no compromised key, and no auth_token access is required — only the ability to broadcast crafted `BlockProposal`s (something any miner-slot winner can already do) combined with control over whether/when their own proposals' validation responses arrive. Because it targets a per-signer local slot/queue, this must be repeated against each signer independently but is otherwise repeatable indefinitely across an entire tenure with no cost beyond one won miner slot.

### Likelihood Explanation
Preconditions: attacker wins a miner slot (or otherwise controls the validation timing of their own submitted proposals) and simply issues one crafted proposal every `block_proposal_validation_timeout` interval (default 120s, config `block_proposal_validation_timeout_ms`). No majority, no privileged role, no local access, no auth_token needed — matches the allowed attacker model exactly. The only uncertainty is whether an attacker can reliably prevent their own proposal's validation response from arriving at each signer within the timeout window (e.g., by submitting many proposals in quick succession so each occupies the slot only transiently before natural node-side validation completes) — but the state machine's window for "slot freed" is entirely deterministic and attacker-triggerable by construction (submit a new proposal exactly once the timeout window has elapsed and never answer/allow the node to answer). This is feasible with pure protocol-level message crafting/gossip.

### Recommendation
In `handle_block_proposal`, after `check_submitted_block_proposal()` frees the slot, do not directly submit the newly-arrived proposal. Instead, always route the freed-slot handoff through `check_pending_block_validations` (or equivalent) so the oldest FIFO-queued entry (if any) is submitted first, and only submit the new proposal directly if the queue is empty. Concretely: when `submitted_block_proposal.is_none()` after the timeout check, first attempt `self.signer_db.get_and_remove_pending_block_validation()`; if `Some`, submit that block and enqueue the current proposal instead; only submit the current proposal immediately if the pending queue was empty.

### Proof of Concept
Rust test plan (in `stacks-signer/src/v0/signer.rs` or an integration test using `SignerTest`):
1. Set `block_proposal_validation_timeout` to a short duration (e.g. 1s) via `config.block_proposal_validation_timeout`.
2. Stall the node's `/v3/block_proposal` validation responses (as done in `block_validation_response_timeout` test, `stacks-node/src/tests/signer/v0/mod.rs:5491-5666`, using `TEST_VALIDATE_STALL`).
3. Submit attacker proposal `A` (occupies `submitted_block_proposal`).
4. Submit honest proposal `B` while `A` is stalled; assert `signer_db.has_pending_block_validation(&B_hash)` is `true` (queued, position 1).
5. Sleep past `block_proposal_validation_timeout`.
6. Submit attacker proposal `C`; assert that after this call `signer_db.has_pending_block_validation(&B_hash)` is *still* `true` and that `self.submitted_block_proposal` now equals `C`'s hash (not `B`'s) — this demonstrates the equality violation: FIFO order says `B` should be dequeued next, but `C` occupies the slot instead.
7. Repeat steps 5-6 with `D`, `E`, ... for N iterations (N > any reasonable liveness bound, e.g. 10 rounds), each time asserting `B` remains stuck in `block_validations_pending` and never gets promoted/validated.
8. Assert failure of liveness: no `BlockValidateOk`/`BlockValidateReject`/pre-commit/response is ever produced for `B`'s `signer_signature_hash` within the bounded tick count, proving the wedge (contrast with the expected fix behavior where `B` would be dequeued by round 2). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1681-1714)
```rust
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
```

**File:** stacks-signer/src/v0/signer.rs (L2082-2112)
```rust
    /// Check if we can submit a block validation, and do so if we have pending block proposals
    fn check_pending_block_validations(&mut self, stacks_client: &StacksClient) {
        // if we're already waiting on a submitted block proposal, we cannot submit yet.
        if self.submitted_block_proposal.is_some() {
            return;
        }

        let (signer_sig_hash, insert_ts) =
            match self.signer_db.get_and_remove_pending_block_validation() {
                Ok(Some(x)) => x,
                Ok(None) => {
                    return;
                }
                Err(e) => {
                    warn!("{self}: Failed to get pending block validation: {e:?}");
                    return;
                }
            };

        info!("{self}: Found a pending block validation: {signer_sig_hash:?}");
        match self.signer_db.block_lookup(&signer_sig_hash) {
            Ok(Some(block_info)) => {
                self.submit_block_for_validation(stacks_client, &block_info.block, insert_ts);
            }
            Ok(None) => {
                // This should never happen
                error!("{self}: Pending block validation not found in DB: {signer_sig_hash:?}");
            }
            Err(e) => error!("{self}: Failed to get block info: {e:?}"),
        }
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2114-2127)
```rust
    /// Check the current tracked submitted block proposal to see if it has timed out.
    /// Broadcasts a rejection and marks the block locally rejected if it has.
    fn check_submitted_block_proposal(&mut self) {
        let Some((proposal_signer_sighash, block_submission)) =
            self.submitted_block_proposal.take()
        else {
            // Nothing to check.
            return;
        };
        if block_submission.elapsed() < self.block_proposal_validation_timeout {
            // Not expired yet. Put it back!
            self.submitted_block_proposal = Some((proposal_signer_sighash, block_submission));
            return;
        }
```

**File:** stacks-signer/src/signerdb.rs (L2056-2069)
```rust
    pub fn get_and_remove_pending_block_validation(
        &self,
    ) -> Result<Option<(Sha512Trunc256Sum, u64)>, DBError> {
        let qry = "DELETE FROM block_validations_pending WHERE signer_signature_hash = (SELECT signer_signature_hash FROM block_validations_pending ORDER BY added_time ASC LIMIT 1) RETURNING signer_signature_hash, added_time";
        let args = params![];
        let mut stmt = self.db.prepare(qry)?;
        let result: Option<(String, i64)> = stmt
            .query_row(args, |row| Ok((row.get(0)?, row.get(1)?)))
            .optional()?;
        Ok(result.and_then(|(sighash, ts_i64)| {
            let signer_sighash = Sha512Trunc256Sum::from_hex(&sighash).ok()?;
            let ts = u64::try_from(ts_i64).ok()?;
            Some((signer_sighash, ts))
        }))
```
