### Title
Signer weight double-counted across the accept and reject tallies, breaking the reject/accept threshold equality — ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener` maintains two independent weight tallies per block, `total_weight_approved` and `total_weight_rejected`, gated by two *different* dedup sets: the accept path checks `block.gathered_signatures.contains_key(&slot_id)` while the reject path checks `block.responded_signers.insert(slot_id)`. Because a single signer can legitimately re-evaluate and flip its vote for the same `signer_signature_hash` (the proposal loop explicitly supports "our rejection reason allows us to reconsider" via `should_reevaluate_block`), that signer's weight can be added to *both* tallies for the same block, since the accept path never checks (or clears) `responded_signers`/prior rejection state.

### Finding Description
In `handle rejected` branch, weight is added once, gated by `block.responded_signers.insert(slot_id)`: [1](#0-0) 

In the accepted branch, weight is added gated by a *separate* structure, `gathered_signatures`, not by `responded_signers`: [2](#0-1) 

There is no code path anywhere in this handler that decrements `total_weight_rejected` when a signer that previously rejected later accepts (or vice versa). The signer client itself explicitly allows this transition: `should_reevaluate_block` lets a previously-rejected proposal be re-processed and potentially accepted once "our rejection reason allows us to reconsider": [3](#0-2) 
and `determine_response`/`check_block_against_state` can transition a block from `valid=false` (rejected) to `valid=true` (accepted) on re-evaluation: [4](#0-3) 

Consequently, `total_weight_approved + total_weight_rejected` can exceed a single signer's true, once-counted contribution and even exceed `self.total_weight` in aggregate, breaking the implicit invariant the coordinator relies on: that a signer's weight counts toward exactly one of "approved" or "rejected" at a time. `SignCoordinator::get_block_status` checks the rejection-threshold branch *before* the approval branch: [5](#0-4) 
so a stale, never-cleared rejection weight from a signer who has since switched to accepting can push `total_weight_rejected + weight_threshold > total_weight` and cause the miner to declare the block dead (`NakamotoNodeError::SignersRejected`) even though that same signer (and possibly enough others) have gone on to produce valid signatures that would otherwise reach the approval threshold.

### Impact Explanation
This is a miscounted-response bug: a single one-slot signer's weight can be simultaneously and permanently double-booked into both the accept and reject tallies for the same block, because the two tallies use inconsistent dedup keys and neither is ever revoked on vote-flip. Since the coordinator's rejection branch is evaluated first and only needs `total_weight_rejected + weight_threshold > total_weight`, stale rejection weight that should have been retracted can wedge a valid, ultimately-signable block into a permanent `SignersRejected` verdict — a liveness break matching the "signer wedged into never signing valid blocks" / "miscounted response" category in scope. No majority collusion is needed: only the ordinary behavior of one signer legitimately reconsidering its own earlier rejection (a state transition the codebase itself documents and supports) is required to create the inconsistency.

### Likelihood Explanation
The vote-flip (reject → later accept for the same `signer_signature_hash`) is not a hypothetical edge case — it is an explicitly supported, documented code path (`should_reevaluate_block`, "our rejection reason allows us to reconsider"). Any signer whose initial rejection reason resolves (e.g., a transient `ConnectivityIssues`, `NoSignerConsensus`, or timing-based rejection) before the miner gives up will trigger this. The `stackerdb_listener.rs` accounting has no mechanism at all to reconcile the two tallies, so the bug triggers whenever this ordinary re-evaluation happens, without requiring any malicious or majority behavior.

### Recommendation
Track each signer's *current* vote state (approve/reject) in a single map keyed by `slot_id`, and when a new message for a slot arrives, first remove that slot's weight from whichever tally it was previously counted toward (if any) before adding it to the new tally. Alternatively, unify `gathered_signatures` and `responded_signers`/rejection bookkeeping into one authoritative "current vote per slot" structure so that recomputation of `total_weight_approved`/`total_weight_rejected` is always derived from a single source of truth rather than two independently-maintained, never-reconciled counters.

### Proof of Concept
1. Miner proposes block `B`. Signer `S` (slot `k`, weight `w`) evaluates `B`, currently sees a transient/timing rejection (e.g., `SortitionViewMismatch` or `ConnectivityIssues`) and broadcasts `BlockResponse::Rejected` for `B`.
2. `StackerDBListener` records this: `responded_signers.insert(k)` succeeds → `total_weight_rejected += w` (per lines 515-518 above).
3. Shortly after, the transient condition resolves; the miner's proposal-retry loop or a `pre-commit` cycle causes signer `S` to re-evaluate `B` per `should_reevaluate_block`/`determine_response`, and `S` now broadcasts a valid `BlockResponse::Accepted` for the *same* `signer_signature_hash`.
4. `StackerDBListener`'s accepted-branch check `!block.gathered_signatures.contains_key(&slot_id)` is true (slot `k` was never in `gathered_signatures`), so it adds `w` to `total_weight_approved` as well — with no corresponding subtraction from `total_weight_rejected`.
5. Now `total_weight_approved + total_weight_rejected > total_weight` by `w`. If enough such flips accumulate (or combined with genuine rejections from a blocking minority), `total_weight_rejected + weight_threshold > total_weight` can become true in `get_block_status` even though `total_weight_approved` is independently climbing toward (or has reached) `weight_threshold`, causing the coordinator to prematurely return `SignersRejected` for a block that in fact has, or would have, sufficient real signature weight — a liveness wedge triggerable by ordinary single-signer vote reconsideration.

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

**File:** stacks-signer/src/v0/signer.rs (L1560-1571)
```rust
        } else {
            info!(
                "{self}: received a block proposal for this block before, but our rejection reason allows us to reconsider";
                "reject_reason" => ?block_info.reject_reason,
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_proposal.block.block_id(),
                "block_height" => block_proposal.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "consensus_hash" => %block_proposal.block.header.consensus_hash
            );
        }
        true
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
