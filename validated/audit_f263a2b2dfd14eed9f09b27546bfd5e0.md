Confirmed the exact code path. Now I have enough to write the analog finding.

The `BlockStatus` struct at `stacks-node/src/nakamoto_node/stackerdb_listener.rs:70-82` tracks `total_weight_approved` and `total_weight_rejected` as two independently-accumulated tallies for the same set of signers, gated by two different membership witnesses: `gathered_signatures` (keyed by slot_id, guards the accept path) and `responded_signers` (a shared set, guards the reject path). These two witnesses are never reconciled against each other before adding weight.

**Accept path** (lines 443-464): weight is added to `total_weight_approved` only if `!block.gathered_signatures.contains_key(&slot_id)`, then `gathered_signatures.insert(...)` and `responded_signers.insert(slot_id)` both happen unconditionally afterward.

**Reject path** (lines 515-518): weight is added to `total_weight_rejected` only if `block.responded_signers.insert(slot_id)` (i.e., first time this slot appears in `responded_signers`).

Consequence: if a signer's Reject arrives first, `responded_signers` gains the slot_id and `total_weight_rejected` is incremented. If that same signer later sends an Accept for the same block (legitimate under the signer's own state machine — the docs at `docs/signer-flows.md` sections 5–6 explicitly describe rejections as "revocable" and signers can flip after re-evaluation), the accept path only checks `gathered_signatures`, which is still empty for that slot — so `total_weight_approved` is *also* incremented, on top of the already-counted `total_weight_rejected`. The reject tally is never decremented. This double counts that signer's weight across both totals, corrupting the reject/approve equality the miner-side threshold logic in `signer_coordinator.rs:487-545` (`compute_block_status`/`get_block_status` consumer of `BlockStatus`) relies on to decide `SignersRejected` vs. accept-and-broadcast.### Title
Miner-side StackerDB signer-response tally double-counts a signer who flips from Reject to Accept, corrupting the approval/rejection weight equality - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`BlockStatus` (miner-side signer response aggregator) maintains two independently-gated weight tallies for the same block — `total_weight_approved` and `total_weight_rejected` — but uses two different, unreconciled membership witnesses to decide whether to add a signer's weight to each: `gathered_signatures` (keyed by slot_id) guards the accept path, and `responded_signers` (a shared set) guards the reject path. A signer who first rejects and later accepts the same block gets its weight counted into *both* totals, because the accept-path guard never checks whether that slot already contributed to the reject tally.

### Finding Description
`BlockStatus` is defined at `stacks-node/src/nakamoto_node/stackerdb_listener.rs:70-82` with `responded_signers: HashSet<u32>`, `gathered_signatures: BTreeMap<u32, MessageSignature>`, `total_weight_approved: u32`, and `total_weight_rejected: u32`.

Accept handling (`BlockResponse::Accepted`, lines ~386-465):
```
if !block.gathered_signatures.contains_key(&slot_id) {
    block.total_weight_approved = block.total_weight_approved.saturating_add(signer_entry.weight);
    ...
}
block.gathered_signatures.insert(slot_id, signature);
block.responded_signers.insert(slot_id);
```
The only guard against re-counting an Accept is `gathered_signatures.contains_key(&slot_id)`.

Reject handling (`BlockResponse::Rejected`, lines ~486-518):
```
if block.responded_signers.insert(slot_id) {
    block.total_weight_rejected = block.total_weight_rejected.saturating_add(signer_entry.weight);
    ...
}
```
The only guard here is `responded_signers.insert(slot_id)` (true the first time this slot appears in the set at all, regardless of whether that appearance was via an accept or a reject).

Sequence: Reject arrives first → `responded_signers` gains `slot_id`, `total_weight_rejected += weight`. The signer's `gathered_signatures` is untouched. That signer later re-evaluates and sends an Accept for the *same* block (a legitimate, documented behavior — the signer-side flow explicitly allows revisiting a decision; see `docs/signer-flows.md` sections 5–6, which state "a rejection is a revocable opinion" and describe pre-commit/signature re-evaluation paths in `stacks-signer/src/v0/signer.rs`, e.g. `handle_block_pre_commit` at lines 1250-1345 and `store_and_process_block_signature` at lines 2442-2539). When that Accept reaches the miner-side listener, the guard `!gathered_signatures.contains_key(&slot_id)` is still true (nothing was ever inserted there for this signer), so `total_weight_approved` is *also* incremented by the same signer's weight — while `total_weight_rejected` is never decremented.

