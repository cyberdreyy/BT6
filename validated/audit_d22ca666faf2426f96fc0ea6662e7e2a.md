## Finding [1](#0-0) [2](#0-1) 

### Title
Signer vote flip (Reject → Accept) lets a single signer's weight be double-counted in both `total_weight_rejected` and `total_weight_approved` in the miner's `BlockStatus` tally - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
The node-side `StackerDBListener`, which the mining coordinator (`signer_coordinator.rs`) relies on to decide whether a block was globally accepted or globally rejected, tracks two independent de-duplication sets for the same `slot_id`: `block.gathered_signatures` (used to dedupe `Accepted` messages) and `block.responded_signers` (used to dedupe `Rejected` messages). These sets are not kept consistent with each other, so a signer that first rejects and later accepts the same block proposal has its weight counted in *both* `total_weight_rejected` and `total_weight_approved`.

### Finding Description
In `handle_block_signature`/`BlockResponse::Accepted` handling: [1](#0-0)  weight is added to `total_weight_approved` only if `!block.gathered_signatures.contains_key(&slot_id)`; it is not gated on `responded_signers`.

In the `Rejected` handling: [3](#0-2)  weight is added to `total_weight_rejected` only if `block.responded_signers.insert(slot_id)` returns `true` (i.e., first time this slot appears in `responded_signers` at all, from either message kind).

Because `responded_signers` is shared but `gathered_signatures` is not checked by the reject path (and vice versa), the two orderings are asymmetric:
- Accept-then-Reject: `responded_signers` already contains the slot from the accept, so the later reject correctly does **not** add rejected weight — the exclusivity guard works in this direction.
- Reject-then-Accept: the reject inserts the slot into `responded_signers` and adds rejected weight. The later accept only checks `gathered_signatures` (untouched by the reject path), finds the slot absent, and adds *approved* weight as well.

This means a signer flipping its vote from Reject to Accept — an entirely expected, protocol-sanctioned behavior on the signer side, since `BlockInfo::check_state` explicitly allows `LocallyRejected -> LocallyAccepted` "re-evaluated" transitions [4](#0-3)  — causes its weight to be permanently retained in `total_weight_rejected` while simultaneously being added to `total_weight_approved`. The two tallies are supposed to partition the signer set (each signer's weight should count toward at most one bucket at any time), but this invariant is broken.

### Impact Explanation
This breaks the "aggregated-weight vs verified-accepts" equality that `signer_coordinator.rs` depends on to make its accept/reject decision for a proposed block: [5](#0-4) . Concretely:
- `total_weight_rejected.saturating_add(weight_threshold) > total_weight` can be satisfied using stale rejection weight from a signer who has since accepted, causing the miner to spuriously treat the block as rejected (`SignersRejected`) and permanently/temporarily ban transactions based on `blocking_minority` weight that no longer reflects the signer's live vote [6](#0-5) .
- More generally, `total_weight_approved + total_weight_rejected` can exceed `total_weight`, meaning the miner's view of the vote is no longer a true partition, undermining the correctness of both the "≥70% approve" and ">30% reject" thresholds that gate block broadcast/tx-exclusion decisions.

This is a liveness/tx-censorship-adjacent inconsistency in the miner's tallying logic reachable by a single signer's ordinary vote flip (no majority collusion needed), not merely a logging cosmetic issue, since it feeds directly into the `SignersRejected` control-flow branch that excludes transactions from future proposals.

### Likelihood Explanation
Any single signer sending a `Rejected` message followed by an `Accepted` message for the same `signer_signature_hash` triggers this — which is a normal occurrence given the documented pre-commit re-evaluation flow where a signer can move from `LocallyRejected` back to `LocallyAccepted` after a later pre-commit/re-proposal round (see `docs/signer-flows.md` state diagram, section 2). It requires no majority, no cryptographic break, and no elevated access — just ordinary gossip traffic timing.

### Recommendation
Track a single per-slot "current disposition" (Accepted/Rejected) instead of two independently-maintained dedupe sets. When a `Rejected` arrives for a slot already in `gathered_signatures`, or an `Accepted` arrives for a slot already in `responded_signers` as a rejection, the previous weight contribution must be subtracted from the old bucket before being added to the new one (or the vote should simply be ignored/superseded consistently), so that `total_weight_approved` and `total_weight_rejected` always remain disjoint w.r.t. slot weight.

### Proof of Concept
1. Miner proposes block `B`; coordinator opens `BlockStatus` for `B` with `total_weight_approved = total_weight_rejected = 0`.
2. Signer `S` (weight `w`) sends `BlockResponse::Rejected` for `B`. `responded_signers.insert(S.slot)` → true → `total_weight_rejected += w`.
3. Signer `S` later re-evaluates (e.g., after the conflicting sibling it initially rejected against goes stale, per the pre-commit re-evaluation flow) and sends `BlockResponse::Accepted` for the same `B`. `gathered_signatures.contains_key(S.slot)` is `false` (never touched by the reject path) → `total_weight_approved += w`.
4. Now `total_weight_rejected` still includes `w` from step 2, and `total_weight_approved` also includes `w` from step 3 — `S`'s weight counts in both buckets simultaneously, and `total_weight_approved + total_weight_rejected` can exceed `total_weight` if other signers have also voted, corrupting the threshold checks in `signer_coordinator.rs`.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-546)
```rust
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
