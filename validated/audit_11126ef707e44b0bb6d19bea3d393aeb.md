### Title
Stale rejection weight never cleared when a signer flips to Accept, causing the miner's aggregated tally to diverge from its actual verified-accepts - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
The `StackerDBListener`'s per-block weight tally (`total_weight_approved` / `total_weight_rejected`) can end up double-counting a single signer's weight on *both* sides of the tally when that signer first rejects a block and later signs (accepts) the same block. This is the same bug class as the `View::queryAssetBalances` finding: one code path (the acceptance handler) computes a value without accounting for a state transition (the earlier rejection) that the canonical/other path does account for — breaking the "aggregated-weight vs verified-accepts" equality the coordinator relies on to decide whether a block should be globally rejected or accepted.

### Finding Description
In `stacks-node/src/nakamoto_node/stackerdb_listener.rs`, the message-processing loop maintains, per proposed block, a `total_weight_approved` and `total_weight_rejected` counter guarded by a single `responded_signers` set.

- On `BlockResponse::Rejected`: the weight is only added if `block.responded_signers.insert(slot_id)` succeeds (i.e., first response from that slot): [1](#0-0) 

- On `BlockResponse::Accepted`: the weight is added purely based on `!block.gathered_signatures.contains_key(&slot_id)`, with **no check of `responded_signers`** at all: [2](#0-1) 

Because the `Accepted` branch never consults `responded_signers` (and never subtracts anything from `total_weight_rejected`), a signer that first rejects a block (adding its weight to `total_weight_rejected` and marking `responded_signers`) and later legitimately reconsiders and signs the same block (a flow explicitly supported by the signer-side protocol — see the "reject_then_accept" reconsideration behavior documented at `stacks-signer/src/signerdb.rs` around the `reject_then_accept` test) will have its weight counted in `total_weight_approved` as well, while the stale weight remains in `total_weight_rejected` forever. No code path ever decrements `total_weight_rejected` when a slot later signs.

This breaks the invariant the coordinator polling loop assumes: that a signer's weight is attributed to exactly one side of the tally. The consumer of this state, `SignerCoordinator::run` in `stacks-node/src/nakamoto_node/signer_coordinator.rs`, checks the reject-threshold condition first and returns `Err(SignersRejected{...})` as soon as `total_weight_rejected.saturating_add(weight_threshold) > total_weight`: [3](#0-2) 

only falling through to the accept check afterward: [4](#0-3) 

With the stale-rejection double-count, the "impossible to reach 70%" condition can be satisfied even though the *true* aggregated weight of currently-signed slots (`gathered_signatures`) has already reached, or is about to reach, the real acceptance threshold — because one signer's weight is inflating `total_weight_rejected` even though that same signer has since signed. Since the reject-check runs first in the loop and returns immediately, the miner can wrongly declare a block globally rejected (`SignersRejected`) and abandon it, even though the same weight is present and counted correctly in `total_weight_approved`/`gathered_signatures`.

Because `gathered_signatures` and `total_weight_approved` are internally self-consistent (each is incremented exactly once per unique slot the first time it signs), this bug does not let the node accept an insufficiently-signed block — the node-side `verify_signer_signatures` (chainstate) recomputes weight strictly from the real signature vector and is unaffected. The break is confined to the coordinator's local tally used to decide *when to give up on a block as rejected*, i.e., it is a liveness/miscounting issue in the miner's local bookkeeping, not a signature-forgery or invalid-block-acceptance issue.

### Impact Explanation
This falls under the "rejection recounted" bug class named in the rules: the same signer's weight is simultaneously counted as a rejection and (later) as an acceptance, with the rejection never retracted. The practical consequence is that the coordinator's `SignersRejected` branch can fire based on a corrupted/stale tally, causing the miner to prematurely abandon a block that in reality has (or would have) reached the real 70% approval threshold from `gathered_signatures`. This wedges block production for that attempt (the miner drops the block and must build a new one), a liveness degradation localized to the coordinator/miner's decision loop rather than a safety break (no invalid/non-canonical block is ever pushed, since the node's independent `verify_signer_signatures` recomputation is unaffected).

### Likelihood Explanation
Triggering requires only a single ordinary signer (no majority, no other signer's key) reconsidering its vote — rejecting a proposal and later, per the protocol's own documented and tested "reject then reconsider" flow, signing the same block once conditions change (e.g., a conflicting block going stale, or validation completing after an earlier rejection). This is a normal single-signer state transition already exercised by the codebase's own tests (`reject_then_accept` in `stacks-signer/src/signerdb.rs`), so the sequence needed to trigger the stale double-count on the miner's `StackerDBListener` tally is readily reachable in ordinary operation, not merely a contrived adversarial scenario.

### Recommendation
When processing a `BlockResponse::Accepted` message, check whether the slot previously contributed to `total_weight_rejected` (e.g., via `responded_signers` combined with a per-slot record of which counter it was attributed to) and, if so, subtract its weight from `total_weight_rejected` before adding it to `total_weight_approved` (and symmetrically for the reverse transition, if allowed). Alternatively, always recompute `total_weight_approved` / `total_weight_rejected` from a canonical per-slot "current decision" map rather than incrementally accumulating both counters independently, so that each signer's weight is attributed to at most one bucket at any point in time.

### Proof of Concept
Conceptual sequence (no code execution performed; derived purely from reading the cited functions):
1. Miner proposes block B; signer S (weight w) initially rejects B → `handle_signer_messages` inserts `slot(S)` into `responded_signers` and adds `w` to `total_weight_rejected`.
2. Other signers' weight brings `total_weight_approved` to `weight_threshold - w` (i.e., one signer short of the real 70% threshold).
3. S reconsiders (a supported protocol flow) and signs B, sending `BlockResponse::Accepted`. The Accepted-branch code adds `w` to `total_weight_approved` (now reaching `weight_threshold`) without checking or clearing `responded_signers`/`total_weight_rejected`.
4. `total_weight_rejected` still holds `w` from step 1. If `total_weight_rejected.saturating_add(weight_threshold) > total_weight` evaluates true (e.g., because other signers also rejected, and this stale `w` tips it over) at the moment `SignerCoordinator::run` polls, the coordinator returns `Err(SignersRejected{...})` — even though `total_weight_approved` (and the real `gathered_signatures`) had already reached the genuine acceptance threshold from step 3.

This demonstrates the aggregated-weight tally in `stackerdb_listener.rs` diverging from the true, verifiable set of accepted signatures, exactly mirroring the analog bug class (a value computed by one path failing to reflect a state change accounted for by another, canonical path).

### Citations

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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-540)
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
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L541-545)
```rust
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```
