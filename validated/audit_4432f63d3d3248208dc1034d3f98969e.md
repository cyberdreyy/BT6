### Title
Stale rejection weight is never revoked when a signer flips to acceptance, permanently inflating `total_weight_rejected` and enabling a false global-rejection verdict — (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener` tallies signer votes for a block proposal into two independent counters, `total_weight_approved` and `total_weight_rejected`, gated by two *different* de-duplication sets: `gathered_signatures` (keyed by slot id, only touched on `Accepted`) and `responded_signers` (a single shared `HashSet<u32>` touched by both `Accepted` and `Rejected`). [1](#0-0)  Because the gate for crediting rejection weight is `responded_signers`, but the gate for crediting acceptance weight is the separate `gathered_signatures` map, a signer who first rejects and later changes their mind and accepts the same block has their weight added to `total_weight_rejected` *and*, independently, to `total_weight_approved`, with no code path ever decrementing the earlier rejection weight. This is the same root-cause pattern as the Ammplify M‑20 report: a value is computed and applied to one bucket but never reconciled/routed away when the state that produced it is superseded, so it becomes permanently "stuck" — here, stuck rejection weight instead of stuck treasury revenue.

### Finding Description
For an `Accepted` response, weight is only added if the slot id is not already present in `gathered_signatures`:
```rust
if !block.gathered_signatures.contains_key(&slot_id) {
    block.total_weight_approved = block.total_weight_approved.saturating_add(signer_entry.weight);
    ...
}
block.gathered_signatures.insert(slot_id, signature);
block.responded_signers.insert(slot_id);
``` [2](#0-1) 

For a `Rejected` response, weight is only added if the slot id was not already in `responded_signers`:
```rust
if block.responded_signers.insert(slot_id) {
    block.total_weight_rejected = block.total_weight_rejected.saturating_add(signer_entry.weight);
    ...
}
``` [3](#0-2) 

Sequence: signer S sends `Rejected` first → `responded_signers.insert(S)` succeeds → `total_weight_rejected += w_S`. S later sends `Accepted` for the *same* block (legitimate under the protocol — see `signer.rs`'s own "outdated peer" re-evaluation and pre-commit re-send flows for handling revised responses) [4](#0-3) . The `Accepted` handler checks `gathered_signatures`, which S has never touched, so the gate passes: `total_weight_approved += w_S`, `responded_signers.insert(S)` (already present, no-op), `gathered_signatures.insert(S, sig)`. There is no code anywhere in this file that removes `w_S` from `total_weight_rejected` when this happens. The two counters, which are supposed to be mutually exclusive partitions of signer weight, now double count `w_S`: `total_weight_approved + total_weight_rejected` can exceed `total_weight`.

This breaks the aggregated-weight-vs-verified-accepts equality that `SignerCoordinator::get_block_status` relies on to decide the block's fate. The rejection branch is checked first:
```rust
if block_status.total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight {
    ...
    return Err(NakamotoNodeError::SignersRejected { ... });
} else if block_status.total_weight_approved >= self.weight_threshold {
    ...
    return Ok(block_status.gathered_signatures.values().cloned().collect());
}
``` [5](#0-4)  Because stale rejection weight from S is never retracted, a block that has genuinely reached the 70% approval threshold in real time (S having flipped to accept) can still simultaneously satisfy the rejection-threshold check first (since checked first), causing the miner to treat a validly-signed block as globally rejected and drop transactions via `permanently_excluded_txids`/`temporarily_excluded_txids`. Conversely, even absent that exact tie, the stale weight permanently biases the reject tally upward for the lifetime of that block's `BlockStatus` entry, making the 30%+ blocking-minority threshold reachable/crossable using weight that no longer represents any signer's current vote.

### Impact Explanation
This is a liveness wedge on block finalization for the miner (violates safety of "aggregated-weight vs verified-accepts" equality): stale, superseded rejection weight is never purged, so the miner's decision logic can classify a block that has real 70% signature weight as globally rejected, or can reach the rejection threshold using weight that no longer reflects the current vote of the affected signer(s). This can stall a tenure (miner stops proposing/using that block, retries, or excludes transactions it should not) — matching the High-impact category "a signer [here, the coordinator/miner] wedged into never signing/accepting valid blocks... losing consistency between counted weight and actual signer state."

### Likelihood Explanation
Requires only a single signer (one slot) to send a `Rejected` message followed later by an `Accepted` message for the same `signer_signature_hash` — both are legitimate protocol messages a single signer can emit on its own (e.g. after `should_reevaluate_block`/re-evaluation flows in `stacks-signer/src/v0/signer.rs` cause a signer to change its verdict on the same proposal, or a signer simply resending after a local recompute). No majority collusion, no key compromise, and no StackerDB-sync trickery is needed — this is purely a bookkeeping bug in `StackerDBListener`'s local tallying of a `BlockStatus`, triggerable by any one signer's own natural response sequence.

### Recommendation
Use a single consistent per-signer state machine for vote tallying: before crediting `total_weight_approved` or `total_weight_rejected`, check (and if necessary retract) any existing weight the same slot id contributed to the opposite bucket. Concretely, gate both `Accepted` and `Rejected` weight updates on a shared "current vote" map keyed by slot id (not two independently-gated collections), and when a signer's vote changes, subtract the old contribution from the previous bucket (`saturating_sub`) before adding it to the new one, mirroring the invariant that `total_weight_approved + total_weight_rejected` must never double count a slot id's weight.

### Proof of Concept
1. Coordinator submits a proposal; `BlockStatus` is created with empty `responded_signers`, `gathered_signatures`, `total_weight_approved = 0`, `total_weight_rejected = 0`.
2. Signer S (slot 5, weight 20) sends `BlockResponse::Rejected` → `responded_signers = {5}`, `total_weight_rejected = 20`.
3. Signer S later re-evaluates and sends `BlockResponse::Accepted` for the same `signer_signature_hash` → `gathered_signatures` does not contain slot 5, so the check `!block.gathered_signatures.contains_key(&5)` passes → `total_weight_approved = 20`, `gathered_signatures = {5: sig}`.
4. Now `total_weight_approved (20) + total_weight_rejected (20) = 40` counts S's weight twice against a `total_weight` that only includes it once.
5. If other signers push `total_weight_approved` to the 70% threshold while S's stale 20 remains in `total_weight_rejected`, `get_block_status` in `signer_coordinator.rs` evaluates the rejection branch first and can return `SignersRejected` even though the real, current signer set has surpassed the acceptance threshold.

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

**File:** stacks-signer/src/v0/signer.rs (L2462-2466)
```rust
        // If this isn't our own signature and we haven't seen a pre-commit from this signer yet, try treating it as a pre-commit in case the caller is running an outdated version
        if signer_address != &self.stacks_address && !self.signer_db.has_committed(block_hash, signer_address).inspect_err(|e| warn!("Failed to check if pre-commit message already considered for {signer_address:?} for {block_hash}: {e}")).unwrap_or(false) {
            self.handle_block_pre_commit(stacks_client, sortition_state, signer_address, block_hash);
            return;
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
