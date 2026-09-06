## Finding

### Title
Broadcast-before-persist ordering in `check_submitted_block_proposal` allows a durably-lost rejection to later be recast as an acceptance - (File: stacks-signer/src/v0/signer.rs)

### Summary
`check_submitted_block_proposal` broadcasts a public block rejection over StackerDB *before* persisting the corresponding `LocallyRejected` state to `SignerDB`, inverting the Checks-Effects-Interactions order used consistently everywhere else in the file. If the signer crashes or otherwise fails to complete the DB write after the broadcast goes out, the durable equivocation record for that block is lost, letting the signer later reprocess and sign the same block it already publicly rejected.

### Finding Description
Every other rejection/acceptance path in `stacks-signer/src/v0/signer.rs` commits the internal `SignerDB` state (`insert_block`) *before* triggering the external interaction (`send_block_response` / broadcast):

- `handle_block_validate_ok`'s inline rejection path: `mark_locally_rejected` → `insert_block` → `handle_block_rejection` → `send_block_response` [1](#0-0) 
- `handle_block_validate_reject`: `mark_locally_rejected` → `insert_block` → `handle_block_rejection` → `send_block_response` [2](#0-1) 
- the sign path in `handle_block_pre_commit`: `mark_locally_accepted` → `insert_block` → `handle_block_signature` → `send_block_response` (with an explicit comment "have to save the signature _after_ the block info") [3](#0-2) 

`check_submitted_block_proposal`, however, calls `send_block_response` (the external broadcast/Interaction) *before* `self.signer_db.insert_block(&block_info)` (the internal Effect):

```
if let Err(e) = block_info.mark_locally_rejected() { ... };
self.send_block_response(&block_info.block, rejection.into());   // <== broadcast first

self.signer_db
    .insert_block(&block_info)                                    // <== persisted after
    .unwrap_or_else(|e| self.handle_insert_block_error(e));
``` [4](#0-3) 

This function is invoked from `check_pending_block_validations`/`process_event`'s regular housekeeping whenever a submitted block-validation request to the node times out, and `self.submitted_block_proposal` is unconditionally cleared via `.take()` at entry regardless of whether the subsequent `insert_block` succeeds. [5](#0-4) 

Because the rejection is announced to the network before the local state is committed, a crash, panic, or unclean shutdown occurring between the broadcast and the `insert_block` call leaves `SignerDB` still showing the block as `Unprocessed` with `valid = None`, even though the network has already received a signed `BlockRejection` for it from this signer. The `valid.is_some()` guards used elsewhere to avoid double-processing a validation response (`handle_block_validate_ok`/`handle_block_validate_reject`) [6](#0-5) [7](#0-6)  will therefore not fire on restart, and a late-arriving validation-OK response for that exact block (from the node, or reprocessing triggered by a re-proposal/pre-commit replay) can proceed through the normal accept path and be signed.

### Impact Explanation
This breaks the intended invariant that a signer's on-disk record durably reflects every decision it has broadcast, before that decision leaves the process. The signer can end up:
1. Broadcasting `Rejected` (ConnectivityIssues) for a block hash to the whole signer set,
2. Losing the durable record of that decision on crash/restart,
3. Later signing (`Accepted`) the *same* block hash after a delayed/late validation success is processed as if fresh.

This is a "rejection recounted as an accept" for a single signer's own record, achievable without needing a majority of signers, cooperation from other signers, or any secret key material - only a slow/timed-out node validation window (independently reachable via a one-slot miner submitting a proposal and the node/validation thread being delayed) coinciding with a process restart. It undermines the equivocation-guard durability the rest of the codebase is careful to preserve (see the analogous "have to save ... after the block info" comment elsewhere in the same file [8](#0-7) ), and maps to the High-impact class "losing the equivocation guard on restart."

### Likelihood Explanation
The timeout path (`check_submitted_block_proposal`) runs routinely whenever node validation is slow (default in every `process_event` pass) [9](#0-8) [10](#0-9) , so the ordering hazard is exercised on any real block-proposal-validation timeout — not a rare edge case. The remaining requirement (a crash/restart landing in the narrow window between the broadcast call and `insert_block`) is a classic durability race; it is a lower-probability but realistic operational event (process kill, OOM, panic elsewhere in the loop, disk/DB I/O error triggering `handle_insert_block_error`).

### Recommendation
Reorder `check_submitted_block_proposal` to follow the Checks-Effects-Interactions pattern used everywhere else in the file: call `self.signer_db.insert_block(&block_info)` immediately after `mark_locally_rejected()` and only then call `self.send_block_response(...)`, mirroring the pattern in `handle_block_validate_reject` and the rejection branch of `handle_block_validate_ok`.

### Proof of Concept
1. A miner (single slot) submits a `BlockProposal`; the signer submits it for validation and records `submitted_block_proposal`. [11](#0-10) 
2. The node's validation response is delayed past `block_proposal_validation_timeout`.
3. On the next `process_event` tick, `check_submitted_block_proposal` fires: it marks the block `LocallyRejected` in memory, calls `send_block_response` (broadcasting the rejection over StackerDB to all peers), and only then calls `insert_block`. [12](#0-11) 
4. If the signer process is killed/restarted (or `insert_block` fails and is only logged via `handle_insert_block_error`) between steps 3's broadcast and the `insert_block` call, `SignerDB` retains the block as `Unprocessed`/`valid = None`.
5. After restart, a late `BlockValidateResponse::Ok` for the same `signer_signature_hash` arrives (or the block is re-proposed and re-submitted for validation and now succeeds); since `block_info.valid` is `None`, the early-exit guard does not trigger, and the signer proceeds through `handle_block_validate_ok`'s accept path, potentially pre-committing/signing a block it already publicly rejected in step 3.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L341-342)
```rust
        self.check_submitted_block_proposal();
        self.check_pending_block_validations(stacks_client);
```

**File:** stacks-signer/src/v0/signer.rs (L408-409)
```rust
        self.check_submitted_block_proposal();
        self.check_pending_block_validations(stacks_client);
```

**File:** stacks-signer/src/v0/signer.rs (L1355-1365)
```rust
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
```

**File:** stacks-signer/src/v0/signer.rs (L1467-1478)
```rust
        if let Err(e) = block_info.mark_locally_accepted(false) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}",);
            }
        }
        self.signer_db
            .insert_block(&block_info)
            .unwrap_or_else(|e| self.handle_insert_block_error(e));
        let accepted = self.create_block_acceptance(&block_info.block);
        // have to save the signature _after_ the block info
        self.handle_block_signature(stacks_client, sortition_state, &accepted);
        self.send_block_response(&block_info.block, accepted.into());
```

**File:** stacks-signer/src/v0/signer.rs (L1682-1700)
```rust
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
```

**File:** stacks-signer/src/v0/signer.rs (L1932-1944)
```rust
        if block_info.valid.is_some() {
            // We should only have valid set if we have already processed a validation response for this block OR we locally marked it as rejected
            // and responded to it. If we received a new proposal for it that we wished to consider, we would have reset valid to None.
            // This is only really possible when a signer is sharing a node or we have timed out a pending validation and it suddenly arrives.
            warn!(
                "{self}: Already processed a block validate response for block {}. Ignoring validation response.", block_info.block.header.signer_signature_hash(); "valid" => ?block_info.valid,
            );
            return;
        }
        if !block_info.check_static_valid_block() {
            debug!("{self}: Block is syntatically invalid; will not store");
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2008-2019)
```rust
        if block_info.valid.is_some() {
            // We should only have valid set if we have already processed a validation response for this block OR we locally marked it as rejected.
            // and responded to it. If we received a new proposal for it, we would have reset valid to None.
            warn!(
                "{self}: Already processed a block validate response for block {}. Ignoring validation response.", block_info.block.header.signer_signature_hash(); "valid" => ?block_info.valid,
            );
            return;
        }
        if !block_info.check_static_valid_block() {
            debug!("{self}: Block is syntatically invalid; will not store");
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2020-2051)
```rust
        if let Err(e) = block_info.mark_locally_rejected() {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally rejected: {e:?}");
            }
        }
        let block_rejection = BlockRejection::from_validate_rejection(
            block_validate_reject.clone(),
            &self.private_key,
            self.mainnet,
            self.signer_db.calculate_full_extend_timestamp(
                self.proposal_config
                    .tenure_idle_timeout
                    .saturating_add(self.proposal_config.tenure_idle_timeout_buffer),
                &block_info.block,
                false,
            ),
            self.signer_db.calculate_read_count_extend_timestamp(
                self.proposal_config
                    .read_count_idle_timeout
                    .saturating_add(self.proposal_config.tenure_idle_timeout_buffer),
                &block_info.block,
                false,
            ),
        );

        block_info.reject_reason = Some(block_rejection.response_data.reject_reason.clone());
        self.signer_db
            .insert_block(&block_info)
            .unwrap_or_else(|e| self.handle_insert_block_error(e));
        self.handle_block_rejection(&block_rejection, sortition_state);
        self.send_block_response(&block_info.block, block_rejection.into());
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2116-2122)
```rust
    fn check_submitted_block_proposal(&mut self) {
        let Some((proposal_signer_sighash, block_submission)) =
            self.submitted_block_proposal.take()
        else {
            // Nothing to check.
            return;
        };
```

**File:** stacks-signer/src/v0/signer.rs (L2156-2172)
```rust
        let rejection = self.create_block_rejection(
            RejectReason::ConnectivityIssues(
                "failed to receive block validation response in time".to_string(),
            ),
            &block_info.block,
        );
        block_info.reject_reason = Some(rejection.response_data.reject_reason.clone());
        if let Err(e) = block_info.mark_locally_rejected() {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally rejected: {e:?}");
            }
        };
        self.send_block_response(&block_info.block, rejection.into());

        self.signer_db
            .insert_block(&block_info)
            .unwrap_or_else(|e| self.handle_insert_block_error(e));
```
