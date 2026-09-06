### Title
Stale rejection weight is never cleared when a signer later switches to acceptance, letting one signer's weight double-count toward both the approval and rejection tallies - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListener`'s per-block tally (`BlockStatus`) uses a single `responded_signers` set to guard against double-counting a signer's weight, but the guard is checked inconsistently between the accept and reject code paths. A signer that rejects a block first and later sends an acceptance for the same block has its weight added to `total_weight_approved` without `total_weight_rejected` ever being decremented, so the same signer's weight is counted in both pools simultaneously.

### Finding Description
`BlockStatus` tracks `total_weight_approved`, `total_weight_rejected`, and a shared `responded_signers: HashSet<u32>` meant to prevent double counting. [1](#0-0) 

On `BlockResponse::Accepted`, the dedup guard used is `!block.gathered_signatures.contains_key(&slot_id)` (not `responded_signers`), and after adding the signature, `responded_signers.insert(slot_id)` is called unconditionally: [2](#0-1) 

On `BlockResponse::Rejected`, the guard is `block.responded_signers.insert(slot_id)` (returns `false`, i.e. no-op, if already present): [3](#0-2) 

Because of this asymmetry:
- Accept → then Reject from the same signer: correctly ignored, since `responded_signers` already contains the slot id (rejection path is properly guarded).
- Reject → then Accept from the same signer (a signer changing its mind, which is a legitimate, reachable single-signer action, no majority required): the reject path already added the signer's weight to `total_weight_rejected` and inserted the slot id into `responded_signers`. The later Accept message is *not* blocked, because the accept-path guard only checks `gathered_signatures` (empty for this slot at that point), not `responded_signers`. The accept is processed, `total_weight_approved` grows by the same signer's weight, and `total_weight_rejected` is never decremented.

The net effect is `total_weight_approved + total_weight_rejected` can exceed `self.total_weight`, because one signer's weight is counted in both pools. This breaks the intended equality "the tallied weight for a given signer reflects only its most recent response," which is exactly the class of bug identified in the referenced report (a persistent count/tally not being reversed on a state transition, corrupting a downstream threshold decision).

### Impact Explanation
The stale rejected weight is read by the coordinator to decide whether a block proposal should be treated as globally rejected (`total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight`) versus approved (`total_weight_approved >= self.weight_threshold`), waking waiters via `cvar.notify_all()`. [4](#0-3) [5](#0-4) 

A signer that initially rejects and then reconsiders and accepts leaves its weight permanently counted against the block (inflating `total_weight_rejected`) while simultaneously counting for it. This can cause the coordinator's rejection-threshold condition to fire using phantom weight that no longer represents a live rejection, making a block that could legitimately reach the 70% acceptance threshold appear to have crossed the 30% rejection-blocking threshold instead (or vice versa, confusing which condition is met first). This is a miner/coordinator-side liveness and correctness defect: it can cause the miner to prematurely abandon a block that real signer support could still finalize, or to race ahead on inflated/ambiguous tallies. Unlike the underlying `SignerDb`/`BlockInfo` state machine used by the signer itself (which is correctly monotonic — signatures are bearer instruments and rejections are revocable, and `check_state`/`move_to` enforce valid transitions, see `stacks-signer/src/signerdb.rs`), this miner-side aggregation has no equivalent transition guard and is inconsistent between the two response kinds.

I was not able to fully trace, within this session, the exact downstream consumer in `signer_coordinator.rs` that reads `total_weight_approved`/`total_weight_rejected` after the wake-up (e.g., whether it re-validates using `gathered_signatures.len()`-derived weight, which would mask the corrupted `total_weight_approved` field, or whether it trusts the raw counter). This limits certainty on whether the corrupted counter is actually consumed for a final decision versus being purely advisory/logging, and should be verified before treating this as more than a likely finding. [6](#0-5) 

### Likelihood Explanation
Requires only a single signer to send a `Rejected` message and later a `Accepted` message for the same block — no majority collusion, no key compromise, no auth token access. Vote flips are a normal, expected occurrence (e.g., a signer re-evaluates after a transient validation failure), so this path is realistically reachable through ordinary gossip traffic without any attacker-controlled majority.

### Recommendation
Make the two response paths symmetric with respect to `responded_signers`/weight bookkeeping:
- When processing `Accepted`, if the signer's slot id is already present with a recorded rejection, subtract the signer's weight from `total_weight_rejected` (and remove any associated `failed_txids` contribution) before/while adding it to `total_weight_approved`.
- Alternatively, maintain a single `HashMap<u32, Vote>` per signer (latest vote only) and recompute `total_weight_approved`/`total_weight_rejected` from that map on every update, rather than incrementally mutating two independently-guarded running totals. This removes the possibility of the same signer contributing to both totals at once.

### Proof of Concept
1. Coordinator inserts a block via `StackerDBListenerComms::insert_block`, initializing `total_weight_approved = 0`, `total_weight_rejected = 0`, empty `responded_signers`/`gathered_signatures`. [7](#0-6) 
2. Signer S (weight `w`) sends `BlockResponse::Rejected` for this block. `responded_signers.insert(S)` succeeds (true), so `total_weight_rejected += w`. [3](#0-2) 
3. Signer S changes its mind and sends `BlockResponse::Accepted` for the *same* block (e.g., after a retried validation succeeds). The guard `!block.gathered_signatures.contains_key(&S)` is `true` (S never signed yet), so the code proceeds: `total_weight_approved += w`, `gathered_signatures.insert(S, sig)`, and `responded_signers.insert(S)` (already present, no-op). [2](#0-1) 
4. Result: `total_weight_rejected` still includes `w` from step 2, and `total_weight_approved` now also includes `w` from step 3 — the same signer's weight is counted in both totals, so `total_weight_approved + total_weight_rejected > self.total_weight` is possible even though only one signer with weight `w` (out of the total) ever responded. This is analogous to the `MinipoolManager.recordStakingError` bug: an event that logically should reverse a prior tally increment (`total_weight_rejected -= w`) never does so, leaving a stale count that corrupts a downstream threshold-based decision.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-470)
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

                        if block.total_weight_approved >= self.weight_threshold {
                            // Signal to anyone waiting on this block that we have enough signatures
                            cvar.notify_all();
                        }
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-518)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L567-574)
```rust
                        if block
                            .total_weight_rejected
                            .saturating_add(self.weight_threshold)
                            > self.total_weight
                        {
                            // Signal to anyone waiting on this block that we have enough rejections
                            cvar.notify_all();
                        }
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L691-704)
```rust
impl StackerDBListenerComms {
    /// Insert a block into the block status map with initial values.
    pub fn insert_block(&self, block: &NakamotoBlockHeader) {
        let (lock, _cvar) = &*self.blocks;
        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");
        let block_status = BlockStatus {
            responded_signers: HashSet::new(),
            gathered_signatures: BTreeMap::new(),
            total_weight_approved: 0,
            total_weight_rejected: 0,
            failed_txids: HashMap::new(),
        };
        blocks.insert(block.signer_signature_hash(), block_status);
    }
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L1-1)
```rust
// Copyright (C) 2024-2026 Stacks Open Internet Foundation
```
