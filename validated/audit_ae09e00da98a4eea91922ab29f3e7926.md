### Title
Signer weight double-counted across both `total_weight_approved` and `total_weight_rejected` when a signer flips its vote — ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
The node-side `StackerDBListener` (used by the mining coordinator to decide whether a block proposal has reached the 70% signing threshold or the >30% blocking-rejection threshold) tracks approvals and rejections in two independent, un-reconciled collections. A single legitimately-registered signer that first rejects a block proposal and later accepts the same proposal (a flow the signer protocol explicitly supports via re-evaluation) has its weight counted into *both* `total_weight_approved` and `total_weight_rejected`, breaking the intended invariant that a signer's weight belongs to exactly one side of the tally.

### Finding Description
`StackerDBListener::run` processes `BlockResponse` messages from signers and updates a shared `BlockStatus` (per `signer_signature_hash`) with two independent membership guards:

- On `BlockResponse::Accepted`, weight is added to `total_weight_approved` only if `block.gathered_signatures` does not already contain the sender's `slot_id`: [1](#0-0) 

- On `BlockResponse::Rejected`, weight is added to `total_weight_rejected` only if `block.responded_signers.insert(slot_id)` succeeds: [2](#0-1) 

`gathered_signatures` and `responded_signers` are separate maps/sets — neither branch checks or clears the other. Nothing removes a signer's weight from `total_weight_rejected` if that same signer later sends an `Accepted` message for the identical `signer_signature_hash`, and vice versa.

This is reachable in normal operation because a signer's local decision on a given `signer_signature_hash` is explicitly allowed to flip in both directions: [3](#0-2) 

For example, `should_reevaluate_reject_reason` permits a previously-rejected block (e.g. rejected for `NotFoundError`, `NoSignerConsensus`, `ConnectivityIssues`, etc.) to be reconsidered and subsequently accepted on re-proposal without any change to `signer_signature_hash` (the hash is derived only from block header fields, not from local signer state): [4](#0-3) 

Contrast this with the signer's own local ledger (`signerdb.rs`), which *does* enforce the mutual-exclusion invariant — a rejection is rejected outright once a signature exists for that signer/block pair, and a new signature clears any prior rejection row: [5](#0-4) 

The node-side `StackerDBListener`, which is the actual arbiter used to decide block finalization from the miner's perspective, has no equivalent reconciliation between its two weight counters: [6](#0-5) 

The coordinator then uses these two counters as if they were mutually exclusive to decide the outcome: [7](#0-6) 

Because StackerDB chunk events are consumed as a real-time stream (not just final-state snapshots), an earlier `Rejected` chunk for a slot is tallied into `total_weight_rejected` before it gets overwritten in that signer's slot by a later `Accepted` chunk (chunks for a signer's `BlockResponse` message lane are versioned/overwritten per slot, but the listener already processed and tallied the earlier chunk when it arrived). This means the double count is a realistic, timing-dependent occurrence of the documented reject→accept re-evaluation flow, not a hypothetical.

### Impact Explanation
This breaks the "aggregated-weight vs verified-accepts/rejects" equality that the coordinator relies on to decide finalization: a single signer's weight can simultaneously and durably contribute to both the approval tally and the rejection tally for the same block, even though only one of its votes reflects its current, final opinion. This is exactly the "rejection recounted as an accept" (or vice versa) miscounted-response class called out as in-scope impact, and it requires nothing beyond a single already-registered signer exercising the protocol's own supported reconsideration flow — no majority, no other signer's key, and no access to the node's `auth_token`.

### Likelihood Explanation
The reject→accept (and accept→reject, via `LocallyAccepted → LocallyRejected` re-evaluation) transition is a first-class, documented part of the signer state machine and is exercised in existing regression tests (e.g. `signer_can_accept_rejected_block`, `signer_reevaluates_proposal_with_missing_burn_view`). Any signer whose proposal validation outcome legitimately changes across a re-proposal (network hiccup, node catching up on a parent block, a burn-view fetch failing transiently, etc.) will trigger this path without any adversarial intent, so the double count can occur during ordinary, non-malicious signer operation.

### Recommendation
In `stackerdb_listener.rs`, maintain a single per-slot "current vote" state for each tracked block instead of two independent sets. When a signer's `Accepted` message arrives, if that slot previously contributed to `total_weight_rejected`, subtract its weight there before adding it to `total_weight_approved` (and symmetrically for `Rejected` after a prior `Accepted`). This mirrors the mutual-exclusion guarantee already implemented on the signer's own `signerdb.rs` ledger (`add_block_rejection_signer_addr` refusing to add a rejection once a signature exists, and clearing rejections when a signature lands) and should be replicated in the node-side listener that actually gates block finalization.

### Proof of Concept
1. A block proposal `P` with `signer_signature_hash = H` is submitted; signer `S` (weight `w`) rejects it due to a transient/re-evaluable reason (e.g. `NotFoundError`), broadcasting `BlockResponse::Rejected{ signer_signature_hash: H, .. }`.
2. `StackerDBListener::run` receives this chunk and executes the `Rejected` arm, incrementing `blocks[H].total_weight_rejected` by `w` via `responded_signers.insert(slot_id)`.
3. The miner re-proposes the same block content (`H` unchanged) after resolving the transient issue; `should_reevaluate_reject_reason` returns `true` for `NotFoundError`, so signer `S` re-validates and now accepts, broadcasting `BlockResponse::Accepted{ signer_signature_hash: H, .. }` in the same StackerDB slot.
4. `StackerDBListener::run` receives this new chunk and executes the `Accepted` arm; since `gathered_signatures` (a separate map from `responded_signers`) does not yet contain `slot_id`, it adds `w` to `blocks[H].total_weight_approved` as well — with no code path removing `w` from `total_weight_rejected`.
5. `blocks[H]` now reports `total_weight_rejected` and `total_weight_approved` that both include `S`'s weight `w`, so `total_weight_rejected + total_weight_approved` can exceed `total_weight`, and both threshold checks in `SignerCoordinator` operate on data that no longer reflects each signer's single, current vote.

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L385-609)
```rust
                match message {
                    SignerMessageV0::BlockResponse(BlockResponse::Accepted(accepted)) => {
                        let BlockAccepted {
                            signer_signature_hash: block_sighash,
                            signature,
                            metadata,
                            response_data,
                        } = accepted;
                        let tenure_extend_timestamp = response_data.tenure_extend_timestamp;
                        let read_count_extend_timestamp =
                            response_data.tenure_extend_read_count_timestamp;

                        let (lock, cvar) = &*self.blocks;
                        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");

                        let Some(block) = blocks.get_mut(&block_sighash) else {
                            info!(
                                "StackerDBListener: Received signature for block that we did not request. Ignoring.";
                                "signature" => %signature,
                                "signer_signature_hash" => %block_sighash,
                                "slot_id" => slot_id,
                                "signer_set" => self.signer_set,
                            );
                            continue;
                        };

                        let Ok(valid_sig) = signer_pubkey.verify(block_sighash.bits(), &signature)
                        else {
                            warn!(
                                "StackerDBListener: Got invalid signature from a signer. Ignoring."
                            );
                            continue;
                        };
                        if !valid_sig {
                            warn!(
                                "StackerDBListener: Processed signature but didn't validate over the expected block. Ignoring";
                                "signature" => %signature,
                                "signer_signature_hash" => %block_sighash,
                                "slot_id" => slot_id,
                            );
                            continue;
                        }

                        if Self::fault_injection_ignore_signatures() {
                            warn!("StackerDBListener: fault injection: ignoring well-formed signature for block";
                                "signer_signature_hash" => %block_sighash,
                                "signer_pubkey" => signer_pubkey.to_hex(),
                                "signer_slot_id" => slot_id,
                                "signature" => %signature,
                                "signer_weight" => signer_entry.weight,
                                "total_weight_approved" => block.total_weight_approved,
                                "percent_approved" => block.total_weight_approved as f64 / self.total_weight as f64 * 100.0,
                                "total_weight_rejected" => block.total_weight_rejected,
                                "percent_rejected" => block.total_weight_rejected as f64 / self.total_weight as f64 * 100.0,
                            );
                            continue;
                        }

                        if !block.gathered_signatures.contains_key(&slot_id) {
                            block.total_weight_approved = block
                                .total_weight_approved
                                .saturating_add(signer_entry.weight);

                            info!("StackerDBListener: Signature Added to block";
                                "signer_signature_hash" => %block_sighash,
                                "signer_pubkey" => signer_pubkey.to_hex(),
                                "signer_slot_id" => slot_id,
                                "signature" => %signature,
                                "signer_weight" => signer_entry.weight,
                                "total_weight_approved" => block.total_weight_approved,
                                "percent_approved" => block.total_weight_approved as f64 / self.total_weight as f64 * 100.0,
                                "total_weight_rejected" => block.total_weight_rejected,
                                "percent_rejected" => block.total_weight_rejected as f64 / self.total_weight as f64 * 100.0,
                                "weight_threshold" => self.weight_threshold,
                                "tenure_extend_timestamp" => tenure_extend_timestamp,
                                "read_count_extend_timestamp" => read_count_extend_timestamp,
                                "server_version" => metadata.server_version,
                            );
                        }
                        block.gathered_signatures.insert(slot_id, signature);
                        block.responded_signers.insert(slot_id);

                        if block.total_weight_approved >= self.weight_threshold {
                            // Signal to anyone waiting on this block that we have enough signatures
                            cvar.notify_all();
                        }

                        // Update the idle timestamp for this signer
                        self.update_idle_timestamp(
                            signer_pubkey.clone(),
                            tenure_extend_timestamp,
                            signer_entry.weight,
                        );

                        // Update the read-count timestamp for this signer
                        self.update_read_count_timestamp(
                            signer_pubkey,
                            read_count_extend_timestamp,
                            signer_entry.weight,
                        );
                    }
                    SignerMessageV0::BlockResponse(BlockResponse::Rejected(rejected_data)) => {
                        let (lock, cvar) = &*self.blocks;
                        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");

                        let Some(block) = blocks.get_mut(&rejected_data.signer_signature_hash)
                        else {
                            info!(
                                "StackerDBListener: Received rejection for block that we did not request. Ignoring.";
                                "signer_signature_hash" => %rejected_data.signer_signature_hash,
                                "slot_id" => slot_id,
                                "signer_set" => self.signer_set,
                            );
                            continue;
                        };

                        let rejected_pubkey = match rejected_data.recover_public_key() {
                            Ok(rejected_pubkey) => {
                                if rejected_pubkey != signer_pubkey {
                                    warn!("StackerDBListener: Recovered public key from rejected data does not match signer's public key. Ignoring.");
                                    continue;
                                }
                                rejected_pubkey
                            }
                            Err(e) => {
                                warn!("StackerDBListener: Failed to recover public key from rejected data: {e:?}. Ignoring.");
                                continue;
                            }
                        };

                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);

                            // Track transactions that failed validation, accumulating
                            // per-txid signer weight and whether any signer flagged
                            // the tx as genuinely problematic.
                            if let Some(txid) = &rejected_data.response_data.failed_txid {
                                match &rejected_data.reason_code {
                                    RejectCode::ValidationFailed(
                                        ValidateRejectCode::BadTransaction
                                        | ValidateRejectCode::ProblematicTransaction,
                                    ) => {
                                        let info =
                                            block.failed_txids.entry(txid.clone()).or_default();
                                        info.total_weight =
                                            info.total_weight.saturating_add(signer_entry.weight);
                                        if matches!(
                                            rejected_data.reason_code,
                                            RejectCode::ValidationFailed(
                                                ValidateRejectCode::ProblematicTransaction
                                            )
                                        ) {
                                            info.problematic_weight = info
                                                .problematic_weight
                                                .saturating_add(signer_entry.weight);
                                        }
                                    }
                                    _ => {}
                                }
                            }

                            info!("StackerDBListener: Signer rejected block";
                                "signer_signature_hash" => %rejected_data.signer_signature_hash,
                                "signer_pubkey" => rejected_pubkey.to_hex(),
                                "signer_slot_id" => slot_id,
                                "signature" => %rejected_data.signature,
                                "signer_weight" => signer_entry.weight,
                                "total_weight_approved" => block.total_weight_approved,
                                "percent_approved" => block.total_weight_approved as f64 / self.total_weight as f64 * 100.0,
                                "total_weight_rejected" => block.total_weight_rejected,
                                "percent_rejected" => block.total_weight_rejected as f64 / self.total_weight as f64 * 100.0,
                                "weight_threshold" => self.weight_threshold,
                                "reason" => rejected_data.reason,
                                "reason_code" => ?rejected_data.reason_code,
                                "tenure_extend_timestamp" => rejected_data.response_data.tenure_extend_timestamp,
                                "failed_txid" => ?rejected_data.response_data.failed_txid,
                                "server_version" => rejected_data.metadata.server_version,
                            );
                        }

                        if block
                            .total_weight_rejected
                            .saturating_add(self.weight_threshold)
                            > self.total_weight
                        {
                            // Signal to anyone waiting on this block that we have enough rejections
                            cvar.notify_all();
                        }

                        // Update the idle timestamp for this signer
                        self.update_idle_timestamp(
                            signer_pubkey.clone(),
                            rejected_data.response_data.tenure_extend_timestamp,
                            signer_entry.weight,
                        );

                        // Update the read-count timestamp for this signer
                        self.update_read_count_timestamp(
                            signer_pubkey,
                            rejected_data
                                .response_data
                                .tenure_extend_read_count_timestamp,
                            signer_entry.weight,
                        );
                    }
                    SignerMessageV0::BlockProposal(_) => {
                        debug!("Received block proposal message. Ignoring.");
                    }
                    SignerMessageV0::BlockPushed(_) => {
                        debug!("Received block pushed message. Ignoring.");
                    }
                    SignerMessageV0::MockSignature(_)
                    | SignerMessageV0::MockProposal(_)
                    | SignerMessageV0::MockBlock(_) => {
                        debug!("Received mock message. Ignoring.");
                    }
                    SignerMessageV0::StateMachineUpdate(update) => {
                        self.update_global_state_evaluator(&signer_pubkey, update);
                    }
                    SignerMessageV0::BlockPreCommit(_) => {
                        debug!("Received block pre-commit message. Ignoring.");
                    }
                };
```

**File:** docs/signer-flows.md (L140-150)
```markdown
    Unprocessed --> PreCommitted : mark_pre_committed
    PreCommitted --> LocallyAccepted : mark_locally_accepted = WE SIGN
    Unprocessed --> LocallyRejected : mark_locally_rejected
    PreCommitted --> LocallyRejected : mark_locally_rejected
    LocallyRejected --> LocallyAccepted : re-evaluated
    LocallyAccepted --> LocallyRejected : re-evaluated
    LocallyAccepted --> GloballyAccepted : mark_globally_accepted
    LocallyRejected --> GloballyRejected : mark_globally_rejected
    GloballyAccepted --> [*]
    GloballyRejected --> [*]
```
```

**File:** stacks-signer/src/v0/signer.rs (L2705-2739)
```rust
/// Determine if a block should be re-evaluated based on its rejection reason˝
fn should_reevaluate_reject_reason(block_info: &BlockInfo) -> bool {
    if let Some(reject_reason) = &block_info.reject_reason {
        match reject_reason {
            RejectReason::ValidationFailed(ValidateRejectCode::UnknownParent)
            | RejectReason::ValidationFailed(ValidateRejectCode::NotFoundError)
            | RejectReason::NoSortitionView
            | RejectReason::ConnectivityIssues(_)
            | RejectReason::TestingDirective
            | RejectReason::InvalidTenureExtend
            | RejectReason::ConsensusHashMismatch { .. }
            | RejectReason::NoSignerConsensus
            | RejectReason::NotRejected
            | RejectReason::Unknown(_) => true,
            RejectReason::ValidationFailed(_)
            | RejectReason::RejectedInPriorRound
            | RejectReason::SortitionViewMismatch
            | RejectReason::ReorgNotAllowed
            | RejectReason::InvalidBitvec
            | RejectReason::PubkeyHashMismatch
            | RejectReason::InvalidMiner
            | RejectReason::NotLatestSortitionWinner
            | RejectReason::InvalidParentBlock
            | RejectReason::DuplicateBlockFound
            | RejectReason::IrrecoverablePubkeyHash
            | RejectReason::ProblematicTransactions
            | RejectReason::ProposalTooOld => {
                // No need to re-validate these types of rejections.
                false
            }
        }
    } else {
        false
    }
}
```

**File:** stacks-signer/src/signerdb.rs (L1922-1940)
```rust
    /// Record an observed block rejection_signature
    pub fn add_block_rejection_signer_addr(
        &self,
        block_sighash: &Sha512Trunc256Sum,
        addr: &StacksAddress,
        reject_reason: RejectReasonPrefix,
    ) -> Result<bool, DBError> {
        // If this signer/block already has a signature, do not allow a rejection
        let sig_qry = "SELECT EXISTS(SELECT 1 FROM block_signatures WHERE signer_signature_hash = ?1 AND signer_addr = ?2)";
        let sig_args = params![block_sighash, addr.to_string()];
        let exists = self.db.query_row(sig_qry, sig_args, |row| row.get(0))?;
        if exists {
            warn!("Cannot add block rejection because a signature already exists.";
                "signer_signature_hash" => %block_sighash,
                "signer_address" => %addr,
                "reject_reason" => ?reject_reason
            );
            return Ok(false);
        }
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L505-545)
```rust
                counters.set_miner_current_rejections_timeout_secs(rejections_timeout.as_secs());
                counters.set_miner_current_rejections(rejections);
            }

            if block_status
                .total_weight_rejected
                .saturating_add(self.weight_threshold)
                > self.total_weight
            {
                info!(
                    "{}/{} signer weight votes to reject block",
                    block_status.total_weight_rejected, self.total_weight;
                    "signer_signature_hash" => %block_signer_sighash,
                );
                counters.bump_naka_rejected_blocks();

                // Only act on failed txids that a blocking minority (>30% weight) agrees on
                let blocking_minority = self.total_weight.saturating_sub(self.weight_threshold);
                let mut temporarily_excluded_txids = HashSet::new();
                let mut permanently_excluded_txids = HashSet::new();
                for (txid, info) in &block_status.failed_txids {
                    if info.total_weight > blocking_minority {
                        // Do not perma ban txids that only a small minority of signers reported as problematic
                        // But make sure its removed from the next block proposal
                        if info.problematic_weight > blocking_minority {
                            permanently_excluded_txids.insert(txid.clone());
                        } else {
                            temporarily_excluded_txids.insert(txid.clone());
                        }
                    }
                }

                return Err(NakamotoNodeError::SignersRejected {
                    temporarily_excluded_txids,
                    permanently_excluded_txids,
                });
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```
