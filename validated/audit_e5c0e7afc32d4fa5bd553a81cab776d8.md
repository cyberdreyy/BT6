### Title
Reject-then-Accept sequence from a single signer double-counts weight across `total_weight_rejected` and `total_weight_approved` in `StackerDBListener`, letting the miner declare a validly-signed block globally rejected - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener` (the node-side signer-response aggregator) tallies each signer's `BlockResponse` into one of two independent counters, `total_weight_approved` and `total_weight_rejected`, per `BlockStatus`. The guard that prevents double counting on the acceptance path checks a different set (`gathered_signatures`) than the one used on the rejection path (`responded_signers`), so a signer who first rejects a block and later accepts the *same* block (same `signer_signature_hash`) has its weight counted in **both** buckets. This breaks the invariant that `total_weight_approved + total_weight_rejected` reflects distinct signer weight, and can cause `SignerCoordinator::get_block_status` to declare a block globally rejected via stale rejection weight even though real, valid signatures already reached the 70% threshold.

### Finding Description
In `handle_block_pre_commit`/response processing in `stackerdb_listener.rs`, the `Accepted` branch only checks whether the slot already has a stored signature before adding weight: [1](#0-0) 

```
if !block.gathered_signatures.contains_key(&slot_id) {
    block.total_weight_approved = block.total_weight_approved.saturating_add(signer_entry.weight);
    ...
}
block.gathered_signatures.insert(slot_id, signature);
block.responded_signers.insert(slot_id);
```

The `Rejected` branch instead guards on `responded_signers`: [2](#0-1) 

```
if block.responded_signers.insert(slot_id) {
    block.total_weight_rejected = block.total_weight_rejected.saturating_add(signer_entry.weight);
    ...
}
```

Because these two branches use different membership sets to decide "have I already counted this signer," the following sequence for the *same* `signer_signature_hash` produces an inconsistency:

1. Signer S sends `Rejected` first → `responded_signers` now contains S's slot → `total_weight_rejected += w_S`.
2. Signer S later sends `Accepted` for the same block hash → the accept branch checks `gathered_signatures` (not `responded_signers`), which does not yet contain S's slot → `total_weight_approved += w_S` as well.

The result: S's weight is now counted in *both* `total_weight_approved` and `total_weight_rejected`, so `total_weight_approved + total_weight_rejected` can exceed `total_weight` (or more precisely, exceeds the true set of distinct-signer weight backing each side). Note the reverse order (Accept then Reject) is correctly guarded, because the reject branch's `responded_signers.insert` will return `false` and skip the increment — the asymmetry is specifically in the Accept branch's check.

This reject-then-accept sequence is not purely hypothetical/malicious: the signer-side flow explicitly supports revisiting a previously-rejected proposal for the same block hash. The documented flow (`should_reevaluate_reject_reason`, `should_reevaluate_block`) allows a signer to re-evaluate and reverse a stale rejection when a re-proposal for the identical block arrives, meaning a single well-behaved signer can legitimately transition from "reject" to "accept" for the same `signer_signature_hash` under ordinary conditions (e.g. a stale chain-state condition clears). A malicious/byzantine signer can trivially also engineer this ordering by publishing conflicting messages via StackerDB, since `StackerDBListener` performs no cross-message consistency check — it only validates the ECDSA signature of each individual message.

### Impact Explanation
`SignerCoordinator::get_block_status` uses these two counters to decide the fate of a proposed block, checking the rejection condition *before* the approval condition: [3](#0-2) 

```
if block_status.total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight {
    ...
    return Err(NakamotoNodeError::SignersRejected { ... });
} else if block_status.total_weight_approved >= self.weight_threshold {
    ...
    return Ok(block_status.gathered_signatures.values().cloned().collect());
}
```

Because one signer's weight can be present in both buckets, `total_weight_rejected` can be pushed past the blocking-minority threshold (>30%) by stale rejection weight from a signer who has since switched to accepting, even while `total_weight_approved` has genuinely reached the 70% signing threshold from real, currently valid signatures. The rejection branch is evaluated first, so the miner incorrectly treats a block that has legitimately gathered enough valid signer signatures as globally rejected (`SignersRejected`), discarding it and potentially excluding transactions from future proposals. This is a liveness wedge for that tenure/block: a properly, sufficiently signed block is thrown away due to a stale/duplicated tally rather than the actual current vote distribution — the "aggregated-weight vs verified-accepts" equality named in scope is broken by a single signer's message ordering, requiring no majority collusion.

### Likelihood Explanation
Reachable by a single signer (one StackerDB slot) sending two `BlockResponse` messages (Rejected then Accepted) for the same `signer_signature_hash`. This can occur either (a) through legitimate protocol behavior when a rejected proposal is later re-evaluated and reversed by the signer's own state machine, or (b) trivially by a byzantine signer deliberately publishing a reject followed by an accept for the identical block hash, since `StackerDBListener` performs no check that a signer's votes for one block are self-consistent across messages — it only verifies each signature independently. No majority, no key compromise, and no auth_token access is required.

### Recommendation
Use a single, unified per-signer "current vote" tracking structure (e.g., replace independent weight accumulators with a `HashMap<slot_id, Vote>` recording the signer's latest recorded vote for that block, or always net out the effect of a prior contradictory vote before applying a new one), and derive `total_weight_approved`/`total_weight_rejected` by summing over the map rather than incrementally on each message. Concretely: before adding weight on the `Accepted` path, check (and if present, subtract) any weight already contributed by this slot via `responded_signers`/prior `Rejected` state, and vice versa for the `Rejected` path relative to `gathered_signatures`, so a signer's weight is only ever attributed to its most recent, single vote.

### Proof of Concept
1. Node proposes block B with `signer_signature_hash = H`.
2. Signer S (weight `w_S`) sends `BlockResponse::Rejected(H, ...)`. `stackerdb_listener.rs` records: `responded_signers = {S}`, `total_weight_rejected += w_S`.
3. Later, for the same H (e.g. following a re-evaluation, or a deliberately crafted message from a byzantine S), S sends `BlockResponse::Accepted(H, sig)`. Since `gathered_signatures` does not yet contain S's slot, the code executes `total_weight_approved += w_S` and inserts S into `gathered_signatures`/`responded_signers` (no-op there).
4. Now `total_weight_approved` may independently reach `weight_threshold` from other signers' genuine signatures, while `total_weight_rejected` (inflated by S's stale rejection) simultaneously crosses `total_weight - weight_threshold`.
5. In `SignerCoordinator::get_block_status`, the rejection branch is checked first and fires, returning `NakamotoNodeError::SignersRejected` for a block that in reality has enough current, valid accepting signatures — discarding a legitimately signable block. [1](#0-0) [4](#0-3) [3](#0-2)

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-522)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);

                            // Track transactions that failed validation, accumulating
                            // per-txid signer weight and whether any signer flagged
                            // the tx as genuinely problematic.
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-522)
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
```
