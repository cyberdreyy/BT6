### Title
Miner's `StackerDBListener::run` double-counts a signer's weight into both `total_weight_approved` and `total_weight_rejected` when a signer switches its vote from Rejected to Accepted for the same block, spuriously triggering block rejection - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
The miner-side vote tally in `StackerDBListener::run` tracks per-block `total_weight_approved` and `total_weight_rejected` in a shared `BlockStatus` struct, guarded by a single `responded_signers: HashSet<u32>` that is meant to ensure each signer's weight is counted only once. However, the guard is applied asymmetrically: the `Rejected` handler checks `responded_signers` before adding weight, but the `Accepted` handler only checks the separate `gathered_signatures` map, never `responded_signers`. This lets one signer's weight be added to *both* tallies when it emits a Reject message followed later by an Accept (signature) message for the same block hash, since `total_weight_rejected` is never decremented on the switch.

### Finding Description
`BlockStatus` tracks two independent weight sums plus a `responded_signers` set: [1](#0-0) 

When an `Accepted` message arrives, the code only guards the increment with `!block.gathered_signatures.contains_key(&slot_id)`, and unconditionally does `block.responded_signers.insert(slot_id)` afterward — it never checks whether `slot_id` is already in `responded_signers` from a prior rejection: [2](#0-1) 

By contrast, when a `Rejected` message arrives, the increment is correctly gated on `responded_signers.insert(slot_id)` returning `true` (i.e., not already present): [3](#0-2) 

This asymmetry means:
- Accept-then-Reject for the same signer/block: correctly guarded — no double count, since the reject path sees `responded_signers` already contains the slot.
- Reject-then-Accept for the same signer/block: **not guarded** — the signer's weight is added to `total_weight_rejected` on the first message and then *also* added to `total_weight_approved` on the second message. `total_weight_rejected` is never decremented, so the signer is now counted in both tallies simultaneously.

This is the same bug class as the referenced Hats Protocol report: a state transition (a signer's status changing) is not reconciled against the stale aggregate counter, breaking the invariant that a signer's weight is reflected exactly once in the vote tally. Here it breaks the equality between "distinct signer votes" and "aggregated weight."

A single malicious signer, controlling only its own private key and StackerDB slot, can trigger this at will by publishing a `Rejected` message for a block and subsequently publishing a validly-signed `Accepted` message for the same block. No majority, no other signer's key, and no node/consensus bug is required — this is purely a bookkeeping flaw in the miner-side `StackerDBListener`/`SignerCoordinator` aggregation logic.

### Impact Explanation
`SignerCoordinator::wait_for_signer_signatures_or_timeout` (in `signer_coordinator.rs`) reads `total_weight_rejected` and `total_weight_approved` from the same `BlockStatus` to decide whether to abort the block as rejected or accept it: [4](#0-3) 

Because the reject branch is evaluated first, an inflated `total_weight_rejected` (from double counting a signer that ultimately signed the block) can push the sum past `total_weight - weight_threshold`, causing the miner to spuriously conclude the blocking-minority threshold was crossed (`SignersRejected`) even though the true, non-duplicated rejecting weight never reached 30%. This can cause the miner to discard/abandon an otherwise validly, sufficiently-signed block, and additionally causes transaction IDs to be wrongly placed into `temporarily_excluded_txids`/`permanently_excluded_txids` based on inflated per-txid weight. This is a liveness wedge: a single dishonest signer can repeatedly force the miner into believing legitimate blocks were rejected, stalling block production/liveness without needing a majority of signers.

### Likelihood Explanation
Trivial to trigger: a single byzantine (or simply buggy/out-of-order) signer process only needs to publish two of its own StackerDB messages — a `BlockResponse::Rejected` followed by a `BlockResponse::Accepted` — for the same `signer_signature_hash`, both of which are messages it is fully authorized to sign with its own key. No collusion, timing majority, or additional privilege is required.

### Recommendation
In the `Accepted` handler, guard the `total_weight_approved` increment on the signer not having already contributed weight via `responded_signers` (or, symmetrically, subtract the signer's weight from `total_weight_rejected` if the slot is switching from a prior rejection), so that each signer's weight is reflected in exactly one of the two tallies at any time — mirroring the guard already present in the `Rejected` branch.

### Proof of Concept
1. Miner requests signatures for block `B` from N signers with weights summing to `total_weight`, `weight_threshold` = 70%.
2. Malicious signer `S` (weight `w`) publishes `BlockResponse::Rejected` for `B`. `StackerDBListener` sets `responded_signers.insert(S)` and `total_weight_rejected += w`.
3. `S` then publishes a validly-signed `BlockResponse::Accepted` for the same `B` (it holds the private key and can sign the hash regardless of its earlier rejection). `StackerDBListener`'s accept handler checks only `gathered_signatures` (empty for `S`), so it does `total_weight_approved += w` and `responded_signers.insert(S)` (no-op, already present) — `total_weight_rejected` is left unchanged at the incremented value.
4. Now `total_weight_approved + total_weight_rejected > total_weight` by `w`. If enough other legitimate signers also reject (just under 30%), adding `w` from this double-count pushes `total_weight_rejected + weight_threshold > total_weight` in `signer_coordinator.rs`, and the miner aborts the block via `NakamotoNodeError::SignersRejected`, even though the actual distinct rejecting weight was insufficient.

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
