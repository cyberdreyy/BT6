## Title
Node-side signer vote tallying double-counts a signer's weight when it flips from Rejected to Accepted, letting a properly-signed block be reported as `SignersRejected` — ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

## Summary
The `StackerDBListener` in the mining node tallies signer votes for a proposed block into two independent counters, `total_weight_approved` and `total_weight_rejected`, gated by two *different* conditions depending on message type. The `Accepted` path only checks whether the signer already appears in `gathered_signatures`, never checking `responded_signers` (which the `Rejected` path uses as its own de-duplication gate). A signer that legitimately rejects a block and later legitimately accepts the same block (a state transition the signer's own local state machine explicitly allows) has its weight added to `total_weight_rejected` first and then, unconditionally, added again to `total_weight_approved`. This breaks the invariant that a signer's weight should be attributed to exactly one side of the aggregate tally.

## Finding Description
`BlockStatus` tracks per-block vote weights: [1](#0-0) 

For a `BlockResponse::Accepted`, weight is added to `total_weight_approved` gated solely on `gathered_signatures`: [2](#0-1) 

For a `BlockResponse::Rejected`, weight is added to `total_weight_rejected` gated on `responded_signers.insert(slot_id)`, and the `Accepted` path also inserts into `responded_signers` (line 465 above): [3](#0-2) 

Because the two paths use different gates:
- If a signer rejects first, `responded_signers` gains the slot id and `total_weight_rejected` is incremented. If that same signer later accepts, the `Accepted` handler only checks `gathered_signatures` (still empty for this slot), so it adds the weight to `total_weight_approved` too — with no removal/adjustment of the earlier `total_weight_rejected` contribution.
- (The reverse order — accept then reject — happens to be protected, since `responded_signers` is already populated by the `Accepted` branch, so the `Rejected` branch's gate fails and skips the increment. This asymmetry confirms the bug is a missing check on one path, not a deliberate design.)

A signer legitimately flipping from reject to accept for the *same* block proposal is an explicitly supported path in the signer's own local state machine (`LocallyRejected --> LocallyAccepted` on re-evaluation), triggered any time a prior reject reason becomes re-evaluable (e.g. transient `ConnectivityIssues`/`NoSignerConsensus`, or a corrected re-proposal). This requires no majority, no other signer's key, and no auth token — only a single signer's normal message flow reaching the node twice.

The coordinator then consumes these two counters with the rejection check evaluated first: [4](#0-3) 

Because the flipped signer's weight is now present in *both* counters, `total_weight_rejected` can cross the blocking-minority threshold (`total_weight - weight_threshold`) purely from double counting, even while `total_weight_approved` has also (or would have) reached the 70% acceptance threshold from genuinely distinct signers. Since the rejection branch is checked first, the coordinator returns `Err(NakamotoNodeError::SignersRejected{...})` for a block that in reality received a valid ≥70%-weight set of acceptances.

## Impact Explanation
This breaks the "aggregated-weight vs verified-accepts" equality explicitly called out as in-scope: the node's aggregate tally no longer reflects the actual, mutually-exclusive set of signer verdicts. The practical consequence is that a block that did in fact reach genuine 70% signer weight consensus can be reported to the miner as signer-rejected, causing the miner to abandon a validly-signed block and to run the `failed_txids`/`temporarily_excluded_txids`/`permanently_excluded_txids` exclusion logic based on corrupted tallies. This is a liveness/safety-relevant wedge in the miner-signer coordination path (in-scope `signer_coordinator.rs`), not merely a logging inconsistency, since it directly gates the `Ok`/`Err` branch that determines whether the block is pushed.

## Likelihood Explanation
No majority of signers, no signer private key, and no auth token are required — a single signer whose own local state machine legitimately transitions from `LocallyRejected` to `LocallyAccepted` for the same block (a normal, documented re-evaluation path, not misbehavior) is sufficient to corrupt the node's tally for that block. The timing window (reject arrives before the signer's own subsequent accept) is a realistic race under normal operation, e.g. when a signer initially rejects due to a transient chain-state mismatch and then accepts once its view catches up while pre-commit/threshold evaluation is still in progress on the node side.

## Recommendation
Make the `Accepted` handler in `stackerdb_listener.rs` treat `responded_signers` as the single source of truth for "has this signer's weight already been counted for this block," symmetric with the `Rejected` handler, and reconcile/decrement the opposite counter if a signer's vote changes, so a given signer's weight can only ever occupy one side of `total_weight_approved`/`total_weight_rejected` at a time.

## Proof of Concept
1. Node receives a `BlockResponse::Rejected` from signer `S` (slot `k`) for block `B`. `responded_signers.insert(k)` succeeds; `total_weight_rejected += weight(S)`.
2. Signer `S`'s local state machine subsequently re-evaluates and legitimately signs `B` (`LocallyRejected -> LocallyAccepted`), broadcasting `BlockResponse::Accepted`.
3. Node's `Accepted` handler checks only `gathered_signatures.contains_key(&k)` (false, first acceptance) and adds `weight(S)` to `total_weight_approved`, without checking that `k` is already in `responded_signers` from step 1.
4. `total_weight_approved` and `total_weight_rejected` both now include `weight(S)`; with other signers' independent votes, it is possible for `total_weight_rejected.saturating_add(weight_threshold) > total_weight` to become true even though a genuine ≥70%-weight distinct-signer acceptance set exists, causing `wait_for_block_signer_signatures`/coordinator logic (`signer_coordinator.rs` L508-546) to return `Err(SignersRejected)` instead of `Ok(signatures)`.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-519)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);

```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L508-546)
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
            } else if rejections_timer.elapsed() > *rejections_timeout {
```
