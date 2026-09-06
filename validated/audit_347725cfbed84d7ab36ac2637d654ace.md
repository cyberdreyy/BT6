## Analysis

The fee-on-transfer bug class is: code assumes an operation's recorded effect (transfer amount) always matches the operation's real effect, and never re-derives ground truth — so two independently-maintained tallies drift apart. The matching analog in this repo is in the **stacks-node miner-side signer response tally**, not the signer's own equivocation-guarded state machine.

### Title
Reject-then-Accept flip lets a single signer's weight be counted in both `total_weight_rejected` and `total_weight_approved` - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener` maintains a per-block `BlockStatus` with two supposedly mutually-exclusive weight tallies, `total_weight_approved` and `total_weight_rejected`, gated by a single `responded_signers` set meant to ensure each signer's weight lands on exactly one side. The two branches that update these tallies use different, inconsistent gating keys, letting one signer's weight be added to both sides when the signer rejects first and later accepts.

### Finding Description
`BlockStatus` is defined with `responded_signers`, `gathered_signatures`, `total_weight_approved`, and `total_weight_rejected`, intended as an exclusive per-signer accounting structure. [1](#0-0) 

On an `Accepted` message, the code gates the weight addition on `gathered_signatures.contains_key(&slot_id)` — which is empty on a signer's first acceptance — and only afterward unconditionally inserts into `responded_signers`: [2](#0-1) 

On a `Rejected` message, the gate is instead `responded_signers.insert(&slot_id)` (true only the first time any response — accept or reject — was recorded for that slot): [3](#0-2) 

Because the accept-path gate (`gathered_signatures`) and the reject-path gate (`responded_signers`) are different sets, the two tallies are not mutually exclusive in one direction: if a signer sends a Reject first (weight added to `total_weight_rejected`, and `responded_signers` now contains its slot), then later sends an Accept for the same block (a legitimate scenario the v0 signer explicitly supports — a rejected block can be re-evaluated and signed once the reject reason becomes stale, per the signer's own re-evaluation logic), the Accept branch still adds the signer's weight to `total_weight_approved` because `gathered_signatures` was still empty for that slot. The stale weight in `total_weight_rejected` is never removed. (The reverse order is correctly guarded: once `responded_signers` contains the slot from an Accept, a later Reject cannot add weight, because the `responded_signers.insert()` gate returns `false`.)

The equality this breaks is: “a signer's weight should be attributable to at most one side (accepted xor rejected) of the miner's tally,” i.e. `total_weight_approved + total_weight_rejected <= total_weight`. With the flip-flop ordering above, that sum can exceed `total_weight`, so the coordinator's rejection determination: [4](#0-3) 
can fire using stale rejection weight from a signer who has since actually signed the block, even though the same signer's genuine signature is present in `gathered_signatures` and would otherwise have contributed to a legitimate `>= weight_threshold` outcome.

### Impact Explanation
This lets a miner's coordinator declare a proposal "rejected" (entering the `SignersRejected`/txid-exclusion path) using inflated, stale rejection weight that double-counts a signer who actually went on to accept and sign the block — i.e., the miner's aggregated-rejection-weight vs. verified-accepts equality is broken, causing the coordinator to treat an actually-signable block as unrecoverably rejected. This wedges block production/liveness for that proposal via the coordinator despite the presence of a real, verifiable supermajority of signatures.

### Likelihood Explanation
No majority of signers, no auth token, and no other signer's key are required — the trigger is one signer (or a small set) whose own decision transitions from reject to accept on the same block, a state transition explicitly supported by the v0 signer's reject-reason re-evaluation flow (`should_reevaluate_reject_reason`, per `docs/signer-flows.md` section 3). A one-slot miner simply needs to observe such ordinary gossip traffic through its `StackerDBListener`.

### Recommendation
Use a single authoritative per-signer response record (e.g., track each slot's *current* verdict and its weight contribution, then recompute `total_weight_approved`/`total_weight_rejected` from that record, or explicitly subtract the previous side's weight when a signer's verdict changes) instead of gating the two tallies on two different, inconsistent sets (`gathered_signatures` vs `responded_signers`).

### Proof of Concept
1. Miner proposes block B; signer S initially rejects B (e.g., due to a transient/stale rejection reason). `total_weight_rejected += weight(S)`; `responded_signers` now contains S's slot.
2. Conditions change; S's local re-evaluation logic (per `should_reevaluate_reject_reason`) causes S to later accept and sign B, broadcasting `BlockResponse::Accepted`.
3. `StackerDBListener` processes the Accepted message: `gathered_signatures` has no entry yet for S's slot, so the guard passes and `total_weight_approved += weight(S)` is executed, while `total_weight_rejected` retains S's earlier contribution (never removed).
4. If enough signers replay this reject→accept sequence, `total_weight_rejected.saturating_add(weight_threshold) > total_weight` can become true using stale weight, causing `signer_coordinator.rs` to return `Err(NakamotoNodeError::SignersRejected {..})` for a block that in fact reached (or would reach) the genuine `>= weight_threshold` of real signatures in `gathered_signatures`.

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
