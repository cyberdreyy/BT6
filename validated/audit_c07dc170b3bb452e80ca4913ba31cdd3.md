### Title
Node-side signer weight double-counting on Reject→Accept re-evaluation - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
The bug class in the external report is a list-processing asymmetry: a check meant to gate one class of item (RDNT) is only applied in a subset of the code paths, so state that should be consistently accounted for is instead permanently stuck/mis-tracked. In `StackerDBListener`, the node-side vote tally for a block proposal uses two *different* dedup keys for the same signer's two possible responses (`Accepted`/`Rejected`), which allows one signer's weight to be counted in both `total_weight_approved` and `total_weight_rejected` simultaneously, and the rejection weight is never retracted.

### Finding Description
`BlockStatus` tracks, per proposed block, `responded_signers`, `gathered_signatures`, `total_weight_approved`, and `total_weight_rejected` [1](#0-0) .

On an `Accepted` message, the dedup check to decide whether to add weight is `!block.gathered_signatures.contains_key(&slot_id)`, and both `gathered_signatures` and `responded_signers` are updated afterward [2](#0-1) .

On a `Rejected` message, the dedup check is `block.responded_signers.insert(slot_id)` (only adds weight the first time this slot_id is seen at all), but this branch never inserts into `gathered_signatures` [3](#0-2) .

Consequence: if a signer's `Rejected` message for a given `signer_signature_hash` arrives first, `responded_signers` gains the slot_id and `total_weight_rejected` is incremented, but `gathered_signatures` remains empty for that slot. If the *same* signer later sends `Accepted` for the *same* block hash (a legitimate, documented transition — the signer's local state machine explicitly supports `LocallyRejected → LocallyAccepted` on re-evaluation, e.g. when a previously-conflicting sibling block times out and is superseded, as covered by `stale_sibling_replaced_when_canonical_tip_below`) [4](#0-3) , the Accept-branch check `!gathered_signatures.contains_key(&slot_id)` is still true (nothing was inserted there by the reject path), so the same signer's weight is added *again*, this time to `total_weight_approved`. The prior `total_weight_rejected` contribution is never retracted.

This breaks the intended invariant that the aggregated weight tallies reflect the current, verified set of distinct signer votes ("aggregated-weight vs verified-accepts" equality): a single signer's weight can now be simultaneously present in both totals, and the stale rejection weight persists indefinitely regardless of the signer's later, valid acceptance.

### Impact Explanation
The miner's `SignCoordinator`/`signer_coordinator.rs` consumes these tallies directly: it computes `SignersRejected` when `total_weight_rejected + weight_threshold > total_weight`, and only afterward checks `total_weight_approved >= weight_threshold` for acceptance [5](#0-4) . Because `total_weight_rejected` can retain permanently stale weight from signers who have since flipped to Accept, the coordinator can be pushed over the rejection-impossibility threshold using weight that no longer represents current opposition, causing the miner to abandon a block proposal that legitimately has, or would have, enough current signer support. This is a liveness degradation on block production reachable by ordinary message reordering/timing during normal signer re-evaluation — it does not require a majority of colluding signers, another signer's key, or any auth bypass.

### Likelihood Explanation
The `LocallyRejected → LocallyAccepted` re-evaluation transition is a documented, tested part of normal signer behavior (not an edge-case exploit), and StackerDB message delivery ordering across signers is not otherwise constrained, so a Reject-then-Accept sequence for the same block hash from a single signer is a realistic occurrence during tenure-start sibling conflicts and reorg-recovery scenarios that are explicitly tested elsewhere in the codebase.

### Recommendation
Use a single, unified dedup structure (e.g., always insert into `gathered_signatures`-equivalent bookkeeping, or track per-slot "last known vote" and its weight) so a signer's weight is (a) counted at most once across the approve/reject tallies, and (b) properly moved from the reject tally to the approve tally when a signer's vote is legitimately superseded. Concretely, track `responded_signers` as a slot_id → (vote kind, weight) map and recompute `total_weight_approved`/`total_weight_rejected` as sums over the map's current values rather than as independently-incremented running counters, so a later `Accepted` message replaces the earlier `Rejected` entry's contribution instead of stacking a second one alongside it.

### Proof of Concept
1. Node opens a `BlockStatus` for a proposed block with `signer_signature_hash = H`.
2. Signer S (weight `w`) proposes/validates and determines conflict with an in-flight sibling and sends `BlockResponse::Rejected` for `H`. Node's `StackerDBListener` processes it: `responded_signers.insert(slot_S)` → true → `total_weight_rejected += w`. `gathered_signatures` unchanged (does not contain `slot_S`).
3. The conflicting sibling subsequently times out per `check_latest_block_in_tenure`/`get_tenure_last_block_info` freshness rules, and signer S re-evaluates and locally accepts/signs the same block `H` (mirrors `stale_sibling_replaced_when_canonical_tip_below`) [4](#0-3) , broadcasting `BlockResponse::Accepted` for `H`.
4. Node's `StackerDBListener` processes the Accept: `gathered_signatures.contains_key(slot_S)` is false → `total_weight_approved += w` is applied again for the same signer S, while `total_weight_rejected` still includes S's earlier `w` and is never decremented.
5. Now `total_weight_rejected + total_weight_approved > total_weight` for this block, with S's weight double-counted; if enough other signers have also rejected in the interim (whether via genuine or similarly-stale means), `total_weight_rejected + weight_threshold > total_weight` can trigger before `total_weight_approved` reaches `weight_threshold`, and the coordinator returns `SignersRejected`, aborting the block even though S's current, valid vote is Accept.

Note: I was unable to trace the exact call sites/naming of `should_reevaluate_block`/`should_reevaluate_reject_reason` and `determine_response` beyond grep hit counts due to the final-iteration cutoff, so the precise signer-side function names that drive the re-send of a superseded response are cited from the design docs and confirmed test (`stale_sibling_replaced_when_canonical_tip_below`) rather than a full read of `stacks-signer/src/v0/signer.rs`. This does not affect the node-side (`stackerdb_listener.rs`) root cause, which is independently confirmed by direct code inspection.

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

**File:** stacks-signer/src/v0/tests.rs (L809-826)
```rust
    #[test]
    fn stale_sibling_replaced_when_canonical_tip_below() {
        // A zero timeout makes A's signature stale immediately, and the node's canonical tip
        // is still the parent (height 9): A failed to be confirmed, so the signer must sign
        // the replacement rather than stall the tenure (the reorg-recovery case).
        let (info_a, info_b, _) = run_sibling_scenario(Duration::ZERO, false, None);
        assert_a_signed(&info_a);
        assert_eq!(
            info_b.state,
            BlockState::LocallyAccepted,
            "block B should be signed: the conflicting sibling timed out and is not canonical, got: {}",
            info_b.state
        );
        assert!(
            info_b.signed_self.is_some(),
            "block B should carry our signature after the conflict timed out unconfirmed"
        );
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
