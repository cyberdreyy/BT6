### Title
Stale rejection weight is never retracted when a signer flips to Accept, letting `total_weight_rejected` and `total_weight_approved` double-count the same signer and falsely trigger global rejection of a properly-approved block - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
This is the same bug class as the reported "dust loss should be capped" finding: an aggregator sums per-item contributions without reconciling them against a value that has already changed, so the total no longer reflects reality. Here, the aggregator is `BlockStatus.total_weight_rejected` / `total_weight_approved` in the miner-side StackerDB listener, and the "item" is a signer's vote weight, which is added to the rejection bucket and never removed even after the same signer legitimately accepts the block.

### Finding Description
`BlockStatus` tracks both `total_weight_approved` and `total_weight_rejected` as independent running sums, gated by a single shared `responded_signers` set that is supposed to prevent a signer's weight from being counted twice.

In the `Rejected` branch, the weight is only added if the signer's slot is being seen for the first time in `responded_signers`: [1](#0-0) 

But in the `Accepted` branch, the weight is added based solely on whether the slot already has a **signature** recorded (`gathered_signatures`), never checking `responded_signers`: [2](#0-1) [3](#0-2) 

So the guard is asymmetric:
- Accept-then-Reject: `responded_signers` already contains the slot from the Accept, so the later Reject's `insert()` returns `false` and its weight is correctly *not* added to `total_weight_rejected`.
- Reject-then-Accept: the Reject adds weight to `total_weight_rejected` and marks the slot in `responded_signers`. The later Accept does not consult `responded_signers` at all — it only checks `gathered_signatures`, which is empty for a first-time signature — so the signer's weight is *also* added to `total_weight_approved`.

The result: a signer who rejects and then (legitimately, after re-evaluation) signs is counted with their full weight in **both** buckets simultaneously. The stale rejection weight is never retracted from `total_weight_rejected`.

This exact class of bug — "do not count both a block acceptance and a block rejection for the same signer/block" — is called out as a fix in the project's own changelog: [4](#0-3) 

That changelog entry documents the fix being applied to the *signer's own* local vote-tallying logic (`stacks-signer/src/v0/signer.rs` / `signerdb.rs`), but the analogous accounting inside the **miner/node-side** `StackerDBListener` (`stacks-node/src/nakamoto_node/stackerdb_listener.rs`), which the block-producing node itself uses in `signer_coordinator.rs` to decide whether to treat a block as accepted or rejected, does not apply the same reconciliation — it derives `total_weight_rejected` incrementally and never decrements it when the same signer later accepts.

### Impact Explanation
`signer_coordinator.rs::get_block_status` uses these two sums directly to decide the fate of a proposed block: [5](#0-4) [6](#0-5) 

If `total_weight_rejected` retains stale weight from signers who have since flipped to Accept, `total_weight_rejected + weight_threshold > total_weight` can become true even though a supermajority (by weight) of signers currently endorse the block with valid signatures. This causes the coordinator to declare `SignersRejected` and abandon a block that in fact commanded enough weight to be validly signed — a liveness wedge for that tenure (the miner discards a signable block and must re-propose), and more importantly it demonstrates that the aggregated weight used for the accept/reject decision does not equal the weight of currently-valid accept/reject responses, breaking the "aggregated-weight vs verified-accepts" invariant this analysis is scoped to check. Depending on timing, this can also delay legitimate block finalization across an entire signer set without requiring any signer to act maliciously — a single honest signer changing its mind (e.g., re-evaluating after a chainstate recheck, as `stacks-signer/src/v0/signer.rs`'s pre-commit/response flow explicitly allows) is enough to trigger it.

### Likelihood Explanation
This requires only one signer, acting completely within protocol (rejecting a proposal and later re-evaluating and accepting it — a flow the codebase explicitly supports, e.g. `should_reevaluate_block`/`should_reevaluate_reject_reason` described in `docs/signer-flows.md`), and no majority collusion. It is entirely plausible in normal operation (e.g., a signer initially rejects due to a transient chainstate mismatch, then a re-proposal or pre-commit-threshold recheck flips it to Accept).

### Recommendation
When processing a `BlockResponse::Accepted` message for a slot that is already present in `responded_signers` due to a prior `Rejected` message, subtract that signer's weight from `total_weight_rejected` (and vice versa if a `Rejected` arrives after an `Accepted`, though the existing `responded_signers` check already blocks that path). Concretely, track a per-slot "current vote" (accept/reject) rather than two independently-incrementing sums, and derive `total_weight_approved`/`total_weight_rejected` from that map so a flipped vote is fully reconciled in both directions, mirroring the fix already applied to the signer's own local tally per the CHANGELOG entry.

### Proof of Concept
1. Node proposes block B to N signers with weight thresholds such that reject-threshold = weight_threshold' (>30%).
2. Signer S (weight w) sends `BlockResponse::Rejected` for B. `stackerdb_listener.rs` line 516-518 adds `w` to `total_weight_rejected` and inserts S's slot into `responded_signers`.
3. Enough other signers (weight ≥ total_weight - weight_threshold - w) also reject, but not yet crossing the reject threshold.
4. Signer S re-evaluates (e.g., after a repropose or after clearing a transient condition) and sends `BlockResponse::Accepted` for the same B, with a valid signature.
5. In the `Accepted` branch (lines 443-465), since `gathered_signatures` does not yet contain S's slot, `total_weight_approved` is incremented by `w` — but `total_weight_rejected` still contains the stale `w` from step 2, never decremented.
6. Now `total_weight_rejected` (still including S's stale weight) plus other late rejecters can cross `> total_weight - weight_threshold` in `signer_coordinator.rs` line 509-513, causing `SignersRejected` to be returned even though the *current* signer weight set (including S's now-valid signature) would meet or exceed `weight_threshold` for acceptance. [7](#0-6) [1](#0-0) [5](#0-4) 

**Note on confidence**: I was unable to fully confirm within the available tool budget whether `stacks-signer/src/signerdb.rs`'s own `add_block_signature`/`add_block_rejection_signer_addr` functions (referenced in the CHANGELOG fix) implement cross-bucket reconciliation that might be mirrored elsewhere, nor could I retrieve git history for `stackerdb_listener.rs` (the repository only exposes a single squashed "Initial commit"), so I cannot verify whether this asymmetry is a known-accepted design choice versus an unintentional omission of the CHANGELOG-documented fix in this specific node-side file. A Devin session with full repository/history access would be needed to confirm intent and check for any other reconciliation path guarding `signer_coordinator.rs`'s consumption of `BlockStatus`.

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

**File:** stacks-signer/CHANGELOG.md (L132-135)
```markdown
### Changed

- Do not count both a block acceptance and a block rejection for the same signer/block. Also ignore repeated responses (mainly for logging purposes).
- Database schema updated to version 16
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-519)
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
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L541-545)
```rust
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```
