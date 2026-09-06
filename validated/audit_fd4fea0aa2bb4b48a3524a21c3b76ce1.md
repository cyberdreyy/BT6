### Title
Miner's rejection tally double-counts a signer's weight after a legitimate reject→accept re-evaluation, causing premature abandonment of a validly-signable block - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener::run` (in `stacks-node/src/nakamoto_node/stackerdb_listener.rs`) maintains two independent weight tallies per proposed block, `total_weight_approved` and `total_weight_rejected`, that are supposed to be mutually exclusive per signer slot. The guard used to gate the *rejection* increment shares the same `responded_signers` set that the *acceptance* branch also populates, but the acceptance branch gates its own increment on a different set (`gathered_signatures`). This asymmetry lets a signer's weight be credited to both tallies when the signer legitimately re-evaluates a block from `LocallyRejected` to `LocallyAccepted` — a state transition explicitly permitted by `BlockInfo::check_state` in `stacks-signer/src/signerdb.rs` (`LocallyRejected --> LocallyAccepted`, "re-evaluated").

### Finding Description
Acceptance handling: [1](#0-0) 
adds `signer_entry.weight` to `block.total_weight_approved` only if `!block.gathered_signatures.contains_key(&slot_id)`, then always does `block.gathered_signatures.insert(slot_id, signature)` and `block.responded_signers.insert(slot_id)`.

Rejection handling: [2](#0-1) 
adds `signer_entry.weight` to `block.total_weight_rejected` only if `block.responded_signers.insert(slot_id)` returns `true` (i.e., the slot had not previously "responded" at all, whether by accept or reject).

Because these two guards use different backing sets:

- Reject → Accept order: a signer first rejects (adds weight to `total_weight_rejected`, inserts `slot_id` into `responded_signers`). Later, the same signer re-evaluates and accepts (the acceptance guard only checks `gathered_signatures`, which is still empty for this slot) — its weight is *also* added to `total_weight_approved`. The prior contribution to `total_weight_rejected` is never removed.
- Accept → Reject order is correctly guarded (the second `responded_signers.insert` returns `false`, so no double increment occurs), but this means the earlier accept-guard fix only protects one direction, exposing the asymmetry.

The reject→accept sequence is not a contrived edge case: it is the documented normal flow for a signer that re-evaluates a previously rejected block once its chainstate view catches up (see `docs/signer-flows.md`, block lifecycle: `LocallyRejected --> LocallyAccepted : re-evaluated`), and `BlockInfo::check_state` explicitly allows this transition: [3](#0-2) 

This is the same bug class as the referenced first-depositor report: a value that should only be "spent" once (a signer's weight, analogous to a depositor's shares) is credited into two supposedly-exclusive running totals because the guard conditions on the write path are not kept consistent with each other, corrupting an invariant the caller relies on (`total_weight_approved + total_weight_rejected <= total_weight`, one distinct-signer contribution per bucket).

### Impact Explanation
The miner's `SignerCoordinator::wait_for_signer_signatures_with_retry` in `stacks-node/src/nakamoto_node/signer_coordinator.rs` uses `block_status.total_weight_rejected` to decide the block cannot reach consensus: [4](#0-3) 
Because `total_weight_rejected` can retain a stale contribution from a signer who has *since* accepted the block, this rejection total can be artificially inflated relative to the real number of currently-rejecting signers. This can push `total_weight_rejected` over the "blocking minority"/failure threshold and cause the miner to abort/`SignersRejected` a block that in reality has (or could reach) sufficient approving weight — a liveness wedge on block production that requires no majority collusion, only a single signer's ordinary reject→accept re-evaluation, which the signer state machine explicitly supports.

### Likelihood Explanation
High: reject→accept transitions happen routinely whenever a signer's local chainstate view initially disagrees with a proposal (e.g., due to a temporarily-stale sortition view) and then catches up, which is exactly the scenario the `should_reevaluate_reject_reason` / re-evaluation logic in `stacks-signer/src/v0/signer.rs` is designed to handle. No attacker collusion or majority is needed — a single normally-operating signer flipping its vote once is sufficient to introduce the double count.

### Recommendation
Use a single, shared per-slot "final answer" bookkeeping structure (or an enum `Accepted(weight)/Rejected(weight)`) so that adding a signer's weight to one tally decrements/clears any previous contribution to the other tally for that same slot, keeping the invariant that each signer's weight can only ever be present in exactly one of `total_weight_approved` or `total_weight_rejected` at a time. Concretely, gate both the acceptance and rejection weight increments on the same `responded_signers`-style set, and when a slot's vote flips, subtract the weight from the old bucket before adding it to the new one.

### Proof of Concept
1. Miner submits `BlockProposal` for block `B`, tracked by `StackerDBListener` with `total_weight = 100`, `weight_threshold = 70`.
2. Signer `S` (weight 10) sends `BlockResponse::Rejected` for `B`: `responded_signers.insert(S) == true` → `total_weight_rejected = 10`.
3. Signer `S` re-evaluates (its local view catches up) and later legitimately sends `BlockResponse::Accepted` for `B`: `gathered_signatures.contains_key(S) == false` → `total_weight_approved += 10 = 10`. `responded_signers.insert(S)` is called again but the value is unused for gating on the accept path.
4. Now `total_weight_approved (10) + total_weight_rejected (10) = 20 > 10`, i.e., signer `S`'s weight of 10 is counted in both buckets even though `S` has a single current vote (accept). Repeating this with enough re-evaluating signers inflates `total_weight_rejected` past `total_weight - weight_threshold`, tripping the `SignersRejected` bail-out in `signer_coordinator.rs` even though real approving weight is/would be sufficient.

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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L500-522)
```rust
                                    "rejections" => rejections,
                                    "rejections_timeout" => rejections_timeout.as_secs(),
                                    "rejections_step" => rejections_step,
                                    "rejections_threshold" => self.total_weight.saturating_sub(self.weight_threshold));

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
```
