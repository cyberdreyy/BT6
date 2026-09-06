### Title
Reject-then-accept vote flip lets a signer's weight be counted in both `total_weight_approved` and `total_weight_rejected`, corrupting the miner's aggregated-weight tally - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener::run` accumulates each signer's weight into `BlockStatus.total_weight_approved` or `total_weight_rejected` based on `BlockResponse` messages, but it has no single per-signer "current vote" record — only two independent, one-shot dedup sets (`gathered_signatures` for accepts, and the shared `responded_signers` set gated only on the *first* message type seen). When a signer legitimately re-evaluates a block it previously rejected (an explicitly supported transition, `LocallyRejected → LocallyAccepted`, documented in the signer state machine) and later sends `BlockResponse::Accepted` for the same `signer_signature_hash`, the node adds that signer's weight to `total_weight_approved` without ever reversing the weight it had already added to `total_weight_rejected` for the earlier rejection. The two tallies are supposed to be mutually exclusive per signer (like segregated deposit balances), but nothing tracks "this signer's weight is already booked in bucket X" the way the CeFi report calls out — there is no accounting record per signer of which bucket currently holds their weight, only cumulative, append-only counters.

### Finding Description
`stacks-node/src/nakamoto_node/stackerdb_listener.rs::run` processes `SignerMessageV0::BlockResponse` events:

- On `Accepted`, weight is added to `total_weight_approved` only if `!block.gathered_signatures.contains_key(&slot_id)`, and `responded_signers.insert(slot_id)` is called unconditionally. [1](#0-0) 

- On `Rejected`, weight is added to `total_weight_rejected` only if `block.responded_signers.insert(slot_id)` returns `true` (i.e., first time this slot has responded at all). [2](#0-1) 

The `responded_signers` set is shared across both message kinds, but the `Accepted` path never checks it before crediting weight — it only checks `gathered_signatures`, a *separate* map that is populated only by prior `Accepted` messages. Consequently:

1. Signer S sends `Rejected` first → `responded_signers` gets S's slot, `total_weight_rejected += weight(S)`.
2. Signer S later re-evaluates (a state transition the signer explicitly supports, per `BlockInfo::check_state`/`move_to`, `LocallyRejected → LocallyAccepted`) and sends `Accepted` for the *same* block.
3. Because `gathered_signatures` does not yet contain S's slot, the `Accepted` branch credits `total_weight_approved += weight(S)` — with no corresponding subtraction from `total_weight_rejected`.

Result: S's weight is now counted in **both** `total_weight_approved` and `total_weight_rejected` simultaneously, permanently. The `BlockStatus` struct has no representation of "the current, single vote of signer S" — only two independently-growing counters, exactly the "missing internal accounting" bug class from the external report: funds/weight attributable to one identity get commingled across buckets with no way to reconcile or reverse a stale contribution.

This directly corrupts the equality the coordinator relies on when polling `BlockStatus` in `SignerCoordinator`: [3](#0-2) [4](#0-3) 

`BlockStatus` and thresholds are defined/used here: [5](#0-4) 

### Impact Explanation
The coordinator's decision to broadcast a block as signed, or to abandon it as rejected, is driven purely by `total_weight_approved >= weight_threshold` and `total_weight_rejected + weight_threshold > total_weight`. Because a single signer's weight can be double-booked (once as rejected, once as approved) after a legitimate reject→accept flip, `total_weight_approved + total_weight_rejected` can exceed `total_weight` for the same block. This breaks the intended invariant that these two counters partition the signer set's weight ("aggregated-weight vs verified-accepts" equality): a stale, superseded rejection is never retracted, so it inflates the perceived rejection weight indefinitely even after the signer has moved on to accepting. In a close vote (near the 70/30 boundary), this stale double-counted weight can:
- cause the miner to treat a block as rejected (`SignersRejected`, banning transactions and forcing a retry with a different tx set) even though genuine, current signer weight would have approved it, or
- contribute to a spurious early "have reached the block acceptance threshold" signal that includes weight that logically also still "belongs" to the rejected bucket.

This is a liveness/aggregation-integrity issue in the node's tally of signer responses (a rejection persisting alongside a later, superseding acceptance — i.e. an accept getting entangled with a stale reject weight, the mirror of "a rejection recounted as an accept"), reachable by a single ordinary signer flipping its vote once — no majority collusion required.

### Likelihood Explanation
Reject→accept flips for the same `signer_signature_hash` are not a fringe/adversarial-only case: the signer's own documented state machine explicitly allows `LocallyRejected → LocallyAccepted` upon re-evaluation (e.g., a transient reject reason such as a stale conflict becomes stale-and-ignorable, or the block is re-proposed after a timeout and re-validated). Any signer that rejects a block for a recoverable reason and later signs the same block after re-evaluation will trigger this code path in the ordinary course of operation, not just under attack. No special timing tricks or majority control are needed — only for the listener's two message-handling branches to see the same slot in a reject-then-accept order for one block hash, which the signer-side logic can produce on its own.

### Recommendation
Track a single "current vote" per slot (e.g., an enum `{None, Rejected(weight), Accepted(weight, signature)}` keyed by `slot_id`) instead of two independently-incrementing counters. When a signer's vote transitions from rejected to accepted (or vice versa), atomically move their weight between buckets: subtract from the old bucket before adding to the new one, mirroring per-depositor internal accounting rather than cumulative append-only totals. Recompute `total_weight_approved`/`total_weight_rejected` from that per-slot map (or maintain them incrementally with correct debit/credit) so the sum of both never exceeds `total_weight` and always reflects each signer's latest response.

### Proof of Concept
1. Node starts collecting responses for a proposed block with `signer_signature_hash = H` and `weight_threshold = 70` out of `total_weight = 100`, with signer S having `weight(S) = 20`.
2. S validates block `H` as invalid for a recoverable reason (e.g., a transient chainstate conflict) and broadcasts `BlockResponse::Rejected` for `H`.
   - In `stackerdb_listener.rs::run`, `Rejected` branch: `responded_signers.insert(S)` → `true`; `total_weight_rejected += 20` → `total_weight_rejected = 20`.
3. A later re-proposal (or re-evaluation window) causes S's own signer logic to reconsider (`should_reevaluate_reject_reason` / `LocallyRejected → LocallyAccepted`), and S signs and broadcasts `BlockResponse::Accepted` for the same `H`.
   - In `stackerdb_listener.rs::run`, `Accepted` branch: `gathered_signatures.contains_key(S)` is `false` (S never sent `Accepted` before) → `total_weight_approved += 20` → `total_weight_approved = 20`.
   - `responded_signers.insert(S)` is a no-op (already present); no code path decrements `total_weight_rejected`.
4. Now `total_weight_rejected = 20` (stale, from S's superseded rejection) and `total_weight_approved = 20` (from S's current, valid acceptance) — S's single 20-weight vote is counted in both buckets, and `total_weight_approved + total_weight_rejected = 40` even though only one signer (weight 20) has actually responded. Repeating this with enough signers flipping votes lets `total_weight_rejected` accumulate stale weight past the `>30%` blocking-minority line, or `total_weight_approved` accumulate past the `70%` threshold, using weight that is simultaneously still "owed" to the other bucket — an outcome the coordinator's threshold checks (`stacks-node/src/nakamoto_node/signer_coordinator.rs` lines 509-513, 541-545) assume cannot happen.

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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-513)
```rust
            if block_status
                .total_weight_rejected
                .saturating_add(self.weight_threshold)
                > self.total_weight
            {
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L541-545)
```rust
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```
