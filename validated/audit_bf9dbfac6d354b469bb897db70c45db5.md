### Title
Miner-side signer weight double-counted across Reject-then-Accept for the same block, allowing a stale rejection to permanently inflate `total_weight_rejected` and cause the miner to abandon a validly-signed block - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListener` tracks two independent weight tallies per proposed block — `total_weight_approved` and `total_weight_rejected` — gated by two different membership checks (`gathered_signatures` for accepts, `responded_signers` for rejects). A single signer can legitimately (per the documented `BlockState` state machine, which explicitly allows `LocallyRejected --> LocallyAccepted` re-evaluation) send a `Reject` for a block and later send an `Accept` for the *same* `signer_signature_hash`. Because the accept-path guard only checks `gathered_signatures`, not `responded_signers`, the signer's weight gets added to `total_weight_approved` without ever being removed from `total_weight_rejected`. The result: that signer's weight is counted in both totals simultaneously, breaking the intended invariant that approve/reject weight tallies are over disjoint signer sets bounded by `total_weight`.

### Finding Description
In `stacks-node/src/nakamoto_node/stackerdb_listener.rs`:

- `BlockResponse::Accepted` handling guards weight addition on `block.gathered_signatures.contains_key(&slot_id)` [1](#0-0) 
- `BlockResponse::Rejected` handling guards weight addition on `block.responded_signers.insert(slot_id)` [2](#0-1) 

These are two separate sets. If a signer's `Reject` arrives first, `responded_signers` gains the slot and `total_weight_rejected` is bumped. If the same signer later sends an `Accept` for the identical block hash, `gathered_signatures` does not yet contain that slot, so the accept-path condition is true and `total_weight_approved` is *also* bumped — even though `responded_signers` already recorded this signer. Nothing ever removes the earlier reject weight from `total_weight_rejected`.

This reject→accept sequence for the *same* `signer_signature_hash` is not a hypothetical/malicious-only scenario: the signer's own documented state machine allows a block to move from `LocallyRejected` back to `LocallyAccepted` on re-evaluation [3](#0-2) , and transient reject reasons (e.g. connectivity/timeout) are explicitly re-evaluated later without changing the block's hash [4](#0-3) . For example, `check_submitted_block_proposal` broadcasts a `Reject` with `RejectReason::ConnectivityIssues` when a validation response is late [5](#0-4) , and a subsequent validation response for the very same block can still lead to `mark_locally_accepted`/`Accept` broadcast later [6](#0-5) .

On the miner/coordinator side, `signer_coordinator.rs::get_block_status` treats `total_weight_rejected` and `total_weight_approved` as if they partition signer weight: it checks the reject-blocking condition first and errors out with `SignersRejected` if `total_weight_rejected + weight_threshold > total_weight`, before ever checking `total_weight_approved` [7](#0-6) . Because a stale, superseded reject vote is never cleared for the affected block (the explicit code comment in `reset_rejections` even states rejections "can be" cleared, unlike approvals, but this only happens on retry-timeout of a *proposal resubmission*, not in response to a signer's own later acceptance) [8](#0-7) , the double-counted weight can push `total_weight_rejected` over the blocking-minority threshold even while `total_weight_approved` is simultaneously at or above the 70% acceptance threshold from the *other* signers, causing the miner to discard a block that in fact carries (or would carry) a sufficient, valid acceptance signature set.

### Impact Explanation
This breaks the "aggregated-weight vs verified-accepts" equality relied on by the mining/coordination logic: the sum of `total_weight_approved` and `total_weight_rejected` is assumed to reflect disjoint per-signer decisions bounded by `total_weight`, but a single signer's weight can appear in both. The practical consequence is a liveness wedge — the miner can be made to treat an otherwise validly and sufficiently signed block as rejected (`NakamotoNodeError::SignersRejected`), stalling tenure block production, without any signer acting maliciously and without requiring a majority of signers. This matches the "High" impact class: a legitimate signer/miner interaction can be wedged out of accepting a valid block due to permanently-stale reject weight. It requires only one signer's normal-course reject→accept re-evaluation on the same block hash (not majority collusion, not key compromise).

### Likelihood Explanation
Requires no adversarial signer — only ordinary conditions already anticipated and tested in this codebase (connectivity/timeout-driven rejects that are later re-evaluated to acceptance for the same block). The window in which this matters (rejections accumulating close to the 30% blocking threshold while the block ultimately still reaches 70% acceptance) is narrower than "always triggers," so likelihood is moderate rather than certain, but the code path exists unconditionally with no signer-set-size precondition beyond having enough concurrently-slow/re-evaluating signers to approach the 30% rejection threshold.

### Recommendation
Make `total_weight_approved`/`total_weight_rejected` tracking mutually exclusive per slot: before adding rejected weight, check (and clear) `gathered_signatures`; before adding approved weight, check (and clear) any weight already counted in `total_weight_rejected` for that slot (and vice versa when an accept follows a reject). Alternatively, track a single `HashMap<u32, Decision>` per slot (Accept/Reject) and derive both totals by summing over that single source of truth, so a slot's weight can never appear in both tallies simultaneously.

### Proof of Concept
1. Configure a reward set where one signer, S, controls close to (but not exceeding) the 30% blocking-minority weight while other signers hold the remaining ~70%+.
2. Have S's local signer instance broadcast `BlockResponse::Rejected` for block B (e.g., via a transient `ConnectivityIssues`/timeout condition as triggered by `check_submitted_block_proposal`) — the node's `StackerDBListener` records this in `responded_signers` and bumps `total_weight_rejected` by S's weight [2](#0-1) .
3. Shortly after, have S re-evaluate and broadcast `BlockResponse::Accepted` for the *same* `signer_signature_hash` of block B (as the signer state machine explicitly permits) — `StackerDBListener` sees `gathered_signatures` does not yet contain S's slot and bumps `total_weight_approved` by S's weight as well [1](#0-0) .
4. Have the remaining honest signers all send `Accepted` for block B, reaching the genuine 70% threshold.
5. Observe in `signer_coordinator::get_block_status` that `total_weight_rejected` (inflated by S's double-counted weight, plus any other honest signers who transiently rejected before B stabilized) can cross `self.total_weight - self.weight_threshold`, causing `NakamotoNodeError::SignersRejected` to be returned even though the same block already has (or is about to have) genuinely sufficient approving signatures [9](#0-8) .

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-465)
```rust
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
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-518)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L706-723)
```rust
    /// Reset rejections for a block proposal.
    /// This is used when a block proposal times out and we need to retry it by
    /// clearing the block's rejections. Block approvals cannot be cleared
    /// because an old approval could always be used to make a block reach
    /// the approval threshold.
    pub fn reset_rejections(&self, signer_sighash: &Sha512Trunc256Sum) {
        let (lock, _cvar) = &*self.blocks;
        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");
        if let Some(block) = blocks.get_mut(signer_sighash) {
            block.responded_signers.clear();
            block.total_weight_rejected = 0;

            // Add approving signers back to the responded signers set
            for (slot_id, _) in block.gathered_signatures.iter() {
                block.responded_signers.insert(*slot_id);
            }
        }
    }
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

**File:** docs/signer-flows.md (L178-187)
```markdown
    REEV --> DONE1{"globally accepted and<br/>already responded?"}
    DONE1 -- yes --> IGN2(["ignore"])
    DONE1 -- no --> REASON{"prior reject reason<br/>re-evaluable?<br/>should_reevaluate_reject_reason"}
    REASON -- no --> PC{"state = PreCommitted?"}
    PC -- yes --> RESEND["re-send pre-commit, re-run<br/>handle_block_pre_commit → section 5"]
    PC -- no --> PREV["re-send previous response<br/>determine_response, or wait if<br/>validation still pending"]
    REASON -- yes --> FRESH
    KNOWN -- no --> DRAIN["collect early votes<br/>drain_pending_block_responses"] --> FRESH["fresh evaluation:<br/>new BlockInfo, fetch<br/>SortitionsView if needed"]
    FRESH --> CHECK["check_block_against_state:<br/>protocol version consensus (NoSignerConsensus),<br/>static validity, no problematic_txs<br/>(ProblematicTransactions), then<br/>v1 SortitionsView::check_proposal or<br/>v2 GlobalStateView::check_proposal → section 7"]
    CHECK -- invalid --> REJ["send rejection<br/>(not stored)"]:::bad
```

**File:** stacks-signer/src/v0/signer.rs (L1961-1983)
```rust
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
```

**File:** stacks-signer/src/v0/signer.rs (L2150-2168)
```rust
        // We cannot determine the validity of the block, but we have not reached consensus on it yet.
        // Reject it so we aren't holding up the network because of our inaction.
        warn!(
            "{self}: Failed to receive block validation response within {} ms. Rejecting block.", self.block_proposal_validation_timeout.as_millis();
            "signer_signature_hash" => %proposal_signer_sighash,
        );
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
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-545)
```rust
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
