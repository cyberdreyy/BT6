### Title
Signer weight double-counted when a signer flips from rejection to acceptance for the same block - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListener` accumulates `total_weight_approved` and `total_weight_rejected` for a proposed block in a `BlockStatus` struct, gating each accumulation with a separate, independent "already counted" check. The rejection path is gated by the shared `responded_signers` set, while the acceptance path is gated by a different, disjoint map, `gathered_signatures`. Because these two gates are not unified, a single signer that first rejects a block and later accepts the same block (a state transition explicitly acknowledged as valid in this codebase, e.g. via the pre-commit/capitulation flow) has its weight counted into *both* totals, breaking the invariant that each signer's weight counts once toward the aggregate used to decide accept/reject thresholds.

### Finding Description
`BlockStatus` tracks vote weight with: [1](#0-0) 

For a `BlockResponse::Rejected` message, weight is only added if `responded_signers.insert(slot_id)` succeeds (i.e., the slot hasn't already "responded"): [2](#0-1) 

For a `BlockResponse::Accepted` message, weight is only added if the slot is not already present in the *separate* `gathered_signatures` map — `responded_signers` is not checked at all on this path: [3](#0-2) 

Because `responded_signers` and `gathered_signatures` are disjoint sets that are never cross-checked, a signer that sends a rejection first (adding its weight to `total_weight_rejected` and inserting its slot into `responded_signers`) and later sends a valid acceptance for the *same* block (e.g., changing its mind, which the documented signer flow explicitly allows — pre-commit/rejection/capitulation transitions are all legitimate in this protocol) will also have its weight added to `total_weight_approved`, since `gathered_signatures` does not yet contain that slot. The signer's weight is now counted on both sides of the ledger.

This is directly analogous to the tBTC bug pattern: two code paths (approve vs. reject accounting) were extended independently over time and share an implicit assumption ("a signer's weight is counted at most once, on one side") that a later change (adding an acceptance path with its own, differently-scoped bookkeeping) silently broke — an equality between "signed" and "counted" that is no longer enforced consistently across both paths.

### Impact Explanation
`signer_coordinator.rs`'s `wait_for_supermajority`-style loop reads these two totals directly to decide the miner's fate: reject (and permanently/temporarily exclude txids) once `total_weight_rejected + weight_threshold > total_weight`, or accept once `total_weight_approved >= weight_threshold`: [4](#0-3) 

A single signer's weight inflating both totals means the aggregated weight no longer reflects distinct verified accepts/rejects — this is exactly the "aggregated-weight vs verified-accepts" equality break and "a rejection recounted as an accept" impact category. In a close vote it can let fewer genuinely-approving distinct signers cross the 70% acceptance threshold (because a flip-flopping signer's earlier "reject" is still silently present while its later "accept" is fully counted), or conversely inflate the rejection tally used to decide which transactions get temporarily/permanently excluded, both without requiring collusion of a majority of signers — only the normal, legitimate behavior of one signer changing its vote.

### Likelihood Explanation
No malicious majority is required. A signer legitimately transitions from rejecting a proposal to later accepting a re-evaluated or re-proposed version of the same block hash (the codebase's own flows document such transitions, e.g. capitulation and pre-commit/re-evaluation logic in `docs/signer-flows.md` and `stacks-signer/src/v0/signer.rs`). Any single signer doing so under normal network conditions (message delay/reordering causing both a reject and a later accept for the same `signer_signature_hash` to be delivered and processed) triggers the double count. This requires only the natural operation of one signer, not a majority or any secret keys.

### Recommendation
Unify the "has this signer already been counted" gate between the acceptance and rejection paths in `stackerdb_listener.rs`. Concretely, before adding weight on either path, check membership in a single canonical per-slot decision record (e.g. reuse `responded_signers` for both accept and reject, or track a `HashMap<u32, Decision>` recording each signer's most-recent counted decision), and when a signer's vote flips, subtract the previously-counted weight from the old total before adding it to the new one — ensuring each signer's weight is reflected in at most one of `total_weight_approved` / `total_weight_rejected` at any time.

### Proof of Concept
1. Signer coordinator proposes block `B` with `signer_signature_hash = H`.
2. Signer `S` (slot `k`, weight `w`) sends `BlockResponse::Rejected` for `H`. `stackerdb_listener` runs the branch at lines 486-565: `responded_signers.insert(k)` succeeds, `total_weight_rejected += w`.
3. Signer `S` later re-evaluates (e.g., after a re-proposal, capitulation, or timeout-driven re-check as documented for pre-commit re-evaluation) and sends `BlockResponse::Accepted` for the same `H` with a valid signature.
4. In the accept branch (lines 386-465), the code checks `block.gathered_signatures.contains_key(&k)` — this is `false` (unrelated to `responded_signers`), so `total_weight_approved += w` executes.
5. Now `w` has been added to *both* `total_weight_rejected` and `total_weight_approved` for the same block, from a single signer — violating the assumption in `signer_coordinator.rs` that these two totals partition (at most) `total_weight` among distinct signers per decision.

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
