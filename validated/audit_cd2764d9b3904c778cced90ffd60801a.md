### Title
Network-error (non-HTTP-status) failures during block-validation submission are silently dropped, leaving a signer permanently mute on that block — ([File: stacks-signer/src/v0/signer.rs])

### Summary
`Signer::submit_block_for_validation` in `stacks-signer/src/v0/signer.rs` handles three outcomes of `stacks_client.submit_block_for_validation`: success (`Ok`), an HTTP status failure (`Err(ClientError::RequestFailure(status))`, with special-case handling for `429`), and a catch-all `Err(e)` branch that only logs a warning and takes no further action.

### Finding Description
`ClientError` (`stacks-signer/src/client/mod.rs`) has variants beyond `RequestFailure`, most notably `ReqwestError(#[from] reqwest::Error)` and `RetryTimeout`, which are produced by `retry_with_exponential_backoff` when the underlying `reqwest` call itself fails at the transport layer (connection refused, DNS failure, TLS error, request timeout before any HTTP response is received), as opposed to a request that reaches the node and gets back a non-2xx status code. [1](#0-0) 

In `submit_block_for_validation`:
```
Ok(_)  => submitted_block_proposal = Some(...)              // tracked, will time out via check_submitted_block_proposal
Err(ClientError::RequestFailure(status)) if 429 => insert_pending_block_validation(...)   // retried later
Err(ClientError::RequestFailure(status)) else   => warn! only
Err(e)                                          => warn! only   // <- ReqwestError / RetryTimeout / etc.
``` [2](#0-1) 

In every branch except `Ok`, `self.submitted_block_proposal` is left as `None` and, except for the `429` case, nothing is inserted into the pending-validation table. This means:

1. `check_submitted_block_proposal` — the mechanism that eventually issues a `ConnectivityIssues` rejection when a submitted validation request goes unanswered — never fires, because it only acts when `submitted_block_proposal.is_some()`. [3](#0-2) 
2. `check_pending_block_validations`, which resubmits blocks recorded in the pending table, has nothing to resubmit for this block (it wasn't inserted), so it never retries this specific proposal either. [4](#0-3) 
3. The calling context (`handle_block_proposal`) already inserted the `block_info` into `signer_db` with `valid = None` before/around this call, and does nothing further to resolve it. [5](#0-4) 

The net effect: the signer never sends any `BlockResponse` (neither accept nor reject) for that block, and the record sits indefinitely in `signer_db` with an unresolved `valid: None` state. This is the direct structural analogue of the Eclair report: a transport-level failure (whose true outcome is unknown — the request may or may not have reached/been processed by the node) is treated neither as "definitely failed" (which would at least yield a safe rejection) nor as "still in flight" (which would keep it in the tracking/retry path) — it is simply discarded, and the signer takes no compensating action at all.

Unlike the deliberately-tested `block_proposal_validation_timeout` path (`check_submitted_block_proposal`), which *does* correctly fall back to a `ConnectivityIssues` rejection after a bounded wait so the signer isn't permanently stuck, the `Err(e)` (non-`RequestFailure`) branch of `submit_block_for_validation` has no equivalent fallback timer or rejection. If the signer's connection to its stacks-node is flaky or down at the moment a block is proposed (a very plausible real-world condition, and one entirely within a single sortition winner's/gossip's ability to trigger simply by being the proposer while the signer's own node connectivity is degraded), that signer silently drops out of the vote for that specific block, with no rejection message broadcast and no re-submission scheduled unless the exact same proposal is regossiped again while state is still `None`.

### Impact Explanation
This produces a per-block liveness gap for the affected signer: it neither signs nor rejects, and unlike the "successful submission then timeout" path, no automatic `ConnectivityIssues` rejection or requeue exists to bound how long the signer stays silent on that block. If a signer's stacks-node link is unstable across a run (not necessarily permanently down — intermittent packet loss/timeouts at exactly the retry-exhaustion point are sufficient), this signer can go silent on an indefinite series of proposals, effectively behaving like a signer "wedged into never signing valid blocks" for the affected proposals, since nothing in the code path re-triggers evaluation of blocks it failed to submit this way. This matches the High-impact category ("a signer wedged into never signing valid blocks"). It does not by itself break signing safety (no invalid/non-canonical block gets signed, and no rejection is miscounted as an acceptance), so it does not rise to Critical.

### Likelihood Explanation
This requires no coordination with other signers, the miner, or any special crafted proposal — a one-slot miner/gossip merely needs to deliver a normal `BlockProposal` while the signer's own connection to its configured stacks-node experiences a transport-level failure (timeout, connection reset, DNS blip) that exhausts the 5-second exponential backoff (`BACKOFF_MAX_ELAPSED`) without ever getting an HTTP status back. Given that `retry_with_exponential_backoff` gives up after only 5 seconds total, this is a plausible, naturally-occurring condition, not a contrived edge case, making the likelihood moderate-to-high in real deployments with imperfect node connectivity. [6](#0-5) 

### Recommendation
Treat the generic `Err(e)` branch of `submit_block_for_validation` (any `ClientError` other than a definitive HTTP-status rejection) the same way an eventual validation timeout is treated: either (a) insert the block into `pending_block_validation` so `check_pending_block_validations` retries it, or (b) immediately synthesize a `ConnectivityIssues` rejection (mirroring `check_submitted_block_proposal`'s behavior) so the signer's state is deterministically resolved and it does not sit silently in `valid: None` forever. Do not distinguish "we don't know if the request was sent" from "we know it failed" in a way that results in *no* action at all — always default to a safe, explicit resolution (retry-with-tracking or explicit reject), analogous to the fix recommended in the referenced Eclair advisory (never let an ambiguous outcome silently collapse into "do nothing").

### Proof of Concept
1. Configure a signer normally connected to its stacks-node.
2. Have the sortition winner (or gossip) deliver a `BlockProposal` to the signer at the moment the signer's HTTP client to its stacks-node experiences a transport-level failure (e.g., simulate by dropping/blackholing the TCP connection to the node's RPC port, or injecting a `reqwest` timeout) such that `submit_block_for_validation` in `stacks_client.rs` returns `Err(ClientError::ReqwestError(_))` or `Err(ClientError::RetryTimeout)` rather than `Err(ClientError::RequestFailure(status))`.
3. Observe in `stacks-signer/src/v0/signer.rs::submit_block_for_validation` (lines 2613–2644) that this falls into the final `Err(e) => warn!(...)` branch: `submitted_block_proposal` stays `None`, and no entry is written via `insert_pending_block_validation`.
4. Confirm no `BlockResponse` (accept or reject) is ever broadcast by this signer for that block's `signer_signature_hash`, and that `signer_db.block_lookup` for that hash keeps returning `valid: None` indefinitely, with `check_submitted_block_proposal` and `check_pending_block_validations` both no-ops for this hash since neither tracking state was populated. [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** stacks-signer/src/client/mod.rs (L33-38)
```rust
/// Backoff timer initial interval in milliseconds
const BACKOFF_INITIAL_INTERVAL: u64 = 128;
/// Backoff timer max interval in milliseconds
const BACKOFF_MAX_INTERVAL: u64 = 16384;
/// Backoff timer max elapsed seconds
const BACKOFF_MAX_ELAPSED: u64 = 5;
```

**File:** stacks-signer/src/client/mod.rs (L61-78)
```rust
    /// Reqwest specific error occurred
    #[error("{0}")]
    ReqwestError(#[from] reqwest::Error),
    /// Failed to build and sign a new Stacks transaction.
    #[error("Failed to generate transaction from a transaction signer: {0}")]
    TransactionGenerationFailure(String),
    /// Stacks node client request failed
    #[error("Stacks node client request failed: {0}")]
    RequestFailure(reqwest::StatusCode),
    /// Failed to serialize a Clarity value
    #[error("Failed to serialize Clarity value: {0}")]
    ClaritySerializationError(#[from] SerializationError),
    /// Failed to parse a Clarity value
    #[error("Received a malformed clarity value: {0}")]
    MalformedClarityValue(String),
    /// Backoff retry timeout
    #[error("Backoff retry timeout occurred. Stacks node may be down.")]
    RetryTimeout,
```

**File:** stacks-signer/src/v0/signer.rs (L1696-1726)
```rust
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
            self.process_pending_responses_for_block(
                stacks_client,
                sortition_state,
                &mut block_info,
                pending_responses,
            );
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2083-2111)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L2613-2644)
```rust
        match stacks_client.submit_block_for_validation(
            block.clone(),
            if self.validate_with_replay_tx {
                self.global_state_evaluator
                    .get_global_tx_replay_set()
                    .unwrap_or_default()
                    .clone_as_optional()
            } else {
                None
            },
        ) {
            Ok(_) => {
                self.submitted_block_proposal = Some((signer_signature_hash, Instant::now()));
            }
            Err(ClientError::RequestFailure(status)) => {
                if status.as_u16() == TOO_MANY_REQUESTS_STATUS {
                    info!("{self}: Received 429 from stacks node for block validation request. Inserting pending block validation...";
                        "signer_signature_hash" => %signer_signature_hash,
                    );
                    self.signer_db
                        .insert_pending_block_validation(&signer_signature_hash, added_epoch_time)
                        .unwrap_or_else(|e| {
                            warn!("{self}: Failed to insert pending block validation: {e:?}")
                        });
                } else {
                    warn!("{self}: Received non-429 status from stacks node: {status}");
                }
            }
            Err(e) => {
                warn!("{self}: Failed to submit block for validation: {e:?}");
            }
        }
```
