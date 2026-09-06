### Title
Stale rejection weight is never cleared when a signer flips Reject → Accept for the same block, letting `total_weight_rejected` and `total_weight_approved` double‑count a signer's weight - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
The node-side `StackerDBListener` aggregates per-signer `BlockResponse` messages into a `BlockStatus{total_weight_approved, total_weight_rejected, ...}` used by `SignerCoordinator::get_block_status` to decide whether a block is approved (≥ threshold) or globally rejected (rejections make threshold unreachable). The dedup guard used on the "Accepted" path checks `gathered_signatures` (accept-only) rather than `responded_signers` (accept+reject), so a signer who first rejects and later legitimately re-evaluates and accepts the same block has their weight added into `total_weight_approved` **without ever being subtracted from `total_weight_rejected`**. This is the same class of bug as `pointsSum.slope` not being decremented on `removeNominee`: a per-vote counter is updated for the "new" state of a voter without ever compensating the counter that reflected their "old" state, breaking the invariant `total_weight_approved + total_weight_rejected ≤ total_weight`.

### Finding Description
- `stacks-signer`'s own state machine explicitly allows a `LocallyRejected → LocallyAccepted` re-evaluation for the same block (as documented at `docs/signer-flows.md:144` and enforced by `BlockInfo::check_state`), [1](#0-0) . This means a signer can broadcast `BlockResponse::Rejected` and later, for the very same `signer_signature_hash`, broadcast `BlockResponse::Accepted`.
- On the node side, `StackerDBListener`'s message loop processes `Rejected` by checking membership in `responded_signers` (a set shared by both accept and reject paths) before adding weight: [2](#0-1) 
- But it processes `Accepted` by checking membership in `gathered_signatures` (an accept-only map) before adding weight, and unconditionally inserts into `responded_signers` afterward: [3](#0-2) 
- Because a signer who previously rejected is present in `responded_signers` but absent from `gathered_signatures`, a subsequent `Accepted` message from that same signer passes the `!block.gathered_signatures.contains_key(&slot_id)` check and adds `signer_entry.weight` to `block.total_weight_approved` — while `block.total_weight_rejected` retains the weight that was added earlier for that same signer's rejection. Nothing in this code path subtracts the stale rejection weight.
- The mirror direction (Accept → Reject) is correctly guarded: `responded_signers.insert(slot_id)` in the reject handler returns `false` for an already-responded signer, so no double count happens in that direction. The asymmetry is exactly analogous to the Olas report: one path (`voteForNomineeWeights`/here, "Accepted") updates the sum, the other removal/negative path (`removeNominee`/here, "Rejected"→"Accepted" transition) fails to compensate the previously-added contribution.
- `BlockStatus.total_weight_rejected` is only reset via `StackerDBListenerComms::reset_rejections`, which runs solely on the coordinator's rejection-timeout path: [4](#0-3) 
This reset does not run on every poll — it fires only after `rejections_timer.elapsed() > rejections_timeout`, so the double-counted state can persist and be observed by the coordinator well before any reset occurs.

### Impact Explanation
`SignerCoordinator::get_block_status` evaluates the **rejection** condition before the **acceptance** condition on every iteration: [5](#0-4) 
Because a flipped signer's weight is simultaneously present in both `total_weight_rejected` (stale) and `total_weight_approved` (fresh), the sum of the two counters can exceed `self.total_weight`. If `total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight` becomes true due to this stale contribution — even though the actual live rejecting weight is below the 30% blocking minority and the block has, or is about to, reach the 70% acceptance threshold — the miner incorrectly returns `NakamotoNodeError::SignersRejected`, discarding a block that legitimately reached (or would reach) consensus. This can also trigger `permanently_excluded_txids`/`temporarily_excluded_txids` bans on transactions based on an inflated "blocking minority" weight that doesn't actually exist anymore, since `failed_txids` weight accounting under `RejectCode::ValidationFailed` also derives from this inflated per-signer rejection state. The net effect is a liveness degradation: valid, canonical blocks/transactions can be wrongly treated as globally rejected due to accounting drift after a normal (in-protocol) signer vote change, matching the "signer wedged/miscounted response breaking the accept/reject equality" impact class.

### Likelihood Explanation
This requires only a single signer (not a majority) to exhibit the state-machine-sanctioned `LocallyRejected → LocallyAccepted` transition for one block and broadcast both responses — a transition explicitly documented and supported by `stacks-signer`'s own logic (re-evaluation on updated chainstate/pre-commit threshold). No forged signatures, no majority collusion, and no access to another signer's key are needed; the flip can occur naturally in normal operation whenever a signer's local view changes between the two responses (e.g., initial rejection due to a stale chain view or timeout, later corrected once the proposal validates or a pre-commit threshold is reached). A signer could also trigger this deliberately by re-sending both response types for the same block hash it controls.

### Recommendation
In the `Accepted` handling branch of the StackerDB listener's message loop, before crediting `total_weight_approved`, check whether the signer previously contributed rejection weight (i.e., was present in `responded_signers` without being in `gathered_signatures`) and if so, subtract that signer's weight from `total_weight_rejected` at the same time the new weight is credited to `total_weight_approved`. Equivalently, track per-signer vote kind (accept/reject) explicitly and recompute `total_weight_approved`/`total_weight_rejected` from that per-signer map rather than incrementally, so any vote change is naturally reconciled instead of stacking two contributions from the same signer.

### Proof of Concept
1. Node proposes a block with `signer_signature_hash = H`; `weight_threshold` is 70% of `total_weight`; assume signer `S` has weight `w`, and other signers currently produce `total_weight_rejected = R` with `R + weight_threshold ≤ total_weight` (not yet a blocking minority) and `total_weight_approved = A` with `A < weight_threshold`.
2. Signer `S` sends `BlockResponse::Rejected` for `H` (e.g., due to a transient state, such as a not-yet-observed pre-commit or stale chain tip). `stackerdb_listener.rs:515-518` adds `S`'s weight `w` to `total_weight_rejected`, now `R' = R + w`, and inserts `S`'s slot into `responded_signers`.
3. `S`'s local signer later re-evaluates per `BlockInfo::check_state` (`LocallyRejected → LocallyAccepted` is a valid transition) and sends `BlockResponse::Accepted` for the same `H` — e.g., because the pre-commit threshold was reached or block conditions changed, per `stacks-signer/src/v0/signer.rs` (`store_and_process_block_signature`, `mark_locally_accepted`).
4. On the node, the `Accepted` handler at `stackerdb_listener.rs:443-465` checks `gathered_signatures.contains_key(&slot_id)` — false, since `S` never had an accepted signature stored — so it adds `w` to `total_weight_approved`, giving `A' = A + w`. `total_weight_rejected` is left at `R' = R + w` (never decremented).
5. Now `total_weight_approved + total_weight_rejected = A' + R' = A + R + 2w`, exceeding the true weight distribution by `w`. If enough other signers respond such that `R' + weight_threshold > total_weight` (a condition made easier to reach purely because of the phantom extra `w` in `R'`), `SignerCoordinator::get_block_status` (`signer_coordinator.rs:509-519`) returns `NakamotoNodeError::SignersRejected` even though the live rejecting weight (excluding the phantom double count) may be below the 30% blocking minority and the block may have genuinely reached (or been about to reach) the 70% acceptance threshold via `total_weight_approved`.

### Citations

**File:** stacks-signer/src/signerdb.rs (L319-328)
```rust
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
```

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
