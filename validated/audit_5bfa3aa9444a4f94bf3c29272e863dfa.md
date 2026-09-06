## Title
Miner-side weight double counting when a signer flips from Rejected to Accepted for the same block — `total_weight_approved` + `total_weight_rejected` invariant broken - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListener::run` maintains two independently-incremented counters, `total_weight_approved` and `total_weight_rejected`, on the node-side `BlockStatus` used by the mining coordinator to decide whether a proposed block has reached the 70% signing threshold or the 30%+ rejection threshold. Each counter is guarded against double counting *within itself* (via `gathered_signatures` for accepts and `responded_signers` for rejects), but the two counters are not reconciled against each other. A signer that first rejects a block and later legitimately re-evaluates and accepts the same block (a state transition explicitly permitted by the signer state machine, `LocallyRejected --> LocallyAccepted`) has its weight added to `total_weight_approved` without ever being subtracted from `total_weight_rejected`. This is the same bug class as the reported `reserved_grant_amounts`/`reserved_amount` desync: a per-participant amount is mutated/consumed, but the aggregate total that must track it is never updated, breaking an equality the code depends on for a safety/liveness decision.

### Finding Description
`BlockStatus` tracks, per proposed block: [1](#0-0) 

When a `BlockResponse::Accepted` message arrives, the coordinator adds the signer's weight to `total_weight_approved` only if the signer's slot is not already present in `gathered_signatures`, then unconditionally records the slot in both `gathered_signatures` and `responded_signers`: [2](#0-1) 

When a `BlockResponse::Rejected` message arrives, weight is added to `total_weight_rejected` only if `responded_signers.insert(slot_id)` succeeds (i.e., the slot hasn't responded before, in either direction): [3](#0-2) 

Consider a signer `S` that first sends `Rejected` for block `B`:
- `responded_signers.insert(slot_S)` succeeds → `total_weight_rejected += weight_S`.

`S` then re-evaluates and sends `Accepted` for the same block `B` (a legitimate, single-signer-triggered transition per the documented state machine, `LocallyRejected --> LocallyAccepted`, see `docs/signer-flows.md` "Block lifecycle (`BlockState`)" section):
- `gathered_signatures.contains_key(slot_S)` is `false` (accept path never checked `responded_signers`), so the guard passes → `total_weight_approved += weight_S`.
- `weight_S` is never subtracted from `total_weight_rejected`.

Result: the same signer's weight is now counted in *both* `total_weight_approved` and `total_weight_rejected` simultaneously, for the lifetime of that `BlockStatus` entry. The invariant the coordinator relies on — that every signer's weight is reflected in exactly one bucket at a time, so `total_weight_approved + total_weight_rejected ≤ total_weight` — is violated. This exactly mirrors the reported bug class: `reserved_grant_amounts[i]` (per-recipient) is reduced by a claim, but `reserved_amount` (the aggregate) is never reduced to match, so a later `sanity_check` compares a stale aggregate against a live per-item sum.

### Impact Explanation
The coordinator's decision loop consumes these two aggregates directly to decide the fate of a block proposal: [4](#0-3) 

Because `total_weight_rejected` retains phantom weight from a signer who has since switched to accepting, the miner can compute `total_weight_rejected.saturating_add(weight_threshold) > total_weight` using an inflated rejection figure that no longer reflects any signer's current opinion, and abort the block as globally rejected (`NakamotoNodeError::SignersRejected`) even though the true, current distinct-signer opinion set may not warrant it. This is a liveness wedge on block production driven entirely by the aggregated-weight-vs-verified-accepts mismatch: a single signer, exercising an explicitly allowed re-evaluation, can leave stale weight in the rejection bucket that combines with other signers' independent rejections to cross the rejection threshold earlier than the true state of votes would justify, or conversely can make an already-borderline rejection appear to persist after the underlying signer no longer opposes the block, delaying or misdirecting block finalization. The corrupted `BlockStatus` counters also feed the rejection-timeout backoff logic in `signer_coordinator.rs` (`rejections` variable driving `block_rejection_timeout_steps`), meaning the stale weight additionally distorts the miner's retry/timeout behavior for the remainder of that block's proposal lifetime, since nothing in the normal proposal flow clears `total_weight_rejected` short of `reset_rejections` (only invoked after a full timeout).

### Likelihood Explanation
This requires only one signer (well under any majority threshold) sending its normal, protocol-permitted two messages for the same block hash — a rejection followed later by an acceptance — which is a documented, legitimate signer behavior (`LocallyRejected --> LocallyAccepted` re-evaluation), not an attack requiring malicious signature forgery, StackerDB manipulation, or collusion. Any signer whose local view causes it to reject a proposal early (e.g., due to a transient chainstate check) and later accept it once conditions clear will trigger this path.

### Recommendation
Track each signer's current vote in a single map keyed by slot id (e.g., `HashMap<u32, Vote>` where `Vote` is `Accepted(MessageSignature)` or `Rejected`), and derive `total_weight_approved`/`total_weight_rejected` by summing over that map on each update (or, at minimum, when transitioning a slot from rejected to accepted, subtract its weight from `total_weight_rejected` before adding it to `total_weight_approved`, and vice versa). This keeps the aggregate weights consistent with the *current* per-signer vote rather than accumulating stale contributions.

### Proof of Concept
1. Node proposes block `B` to the signer set; `StackerDBListener` creates a fresh `BlockStatus` for `B` with `total_weight_approved = 0`, `total_weight_rejected = 0`.
2. Signer `S` (weight `w_S`) initially rejects `B` (e.g., transient chainstate check fails) and broadcasts `BlockResponse::Rejected`. The listener applies `stacks-node/src/nakamoto_node/stackerdb_listener.rs:515-518`: `responded_signers.insert(slot_S)` succeeds, `total_weight_rejected += w_S`.
3. `S` re-evaluates per the signer state machine and now accepts `B`, broadcasting `BlockResponse::Accepted`. The listener applies `stacks-node/src/nakamoto_node/stackerdb_listener.rs:443-465`: since `slot_S` is absent from `gathered_signatures`, `total_weight_approved += w_S`; `total_weight_rejected` is left unchanged at `w_S`.
4. Now `total_weight_approved + total_weight_rejected = w_S (approved) + w_S (stale rejected) > w_S` — `S`'s weight is double counted across the two buckets, and the stale `w_S` in `total_weight_rejected` can combine with genuinely-rejecting signers' weight to cross the `total_weight_rejected.saturating_add(weight_threshold) > total_weight` check in `signer_coordinator.rs:509-513`, causing the coordinator to abort the block even though `S` no longer opposes it.

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
