### Title
Vote-switch double counting breaks the aggregated-weight vs. verified-accepts equality in `StackerDBListener` - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener` tallies `total_weight_approved` and `total_weight_rejected` for a block using two *different* de-duplication sets (`gathered_signatures` for accepts, `responded_signers` for rejects), rather than one canonical per-signer vote record. A single signer who first rejects and later accepts the same block (a legitimate, gossip-reachable sequence) has its weight added to `total_weight_rejected` and *never removed*, while the later accept is unconditionally added to `total_weight_approved` as well — because the accept path only checks `gathered_signatures`, which the reject path never touches. This mirrors the Zivoe bug class: a state transition (reject → accept) fails to subtract the previous contribution before/while adding the new one, leaving a stale, inflated tally that the miner's consensus decision (`SigningCoordinator`/`signer_coordinator.rs`) relies on as if it were an exact, mutually exclusive weight partition.

### Finding Description
`BlockStatus` tracks per-block tallies additively: [1](#0-0) 

On `Accepted`, the weight is only added if the slot is not already in `gathered_signatures`; `responded_signers` is then unconditionally inserted, but is never *checked* before adding weight: [2](#0-1) 

On `Rejected`, the weight is only added if the slot is not already in `responded_signers` (the same shared set that `Accepted` also inserts into, but which `Accepted`'s weight-gate does not consult): [3](#0-2) 

Trace both orderings for one signer slot `S` with weight `w`:

- **Accept then Reject** (protected): Accept adds `w` to `total_weight_approved`, inserts `S` into both `gathered_signatures` and `responded_signers`. The later Reject checks `responded_signers.insert(S)` → already present → returns `false` → `total_weight_rejected` is *not* incremented. Correct behavior.
- **Reject then Accept** (broken): Reject checks `responded_signers.insert(S)` → not present → `true` → `total_weight_rejected += w`. The later Accept checks `gathered_signatures.contains_key(S)` → `false` (reject never touched this map) → `total_weight_approved += w` again, and only *then* inserts `S` into `responded_signers` (a no-op, since it's already there from the reject).

The result: signer `S`'s weight `w` is now counted in **both** `total_weight_rejected` and `total_weight_approved` simultaneously, and the stale rejection contribution is never retracted even though `S` has switched its vote to accept. This breaks the intended invariant that `total_weight_approved + total_weight_rejected` reflects the current, mutually exclusive stance of each signer weighted against `total_weight` — the exact "aggregated-weight vs verified-accepts" equality called out as a target class.

These tallies feed the miner's threshold decisions directly in the coordinator: [4](#0-3) 

### Impact Explanation
Because `total_weight_rejected` retains stale weight from signers who have since switched to accepting, the rejection-threshold check (`block_status.total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight`) can fire based on votes that no longer represent any signer's live position. This can cause the miner to spuriously treat a block as rejected by a blocking minority (`NakamotoNodeError::SignersRejected`, txid exclusion), discarding a block/set of transactions the actual current signer set would have approved — a liveness degradation on block production driven purely by ordinary vote-switch gossip from a single signer slot, no majority collusion required. It also means the two weight tallies used to gate both accept and reject outcomes are no longer a true partition of `total_weight`, undermining the soundness of the aggregated-weight accounting the coordinator relies on to make consensus-affecting decisions.

### Likelihood Explanation
Vote switching (reject-then-accept) is a normal, expected occurrence: a signer may reject a proposal based on a stale view (e.g., timing, pending burn-block info, or a transient validation failure) and then send a corrected `Accepted` response once its view updates — no adversarial behavior or majority collusion is needed, only ordinary message gossip via StackerDB from one signer.

### Recommendation
Track a single canonical last-vote-per-slot state (e.g., `HashMap<u32, VoteKind>`) instead of two independently-gated sets/maps. When a signer's vote transitions from Rejected to Accepted (or vice versa), subtract the previous contribution from the old tally before adding the new weight to the new tally, so `total_weight_approved` and `total_weight_rejected` always reflect each signer's current vote exactly once.

### Proof of Concept
1. Node's `StackerDBListener` is tracking a `BlockStatus` for a proposed block with signer slot `S` (weight `w`).
2. Signer `S` broadcasts `SignerMessageV0::BlockResponse(BlockResponse::Rejected(...))` for the block. `responded_signers.insert(S)` succeeds → `total_weight_rejected += w`.
3. Signer `S` later broadcasts `SignerMessageV0::BlockResponse(BlockResponse::Accepted(...))` for the same block (e.g., after receiving updated info). `gathered_signatures.contains_key(S)` is `false` → `total_weight_approved += w` as well.
4. Now `total_weight_rejected` still includes `w` (never decremented) and `total_weight_approved` also includes `w`. If enough other signers accept, `total_weight_rejected` may already be inflated toward the blocking-minority threshold in `poll_for_block_signatures` (`signer_coordinator.rs` lines 509-518), causing an erroneous `SignersRejected` outcome despite `S`'s current vote being an accept.

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
