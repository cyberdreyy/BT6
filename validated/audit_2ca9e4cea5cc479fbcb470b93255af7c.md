### Title
Stale rejection weight is never cleared when a signer later accepts, causing `total_weight_rejected` and `total_weight_approved` to double-count the same signer and wrongly trigger `SignersRejected` on a block with sufficient real support - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
The miner-side `StackerDBListener` aggregates per-signer `BlockResponse` messages into a shared `BlockStatus{total_weight_approved, total_weight_rejected, responded_signers, gathered_signatures}` tally that `SignerCoordinator::wait_for_signer_signature` (or the equivalent poll loop) uses to decide whether to keep waiting, accept the block, or abort it via `NakamotoNodeError::SignersRejected`. The rejection path and the acceptance path use two different, non-mutually-exclusive gates (`responded_signers` vs `gathered_signatures`) to decide whether to add a signer's weight, so a signer that first rejects and later reconsiders and accepts (an explicitly supported, non-malicious flow in this codebase, see `should_reevaluate_reject_reason`) has its weight counted in *both* `total_weight_rejected` and `total_weight_approved`. Because the rejected-weight entry is never cleared, `total_weight_rejected` can remain permanently inflated with weight from signers who no longer reject the block, which can push the coordinator's rejection check (`total_weight_rejected + weight_threshold > total_weight`) over the line and cause the miner to give up on a block that in fact has (or will have) sufficient real signer support.

### Finding Description
`BlockStatus` tracks two independent sets:
- `responded_signers: HashSet<u32>` – slot ids that have "responded" in some form
- `gathered_signatures: BTreeMap<u32, MessageSignature>` – slot ids that have supplied a valid acceptance signature [1](#0-0) 

In the acceptance branch, weight is added to `total_weight_approved` only if the slot is not already present in `gathered_signatures`; `responded_signers` is inserted unconditionally but never consulted before crediting `total_weight_approved`: [2](#0-1) 

In the rejection branch, weight is only added to `total_weight_rejected` when `responded_signers.insert(slot_id)` succeeds (i.e., the slot has not previously "responded"): [3](#0-2) 

Consider signer S at slot `k`, weight `w`:
1. S sends `Rejected` first. `responded_signers.insert(k)` succeeds (set was empty for `k`), so `total_weight_rejected += w`.
2. S later re-evaluates and sends `Accepted` for the same block (a supported flow — the signer-side `should_reevaluate_reject_reason`/`should_reevaluate_block` logic explicitly allows a signer to switch from reject to accept for certain reject reasons, and stacks-signer's own `add_block_signature` even deletes the corresponding rejection row when a signature is later produced). In the coordinator's independent bookkeeping, the acceptance branch checks only `gathered_signatures.contains_key(&k)`, which is false (S never accepted before), so `total_weight_approved += w` as well.
3. `total_weight_rejected` is *never* decremented — there is no code path in this file that removes a slot from the reject tally once added — so S's weight `w` now counts toward both `total_weight_rejected` and `total_weight_approved` simultaneously, even though S currently supports the block.

This breaks the intended equality that a signer's weight should count toward exactly one side of the aggregated tally, i.e. the "aggregated-weight vs verified-accepts" invariant the coordinator relies on to make progress/abort decisions.

### Impact Explanation
The coordinator's polling loop evaluates the reject condition before the accept condition: [4](#0-3) 

Because the stale, double-counted rejected weight is never cleaned up, `total_weight_rejected` can be pushed past `total_weight - weight_threshold` purely from signers who have since reconsidered and are actively signing the block. When that happens the miner takes the `SignersRejected` branch and permanently abandons/penalizes the block (including computing `permanently_excluded_txids`/`temporarily_excluded_txids` off this inflated tally) even though the real, current set of accepting signers may already meet or be about to meet the 70% acceptance threshold. This is a liveness fault: legitimate blocks with sufficient real signer support can be wrongly killed by the miner because of stale rejection bookkeeping, and legitimate transactions can be temporarily or permanently excluded based on inflated `failed_txids` weight tied to the same stuck accounting.

Note that this does not let an *invalid* or under-signed block reach the chain — `NakamotoBlockHeader::verify_signer_signatures` independently re-derives real signer weight from cryptographic signatures over the block when it is finally accepted — so the safety of on-chain block acceptance is not broken. The impact is confined to miner-side liveness/throughput.

### Likelihood Explanation
No malicious actor or majority is required: this triggers under the ordinary, explicitly supported flow where a single signer rejects a proposal and later reconsiders and signs it (a documented and intentional feature — "For some rejection reasons, a signer will reconsider a block proposal that it previously rejected"). Any one signer emitting `Rejected` then `Accepted` for the same `signer_signature_hash` is sufficient to permanently inflate `total_weight_rejected` by that signer's weight for the lifetime of that `BlockStatus` entry.

### Recommendation
Make the rejected/approved tallies mutually exclusive per slot id, e.g.:
- Track a single `HashMap<u32, ResponseKind>` (or reuse `responded_signers`) that records the *current* response type per slot, and when a slot flips from `Rejected` to `Accepted` (or vice versa), subtract the old contribution before adding the new one to the corresponding tally.
- Equivalently, recompute `total_weight_rejected`/`total_weight_approved` from the current per-slot response map rather than incrementally accumulating them, so a slot's weight can never be counted on both sides at once.

### Proof of Concept
1. Configure a reward set with signer S at slot `k` with weight `w`.
2. S broadcasts `BlockResponse::Rejected` for block `B` (any legitimate rejection reason that is later reconsiderable). Coordinator's `total_weight_rejected += w`; `responded_signers` now contains `k`.
3. S re-evaluates (per `should_reevaluate_reject_reason`) and later broadcasts `BlockResponse::Accepted(signature)` for the same `B`. Since `gathered_signatures` does not yet contain `k`, coordinator's `total_weight_approved += w` as well; `total_weight_rejected` is left unchanged at its previous value.
4. Repeat/accumulate with enough signers reconsidering (or combine with genuine minority rejections) until `total_weight_rejected.saturating_add(weight_threshold) > total_weight` even though `total_weight_approved` is climbing toward (or has reached) `weight_threshold` from the same signers who "rejected" earlier.
5. Observe the coordinator returns `Err(NakamotoNodeError::SignersRejected{...})` at [5](#0-4)  before ever reaching the `total_weight_approved >= weight_threshold` branch, discarding a block that in fact has sufficient current signer support.

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
