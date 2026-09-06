## Title
Node/signer clock-skew on `block_proposal_max_age_secs` freezes a signer's block decision when the node rejects with HTTP 422 - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`handle_block_proposal` checks proposal freshness against the signer's own clock and, if fresh enough, calls `submit_block_for_validation`, which POSTs to the node's `/v3/block_proposal`. The node independently re-checks freshness against its own clock in `postblock_proposal.rs` and can synchronously reject with HTTP 422 without ever spawning a validation thread. The signer's error handling for that specific status code silently logs and returns, leaving the `BlockInfo` inserted with `valid: None` and `submitted_block_proposal` never set, so no timeout path exists to ever produce a `BlockResponse` for that proposal.

### Finding Description
The equality this depends on is: *"if the signer's freshness check (`timestamp + block_proposal_max_age_secs < signer_now`) passes, then the node's freshness check (`timestamp + block_proposal_max_age_secs < node_now`) also passes."* This equality only holds when the signer and node process clocks agree. It is not enforced anywhere in the code.

Path:
1. The attacker, as the winning miner for a slot, crafts a `NakamotoBlock` with `header.timestamp` chosen to sit exactly at the freshness boundary as seen from the signer, e.g. `now_signer - block_proposal_max_age_secs + 1`, and gossips it as a `BlockProposal`.
2. `handle_block_proposal` evaluates the age check using its own clock and does **not** reject it: [1](#0-0) 
3. Not being provably invalid, the block is inserted into `signer_db` and forwarded to `submit_block_for_validation`: [2](#0-1) 
4. `stacks_client.submit_block_for_validation` POSTs to `/v3/block_proposal`. If the node's clock is skewed such that its own age check trips, the node synchronously returns HTTP 422 without spawning any validation thread: [3](#0-2) 
5. On the signer side, `stacks_client.submit_block_for_validation` maps any non-2xx status to `ClientError::RequestFailure(status)`: [4](#0-3) 
6. `Signer::submit_block_for_validation` only has recovery logic for HTTP 429; every other status (including 422) is just logged, with **no** `insert_pending_block_validation` and, critically, `self.submitted_block_proposal` is **never set** in this branch: [5](#0-4) 

Because `submitted_block_proposal` stays `None`, the only timeout mechanism that could reject a stuck submission never triggers, since it only acts when there is a tracked `Some((hash, Instant))` entry: [6](#0-5) 

No `BlockValidationResponse` will ever arrive for this `signer_signature_hash` because the node never spawned the validation thread that would produce one, so `handle_block_validate_response` / `handle_block_validate_ok` / `handle_block_validate_reject` are never invoked. `determine_response` (used to answer a re-proposal) requires `block_info.valid` to be `Some`, so as long as `valid` remains `None`, nothing is ever sent back to the network for that block: [7](#0-6) 

Existing guards that don't help here: the 429 branch does requeue via `insert_pending_block_validation`, but 422 is treated as an unrecoverable "log and drop" outcome; `check_submitted_block_proposal`'s timeout guard is bypassed entirely because it is gated on `submitted_block_proposal` being populated, which this path skips.

### Impact Explanation
This breaks the **LIVENESS** guarantee that every proposal a signer does not provably reject eventually resolves to an explicit `BlockAccepted`/`BlockRejection`. The affected signer's `BlockInfo` is stuck at `Unprocessed`/`valid=None` for that specific proposal with no scheduled re-check, so that signer contributes no vote (accept or reject) for the height while the clock-skew condition persists. If the attacking miner keeps crafting fresh proposals timed at the signer-vs-node clock-skew boundary, this can be provoked repeatedly per tenure. This matches the "signer wedged into never signing valid blocks (liveness)" High-severity category — not a chain-safety break, since the signer never signs anything invalid, but the block-decision path silently drops rather than failing closed with a bounded rejection.

### Likelihood Explanation
Preconditions: the signer's stacks-node must be reachable at a slightly different wall-clock time than the signer process (realistic in any deployment where the node and signer are on separate hosts, VMs, or containers with imperfect NTP sync), and the attacker must win a single miner slot (as stipulated, one BTC-won slot is sufficient) to control `header.timestamp` and force it to the boundary. No majority of signers, no compromised keys, and no local access are required — only crafting and gossiping a `BlockProposal`, which is within the unprivileged-miner threat model. The effect is repeatable per skewed signer/node pair for as long as the skew persists, and is fully deterministic given knowledge (or trial) of the skew magnitude.

### Recommendation
In `Signer::submit_block_for_validation`, treat any `ClientError::RequestFailure` from the node — not just 429 — as requiring either an immediate local rejection (e.g. `RejectReason::ProposalTooOld` or `ConnectivityIssues`) or a tracked, timeout-bound retry (populate `submitted_block_proposal` or `insert_pending_block_validation` even on 422), so `check_submitted_block_proposal`'s existing timeout/rejection path can eventually fire instead of the block being left indefinitely at `valid: None`.

### Proof of Concept
Rust signer-side test plan (mirrors existing harness in `stacks-signer/src/v0/signer.rs` / `stacks-node/src/tests/signer/v0/mod.rs`):
1. Stand up a `SignerTest` with `block_proposal_max_age_secs` set on the signer config, and independently mock/stub the node's `/v3/block_proposal` handler (or use `TEST_VALIDATE_STALL`/a fake HTTP layer) to always return HTTP 422 for the specific proposal under test, simulating node-side clock skew.
2. Craft a `NakamotoBlock` whose `header.timestamp` passes the signer's own `block_proposal_max_age_secs` check (`assert!` no `ProposalTooOld` rejection is emitted).
3. Propose the block to the signer; assert `stacks_client.submit_block_for_validation` returns `Err(ClientError::RequestFailure(422))`.
4. Assert, after `block_proposal_validation_timeout` elapses and multiple `process_event` ticks pass, that no `BlockResponse` (neither `Accepted` nor `Rejected`) is ever broadcast for this `signer_signature_hash`, and that `signer_db.block_lookup(&hash).unwrap().valid` remains `None` indefinitely — demonstrating the wedge, in contrast to the expected bounded `RejectReason::ConnectivityIssues` outcome.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L458-471)
```rust
    fn determine_response(&mut self, block_info: &BlockInfo) -> Option<BlockResponse> {
        // We will only have the valid field set if we have already validated this block
        // against our stacks-node/local state.
        let valid = block_info.valid?;
        let response = if valid {
            debug!("{self}: Accepting block {}", block_info.block.block_id());
            self.create_block_acceptance(&block_info.block).into()
        } else {
            debug!("{self}: Rejecting block {}", block_info.block.block_id());
            self.create_block_rejection(RejectReason::RejectedInPriorRound, &block_info.block)
                .into()
        };
        Some(response)
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1606-1628)
```rust
        if block_proposal
            .block
            .header
            .timestamp
            .saturating_add(self.block_proposal_max_age_secs)
            < get_epoch_time_secs()
        {
            // Block is too old. Reject it (without validating) rather than silently
            // dropping it: the miner's proposal loop re-sends the same block until it
            // accumulates rejection weight, so a silent drop from the whole signer set
            // would livelock the tenure until the next sortition.
            warn!("{self}: Received a block proposal that is more than {} secs old. Rejecting...", self.block_proposal_max_age_secs;
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_proposal.block.block_id(),
                "block_height" => block_proposal.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "timestamp" => block_proposal.block.header.timestamp,
            );
            let rejection =
                self.create_block_rejection(RejectReason::ProposalTooOld, &block_proposal.block);
            self.send_block_response(&block_proposal.block, rejection.into());
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1670-1719)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L2623-2644)
```rust
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

**File:** stackslib/src/net/api/postblock_proposal.rs (L1223-1234)
```rust
            if block_proposal
                .block
                .header
                .timestamp
                .saturating_add(network.get_connection_opts().block_proposal_max_age_secs)
                < get_epoch_time_secs()
            {
                return Err((
                    422,
                    NetError::SendError("Block proposal is too old to process.".into()),
                ));
            }
```

**File:** stacks-signer/src/client/stacks_client.rs (L310-316)
```rust
        let response = retry_with_exponential_backoff(send_request)?;
        timer.stop_and_record();
        if !response.status().is_success() {
            return Err(ClientError::RequestFailure(response.status()));
        }
        Ok(())
    }
```
