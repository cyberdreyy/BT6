### Title
Miner double-counts a signer's weight in both `total_weight_approved` and `total_weight_rejected` when a signer changes its vote - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener` (the miner-side vote tally used by `SigningCoordinator`) tracks a signer's contribution to a block's approval and rejection tallies using two different, inconsistent membership checks. A `BlockResponse::Rejected` uses the shared `responded_signers` set to decide whether to add weight, while a subsequent `BlockResponse::Accepted` from the *same* signer uses a separate `gathered_signatures` map to decide whether to add weight. Because these two data structures are not kept in sync, a single signer that first rejects and later accepts the same block (a supported, intentional flow per the changelog note "a signer will reconsider a block proposal it previously rejected") has its weight counted in *both* `total_weight_rejected` and `total_weight_approved` simultaneously, which is exactly the "double accounting via two different tracking keys for the same actor" bug class from the reference report.

### Finding Description
In the accept branch, weight is added only if the signer's slot is not yet present in `gathered_signatures`: [1](#0-0) 

In the reject branch, weight is added only if the signer's slot is not yet present in `responded_signers`: [2](#0-1) 

The `BlockStatus` struct keeps `responded_signers`, `gathered_signatures`, `total_weight_approved`, and `total_weight_rejected` as separate fields, with no invariant enforced between them: [3](#0-2) 

Walking the sequence for one signer `S` with weight `w`:
1. `S` sends `Rejected` first: `responded_signers.insert(slot_id)` returns `true` (first insertion) → `total_weight_rejected += w`.
2. `S` later sends `Accepted` for the *same block* (a legitimate reconsideration flow, cf. stacks-signer's own "signer will reconsider a block proposal it previously rejected" behavior, `stacks-signer/CHANGELOG.md:178-180`): the check is `!block.gathered_signatures.contains_key(&slot_id)`, which is still `true` (nothing has been inserted into `gathered_signatures` yet) → `total_weight_approved += w`.

Result: `S`'s weight `w` is now counted in **both** `total_weight_approved` and `total_weight_rejected` for the same block, even though `S` has exactly one live vote (its most recent one, `Accepted`). The stale reject-weight is never decremented because the code only ever adds to `total_weight_rejected`/`total_weight_approved`; there is no path that removes a signer's earlier contribution when it changes its vote.

This is consumed directly by `SigningCoordinator::wait_for_signatures` style loop in `signer_coordinator.rs`, which checks the rejection threshold first, then the approval threshold, purely from these two accumulated (and now inflated/incoherent) integers: [4](#0-3) 

The accept-side check (`gathered_signatures.contains_key`) and reject-side check (`responded_signers.insert`) are not the same predicate over the same set — this is the missing-check/two-different-keys-for-one-entity flaw analogous to the `AppStorage.stakeCollected[][]` dual-purpose accounting bug in the report.

### Impact Explanation
`total_weight_rejected` can retain phantom weight from signers who have since switched to `Accepted`, and `total_weight_approved` simultaneously grows because the accept-side dedup key (`gathered_signatures`) never observed that slot before. This corrupts the equality the coordinator relies on — "aggregated weight vs. verified/current accepts" — in two ways:
- `total_weight_rejected` can spuriously cross the `> total_weight - weight_threshold` blocking-minority line using weight from signers who no longer actually reject the block, causing the miner to needlessly abort/`SignersRejected` a block that legitimately has (or would have) enough real support — a liveness degradation for block production.
- Conversely, because the two totals are tracked independently, it's possible for `total_weight_approved` to reach `weight_threshold` while a chunk of that "approved" weight also still sits inside `total_weight_rejected`, meaning the miner's accounting of "who actually currently backs this block" is not a faithful reflection of the true, current signer votes — the same class of accounting corruption as the report (weight double-booked across two different counters for one entity).

This does not require a signer majority; a single signer flipping its vote (reject → accept) on a proposal is sufficient to trigger the double count, satisfying the "not requiring a majority" scoping constraint.

### Likelihood Explanation
This is fully reachable with a single non-majority signer, using standard protocol messages (`BlockResponse::Rejected` followed later by `BlockResponse::Accepted` for the same `signer_signature_hash`), and the reconsideration flow is explicitly supported/expected by the signer software itself (per the CHANGELOG entry allowing reconsideration of certain rejections). No malicious signing key beyond the one signer's own key is needed, and no StackerDB-sync trickery is required — this is a logic defect in how the miner tallies distinct `BlockResponse` message types against two different membership sets.

### Recommendation
Track a single canonical "current vote" per `slot_id` (e.g., one `HashMap<u32, Vote>` where `Vote` is `Accepted(weight)` or `Rejected(weight)`), and derive `total_weight_approved`/`total_weight_rejected` by summing over that single source of truth, removing/adjusting the previous contribution whenever a signer's vote changes. At minimum, the accept branch should check `responded_signers` (the same set used by the reject branch) rather than `gathered_signatures`, and when a signer's vote flips, its prior tally contribution must be subtracted before the new one is added.

### Proof of Concept
1. Miner proposes block `B` with `signer_signature_hash = H`; `StackerDBListener` creates `BlockStatus` for `H` with `total_weight_approved = 0`, `total_weight_rejected = 0`.
2. Signer `S` (weight `w`) broadcasts `SignerMessageV0::BlockResponse(BlockResponse::Rejected(...))` for `H`. In the reject handler, `block.responded_signers.insert(slot_id)` returns `true` → `total_weight_rejected = w` (`stackerdb_listener.rs:515-519`).
3. Signer `S` reconsiders and later broadcasts `SignerMessageV0::BlockResponse(BlockResponse::Accepted(...))` for the same `H` (a scenario the signer software supports for certain reject reasons). In the accept handler, `block.gathered_signatures.contains_key(&slot_id)` is `false` (never touched by the reject path) → `total_weight_approved = w` (`stackerdb_listener.rs:443-446`).
4. Now `BlockStatus{ total_weight_approved: w, total_weight_rejected: w }` for the same block, with a single signer's weight counted on both sides — demonstrating the double-accounting defect that `signer_coordinator.rs` then evaluates against the rejection/approval thresholds using these corrupted numbers.

Note: I was unable to execute this scenario in a live/test harness (ask-only mode, no execution access); the above is derived purely from static code review of the cited functions. If further confirmation via test execution or a live signer/miner setup is desired, a Devin session with repository execution access would be needed to build a reproduction test.

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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L487-545)
```rust
            if rejections != block_status.total_weight_rejected {
                rejections = block_status.total_weight_rejected;
                let (rejections_step, new_rejections_timeout) = self
                    .block_rejection_timeout_steps
                    .range((Included(0), Included(rejections)))
                    .last()
                    .ok_or_else(|| {
                        NakamotoNodeError::SigningCoordinatorFailure(
                            "Invalid rejection timeout step function definition".into(),
                        )
                    })?;
                rejections_timeout = new_rejections_timeout;
                info!("Number of received rejections updated, resetting timeout";
                                    "rejections" => rejections,
                                    "rejections_timeout" => rejections_timeout.as_secs(),
                                    "rejections_step" => rejections_step,
                                    "rejections_threshold" => self.total_weight.saturating_sub(self.weight_threshold));

                counters.set_miner_current_rejections_timeout_secs(rejections_timeout.as_secs());
                counters.set_miner_current_rejections(rejections);
            }

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