The result: one signer's weight is now counted in both `total_weight_approved` and `total_weight_rejected` simultaneously for the same block. This breaks the intended equality that a signer's weight should count toward at most one of the two mutually-exclusive tallies at any time.

This tally feeds directly into the miner's threshold decision in `stacks-node/src/nakamoto_node/signer_coordinator.rs` (lines 482-562 and 400-410): the loop compares `total_weight_rejected` against the blocking-minority threshold and `total_weight_approved` against `weight_threshold` to decide `SignersRejected` vs. returning the gathered signatures as an accepted block. Because a flipped signer inflates both counters, the miner's rejection-vs-acceptance bookkeeping no longer reflects each signer's actual, current vote.

### Impact Explanation
This does not by itself let a single signer forge signatures or create a majority — the underlying signatures/rejections are still individually verified (`signature.verify`, `recover_public_key`). The impact is a miscount: a signer's already-counted rejection weight is never retracted when that signer later accepts, so `total_weight_rejected` can remain permanently inflated relative to reality (a "stale rejection" that stays counted forever), while the same signer's weight also legitimately counts toward `total_weight_approved`. Depending on which threshold is checked first and how close weights are to the >30%/≥70% boundaries, this can:
- cause the miner to declare `SignersRejected` (transaction exclusion / rejection path) even though the signer set no longer actually holds a blocking >30% rejection once the flipped signer's true intent is "accept", stalling block production (a liveness degradation on the miner side), or
- otherwise produce inconsistent/incorrect telemetry and threshold evaluation that a one-slot signer (plus its own gossip messages) can trigger deterministically, without needing majority collusion.

This matches the report's underlying bug class: two witnesses meant to describe the same fact (this signer's current vote) are tracked via separate, non-reconciled guards, and nothing enforces that a signer's weight is retracted from one bucket when it moves to the other.

### Likelihood Explanation
Trivially triggerable by any single signer (or a signer-controlled/gossip-relayed message pair) that first sends a Reject then an Accept for the same `signer_signature_hash` — both are individually valid, signed messages, requiring no majority, no other signer's key, and no auth-token/local access, exactly matching the class of "one-slot miner (plus gossip)"-triggerable finding the rules call for. The re-evaluation flow that produces a legitimate Reject→Accept flip already exists in the signer's own logic (chainstate re-checks in `check_block_against_signer_db_state`, conflict resolution in `handle_block_pre_commit`), so this is not merely a theoretical ordering — it is a scenario the codebase's own signer design anticipates and documents as valid ("a rejection is a revocable opinion").

### Recommendation
When processing a `BlockResponse::Accepted` for a slot that is already present in `responded_signers` due to a prior rejection, first decrement `total_weight_rejected` by that signer's weight (and remove any of its `failed_txids` contributions) before adding to `total_weight_approved`. Symmetrically, if an Accepted message is later followed by a Rejected one for the same slot (if that path is intended to be allowed at all), the accepted weight and `gathered_signatures` entry must be retracted before adding to `total_weight_rejected`. In general, track a single per-slot "current vote" state (Accepted/Rejected) rather than two independently-incremented saturating counters, and derive `total_weight_approved`/`total_weight_rejected` by summing over that per-slot state, so a slot's weight can never appear in both totals at once.

### Proof of Concept
1. Miner proposes block `B` with `signer_signature_hash = H`.
2. Signer `S` (slot `k`, weight `w`) sends `BlockResponse::Rejected` for `H`. Miner-side: `responded_signers.insert(k)` succeeds → `total_weight_rejected += w`.
3. Signer `S` re-evaluates (e.g., because a conflicting sibling it had earlier signed goes stale, or because it re-validates and now judges `B` valid) and sends `BlockResponse::Accepted` for the same `H`.
4. Miner-side accept handler checks `!gathered_signatures.contains_key(&k)` → true (never populated) → `total_weight_approved += w`; `gathered_signatures.insert(k, sig)`; `responded_signers.insert(k)` (no-op, already present).
5. Now `total_weight_rejected` still includes `w` from step 2, and `total_weight_approved` also includes `w` from step 4 — the same signer's weight is double-counted across the two mutually-exclusive tallies, verifiable by inspecting `BlockStatus.total_weight_approved` and `BlockStatus.total_weight_rejected` after step 4 (`stacks-node/src/nakamoto_node/stackerdb_listener.rs:443-518`). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L482-546)
```rust

                    continue;
                }
            };

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
            } else if rejections_timer.elapsed() > *rejections_timeout {
```
