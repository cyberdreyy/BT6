Confirmed: there's a genuine weight-double-counting bug in the node-side StackerDB listener that fits the "rejection recounted as an acceptance" bug class from the H-27 analog.

### Title
Signer weight double-counted across accept/reject tallies after a vote flip - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
The `SignerCoordinator`'s in-memory `BlockStatus` tally (used by the block-proposing node/miner to decide when a block has enough signer weight to broadcast, or too much rejection weight to give up) tracks `total_weight_approved` and `total_weight_rejected` with two different, non-overlapping dedup guards. When a single signer legitimately changes its vote from `Rejected` to `Accepted` for the same `signer_signature_hash` (which the signer-side state machine explicitly permits for reconsiderable reject reasons), that signer's weight ends up counted in *both* totals simultaneously, because the reject-side guard is never invalidated by a later accept.

### Finding Description
In the message-processing loop, the `Accepted` branch guards adding weight to `total_weight_approved` using `block.gathered_signatures.contains_key(&slot_id)`, and separately, unconditionally inserts `slot_id` into `block.responded_signers`: [1](#0-0) 

The `Rejected` branch guards adding weight to `total_weight_rejected` using `block.responded_signers.insert(slot_id)`, which returns `true`/adds weight only if `slot_id` was not already present: [2](#0-1) 

Sequence that breaks the invariant "each signer's weight counts in at most one of the two totals":
1. Signer sends `Rejected` first. `responded_signers.insert(slot_id)` succeeds (not yet present) → `total_weight_rejected += weight`, and `slot_id` is now recorded in `responded_signers`.
2. The block proposer re-issues the identical block (same `signer_signature_hash`) later, and per the signer-side rules the signer is permitted to reconsider certain reject reasons and switch to `Accepted` for that same hash (documented explicitly in `handle_block_pre_commit`'s comment: "we do not change our votes on rejected blocks unless we receive a new block proposal for it and the reject reason allows us to reconsider"): [3](#0-2) 
3. The `Accepted` message arrives at the coordinator. Its guard only checks `gathered_signatures.contains_key(&slot_id)` — which is `false` because this slot has never produced an accept signature before — so `total_weight_approved += weight` fires, even though `responded_signers` (and therefore the earlier reject weight) already contains this slot.

The reject-side guard (`responded_signers`) is shared state that is written by both branches but only consulted for de-duplication on the reject side; nothing clears or reconciles it when a slot later accepts. The result: the same signer's weight is now present in both `total_weight_approved` and `total_weight_rejected` for the same block, permanently, for the lifetime of that `BlockStatus` entry.

This is the direct analog of the H-27 pattern: the tally uses a mutable/overlapping accounting bucket (`responded_signers`, akin to a spendable balance) instead of an authoritative, exclusive per-signer decision record, letting one action's effect leak into the count for a different action.

### Impact Explanation
`total_weight_rejected` is used to decide when to give up on a block: `total_weight_rejected.saturating_add(weight_threshold) > total_weight` triggers `SignersRejected` and aborts the mining attempt for that block. [4](#0-3)  Because the flipped signer's weight is never removed from `total_weight_rejected` even after it accepts, the rejection tally is inflated relative to genuinely-still-rejecting weight. This can push the coordinator into declaring `SignersRejected` (excluding transactions, aborting the proposal) using overlapping/stale weight that in reality has already flipped to approve — a false-negative that wedges block production for that attempt and forces retries, a liveness impact on the miner's ability to make progress with an otherwise-approved block.

Symmetrically, `total_weight_approved >= weight_threshold` triggers broadcasting the signatures collected in `gathered_signatures`. [5](#0-4)  Since the inflated `total_weight_approved` counter can cross the threshold sooner than the real number of distinct never-rejected acceptances would justify, the miner may believe consensus was reached and broadcast prematurely; the block itself is still re-validated downstream by `verify_signer_signatures`, which recomputes weight strictly from the signatures actually present in the header, so an outright invalid block is not accepted network-wide. [6](#0-5)  The primary, reliably-triggerable consequence is therefore a liveness wedge/false-abort via the rejection path, not an invalid block being finalized.

### Likelihood Explanation
Triggering requires only a single signer (one slot) changing its own vote on a re-proposed identical block, a scenario the signer-side logic explicitly anticipates and allows for certain reject reasons, and standard StackerDB gossip delivering both messages to the coordinator — no majority collusion or key compromise needed.

### Recommendation
Track each signer's final decision exclusively (e.g., a single `HashMap<slot_id, Accepted|Rejected>` or by removing the signer's weight from `total_weight_rejected` whenever it later moves to `gathered_signatures`) so that a slot's weight is reconciled to belong to at most one of `total_weight_approved` / `total_weight_rejected` at any time, mirroring the recommendation in H-27 to use an authoritative single source of truth instead of a value that can be independently mutated by two different code paths.

### Proof of Concept
1. Node proposes block B with `signer_signature_hash = H`.
2. Signer S (weight w) validates B, determines it is invalid for a reconsiderable reason, and broadcasts `BlockResponse::Rejected(H, ...)`. The coordinator's `stackerdb_listener` records `total_weight_rejected += w` and `responded_signers.insert(slot_S)`.
3. The proposer re-issues the same block B (same `H`) — e.g., after conditions change — and S's signer-side logic reconsiders and produces `BlockResponse::Accepted(H, signature)` per the "reject reason allows us to reconsider" path in `handle_block_pre_commit`.
4. The coordinator receives the `Accepted` message; `gathered_signatures` does not yet contain `slot_S`, so `total_weight_approved += w` fires unconditionally.
5. Now both `total_weight_approved` and `total_weight_rejected` include S's weight `w` for the same block `H`, violating the intended invariant `total_weight_approved + total_weight_rejected <= total_weight` attributable to distinct signers, and can cause a spurious `SignersRejected` abort (or a premature-threshold broadcast) depending on arrival order and other signers' votes.

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

**File:** stacks-signer/src/v0/signer.rs (L1323-1331)
```rust
        if !block_info.valid.unwrap_or(false) {
            // We received a pre-commit for a block that we have not validated or we have already marked this block as invalid.
            // We should not do anything further as we do not know what our response should be and we do not change our votes on rejected
            // blocks unless we receive a new block proposal for it and the reject reason allows us to reconsider.
            debug!(
                "{self}: Received a pre-commit for a block that we have not determined to be valid: {:?}. Doing nothing...", block_info.valid
            );
            return;
        }
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-518)
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
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L541-545)
```rust
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1175-1189)
```rust
            total_weight_signed = total_weight_signed
                .checked_add(signer.weight)
                .expect("FATAL: overflow while computing signer set threshold");
        }

        let threshold = Self::compute_voting_weight_threshold(total_weight)?;

        if total_weight_signed < threshold {
            return Err(ChainstateError::InvalidStacksBlock(format!(
                "Not enough signatures. Needed at least {} but got {} (out of {})",
                threshold, total_weight_signed, total_weight,
            )));
        }

        return Ok(total_weight_signed);
```
