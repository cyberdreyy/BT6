### Title
Stale rejection weight is never retracted when a signer flips Rejected→Accepted, letting `total_weight_rejected` and `total_weight_approved` double-count the same signer - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener` tallies each block's approval and rejection weight into two independent accumulators, `total_weight_approved`/`gathered_signatures` and `total_weight_rejected`/`responded_signers`. The guard that prevents double counting is asymmetric: an `Accepted` message, once seen, blocks a later `Rejected` from re-adding weight (because `Accepted` also inserts into `responded_signers`), but a `Rejected` message does **not** block a later `Accepted` from adding weight again, nor does it retract the previously counted rejection weight. A single signer legitimately re-evaluating from local rejection to local acceptance (a transition explicitly supported by the signer state machine) therefore has its weight counted in both `total_weight_rejected` and `total_weight_approved` at once.

### Finding Description
The listener keeps two separate tallies per block, guarded by two different sets:

- `Accepted` path (`stackerdb_listener.rs` lines 443-465): weight is added to `total_weight_approved` only `if !block.gathered_signatures.contains_key(&slot_id)`; it then unconditionally does `block.gathered_signatures.insert(slot_id, signature)` **and** `block.responded_signers.insert(slot_id)`. [1](#0-0) 

- `Rejected` path (lines 515-518): weight is added to `total_weight_rejected` only `if block.responded_signers.insert(slot_id)` returns `true` (i.e., first time this slot appears in `responded_signers`). [2](#0-1) 

Because `Accepted` populates `responded_signers` too, an Accept-then-Reject sequence from the same signer is correctly guarded (the later Reject's `responded_signers.insert` returns `false`, so it can't add weight). But the reverse — Reject-then-Accept — is **not** guarded: the Reject only touched `responded_signers`, never `gathered_signatures`. When the same signer later sends `Accepted`, the check `!block.gathered_signatures.contains_key(&slot_id)` is still `true`, so `total_weight_approved` gets that signer's weight added — while `total_weight_rejected` still retains the earlier weight from the same signer, since nothing ever decrements it (the only place `total_weight_rejected` is reset is `reset_rejections`, which runs solely on a proposal timeout, and its own comment explains that `gathered_signatures`/approvals are deliberately *never* cleared, but says nothing about clearing stale rejection weight on a legitimate vote flip). [3](#0-2) 

This vote-flip path is not a corner case invented for this analysis — the signer's own local state machine explicitly allows and models it: `LocallyRejected --> LocallyAccepted : re-evaluated`, documented as a normal transition in the block lifecycle (`BlockInfo::check_state`, `move_to`). [4](#0-3) [5](#0-4) 

The coordinator that consumes these two tallies checks rejection **before** approval:
```
if block_status.total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight { … reject … }
else if block_status.total_weight_approved >= self.weight_threshold { … accept … }
``` [6](#0-5) 

So stale rejection weight from a signer who has since switched to accept is still fully "live" in `total_weight_rejected` and can push the rejection check over the 30% blocking-minority line even though that signer's *current* vote is Accept — an equality break between "weight the coordinator believes has rejected" and "weight of signers who currently, actually reject" (aggregated-weight vs. verified-accepts mismatch called out in scope).

### Impact Explanation
This lets the miner/coordinator treat a block as globally rejected (dropping it, excluding its transactions, moving on) even when the live signer set actually supports it at or above the 70% approval threshold, because one signer's earlier, superseded rejection weight is never retracted from the rejection tally. This is a liveness wedge on otherwise-valid blocks: the coordinator can spuriously declare `SignersRejected` for a block that in fact has enough current approvals, stalling block production/propagation for that slot and needlessly excluding transactions as "problematic." It requires no majority — only one signer flipping its vote in the normal, supported way, combined with genuine rejectors close to (but below) the 30% line, to tip the (stale) sum over threshold.

### Likelihood Explanation
Reachable purely through the normal, supported re-evaluation path (`LocallyRejected → LocallyAccepted`) that any single honest signer can traverse when new information (e.g., a later-observed pre-commit, or the reject reason becoming stale) changes its verdict — this is exactly the kind of re-evaluation the signer protocol is built to support (`should_reevaluate_block`, `should_reevaluate_reject_reason`). It requires only StackerDB message delivery (in scope), not a majority, not another signer's key, and not any node consensus flaw as the root cause — the flaw is purely in the coordinator/listener's weight-bookkeeping.

### Recommendation
Track a single "current vote" (with weight) per slot rather than two independently-guarded sets. When a signer's message flips their status (Reject→Accept or vice versa), retract the previous contribution before adding the new one — e.g., replace `responded_signers`/`gathered_signatures` bookkeeping with a per-slot `enum Vote { Approved(sig), Rejected(reason) }` map, recomputing `total_weight_approved`/`total_weight_rejected` as sums over current votes rather than incrementally, or explicitly subtracting the stale weight from `total_weight_rejected` when an `Accepted` is recorded for a slot previously counted as rejected.

### Proof of Concept
1. Coordinator is waiting on signer weight for a block with `weight_threshold` = 70% of `total_weight`.
2. Genuine rejectors accumulate 29% of weight via `Rejected` messages (each adds to `total_weight_rejected` via the `responded_signers.insert` guard, lines 515-518).
3. One additional signer S with weight `w` (say 2%) — having validated the block and hit a transient/re-evaluable rejection reason — first sends `Rejected`; `responded_signers.insert(S)` succeeds, `total_weight_rejected += w` (now 31%).
4. S re-evaluates per the documented `LocallyRejected → LocallyAccepted` transition and later sends `Accepted` for the same block hash. In the listener, `gathered_signatures.contains_key(&S)` is `false` (S never sent Accepted before), so `total_weight_approved += w` is added (lines 443-446) — but `total_weight_rejected` is never decremented; it remains at 31%.
5. Meanwhile, ≥70% (including S) legitimately send `Accepted`, so `total_weight_approved` also reaches/exceeds `weight_threshold`.
6. In `wait_for_signer_signatures_or_timeout` (`signer_coordinator.rs`), the rejection branch is evaluated first: `total_weight_rejected (31%) + weight_threshold (70%) > total_weight (100%)` is true, so the coordinator returns `NakamotoNodeError::SignersRejected`, discarding a block that in fact has ≥70% *current* approving weight.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L706-722)
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
```

**File:** docs/signer-flows.md (L141-149)
```markdown
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

**File:** stacks-signer/src/signerdb.rs (L313-341)
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

    /// Attempt to transition the block state
    pub fn move_to(&mut self, state: BlockState) -> Result<(), String> {
        if !self.check_state(state) {
            return Err(format!(
                "Invalid state transition from {} to {state}",
                self.state
            ));
        }
        self.state = state;
        Ok(())
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
