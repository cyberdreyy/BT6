### Title
Signer weight double-counted into both `total_weight_approved` and `total_weight_rejected` on Reject→Accept flip, breaking the aggregated-weight equality used by the mining coordinator - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`BlockStatus` in the node's StackerDB listener tracks per-block signer responses with two independent membership checks — `gathered_signatures` (a `BTreeMap<slot_id, signature>`) gates the `Accepted` path, while the shared `responded_signers` set gates the `Rejected` path. Because `Accepted` never checks `responded_signers` before crediting weight, a signer who first rejects a block and later legitimately reconsiders and accepts it (a codepath the signer already supports) gets its weight counted in *both* `total_weight_rejected` and `total_weight_approved`. This is the same bug class as the Kintsu report: a value (`staked` / here `total_weight_{approved,rejected}`) that is supposed to move exclusively along one accounting path can be mutated through an alternate path that bypasses the guard meant to keep it in sync, leaving the aggregate weight desynchronized from the real, deduplicated set of responses.

### Finding Description
`BlockStatus` is defined with independent bookkeeping: [1](#0-0) 

In the `Rejected` handler, the *only* guard against double counting is `responded_signers.insert(slot_id)`, which is a set shared with the accept path but is not consulted at all by the accept path: [2](#0-1) 

In the `Accepted` handler, the guard is instead `!block.gathered_signatures.contains_key(&slot_id)` — a completely separate map that the `Rejected` branch never touches: [3](#0-2) 

Trace the two possible orderings for the same `(block_sighash, slot_id)`:
- Accept → Reject: `gathered_signatures` gets the slot, `total_weight_approved` is credited, and `responded_signers` is also set. When the later Reject arrives, `responded_signers.insert(slot_id)` returns `false` (already present), so the reject path is skipped entirely — no double count here.
- Reject → Accept: `responded_signers.insert(slot_id)` returns `true` (first time), crediting `total_weight_rejected`. `gathered_signatures` is untouched. When the later Accept arrives, `!gathered_signatures.contains_key(&slot_id)` is `true` (nothing was ever inserted there by the reject path), so `total_weight_approved` is credited a second time for the *same signer's weight* — on top of the weight already counted in `total_weight_rejected`.

This second ordering is not a hypothetical: the signer explicitly supports withdrawing an earlier rejection and later accepting the same proposal once "some rejection reasons" are re-evaluated, per the CHANGELOG: [4](#0-3) 

The two totals feed directly into the mining `SignerCoordinator`'s pass/fail decision, which treats `total_weight_rejected` and `total_weight_approved` as if they always partition the signer set: [5](#0-4) 

Because a single signer's weight can now appear in both sums simultaneously, `total_weight_approved + total_weight_rejected` can exceed `total_weight`, breaking the aggregated-weight vs. verified-accepts equality: the sums no longer correspond to a set of distinct, current per-signer positions.

### Impact Explanation
This directly matches the in-scope "aggregated-weight vs verified-accepts" equality break. A single signer reconsidering (Reject→Accept) inflates `total_weight_approved` by that signer's weight beyond what the true, current set of accepting signers would produce, while the stale rejection weight also remains counted. In marginal-weight scenarios this can let the coordinator believe the 70% acceptance threshold is reached with fewer genuinely-accepting distinct signers than required, or conversely keep an already-abandoned rejection weight "alive" toward the blocking-minority (>30%) rejection threshold after the signer withdrew it — either miscounted outcome corrupts a threshold decision that is meant to reflect exact aggregate stake weight. This falls under the specified High/Critical impact bucket of a rejection/acceptance being miscounted by the coordinator's accounting.

### Likelihood Explanation
This requires only a single signer (one slot) legitimately using the existing, documented reconsideration feature — no majority collusion, no key compromise, and no consensus-acceptance dependency. The only precondition is a rejection reason that the signer's own logic treats as reconsiderable and a subsequent legitimate acceptance for the same `signer_signature_hash`, which is an intended, supported code path, not an attacker-crafted one. This makes the bug reachable in ordinary operation whenever timing causes a flip, making likelihood moderate-to-high whenever reconsideration occurs near a threshold boundary.

### Recommendation
Use a single, shared per-slot state (not two independently-gated collections) to track each signer's *current* vote for a given block, e.g. an enum `{Accepted(sig), Rejected}` keyed by `slot_id`. On receipt of a new response for a slot that already has a recorded opposite-kind vote, first subtract the previously counted weight from the stale total (`total_weight_rejected` or `total_weight_approved`) before adding it to the new total, so that at all times `total_weight_approved + total_weight_rejected` equals the sum of weights of currently-known distinct positions, never double-counting a flipped signer's weight into both buckets.

### Proof of Concept
1. Set up 2 signers, A (weight w_A) and B (weight w_B), such that `w_A < min_weight ≤ w_A + w_B` (A alone can't reach acceptance threshold, but A+B can), and separately `w_A` alone is enough to satisfy the coordinator's "blocking minority" check together with the current sum (`total_weight_rejected + weight_threshold > total_weight`).
2. Miner proposes a block; signer A sends `BlockResponse::Rejected` for it with a reconsiderable reason code (per the "reconsider" changelog entry). `stackerdb_listener.rs` records: `responded_signers = {A}`, `total_weight_rejected = w_A`.
3. Some time later, A's local state re-evaluates the same reject reason and now sends `BlockResponse::Accepted` for the very same `signer_signature_hash`. The `Accepted` handler checks only `gathered_signatures.contains_key(A)`, which is false, so it adds `w_A` to `total_weight_approved` as well: now `total_weight_approved = w_A`, `total_weight_rejected` still `w_A`.
4. Signer B then sends `Accepted`; `total_weight_approved` becomes `w_A + w_B ≥ min_weight`, so `signer_coordinator.rs`'s `wait_for_signer_signatures` returns block signatures and mines the block ‑ while `total_weight_rejected` (still `w_A`) is simultaneously nonzero and unretracted, i.e., the coordinator's bookkeeping no longer reflects a consistent partition of signer weight, and in a configuration where B's weight is smaller, the stale `total_weight_rejected` entry from A could instead push the rejection branch (`total_weight_rejected + weight_threshold > total_weight`) to fire concurrently/erroneously, demonstrating the miscount.

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L70-82)
```rust
#[derive(Debug, Clone)]
pub struct BlockStatus {
    /// Set of the slot ids of signers who have responded
    pub responded_signers: HashSet<u32>,
    /// Map of the slot id of signers who have signed the block and their signature
    pub gathered_signatures: BTreeMap<u32, MessageSignature>,
    /// Total weight of signers who have signed the block
    pub total_weight_approved: u32,
    /// Total weight of signers who have rejected the block
    pub total_weight_rejected: u32,
    /// Per-txid rejection tracking from signers
    pub failed_txids: HashMap<Txid, FailedTxInfo>,
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

**File:** stacks-signer/CHANGELOG.md (L176-180)
```markdown
## [3.1.0.0.8.0]

### Changed

- For some rejection reasons, a signer will reconsider a block proposal that it previously rejected ([#5880](https://github.com/stacks-network/stacks-core/pull/5880))
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
