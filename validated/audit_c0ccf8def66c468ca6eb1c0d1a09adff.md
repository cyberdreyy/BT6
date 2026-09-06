### Title
Node-side signer coordinator double-counts a signer's weight across both rejection and acceptance tallies for the same block, allowing stale rejection weight to spuriously trip the reject threshold - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
The reported DutchAuctionLiquidator bug is a class of "amount that should be exclusive/consumed is instead retained and double-attributed," breaking an accounting equality. The analogous defect in this repo is in the node-side `StackerDBListener` message handler that tallies `total_weight_approved` / `total_weight_rejected` for a proposed block: a signer's weight, once counted toward rejection, is never removed if that same signer later accepts the identical block (a documented, legitimate transition, `LocallyRejected -> LocallyAccepted: re-evaluated`). The two counters are gated by different membership sets (`gathered_signatures` for approvals vs `responded_signers` for rejections), so the same signer's weight can end up counted on both sides simultaneously, breaking the invariant that each signer's weight is attributed to exactly one side of the tally.

### Finding Description
`BlockResponse::Accepted` handling gates weight addition on `!block.gathered_signatures.contains_key(&slot_id)`, then unconditionally does `block.responded_signers.insert(slot_id)`: [1](#0-0) 

`BlockResponse::Rejected` handling gates weight addition on `block.responded_signers.insert(slot_id)` returning `true` (i.e., first time this slot is seen in `responded_signers`): [2](#0-1) 

Because `responded_signers` is a single shared set touched by both branches, the sequence below produces an inconsistent tally:

1. Signer `S` (weight `w`) rejects block `B` (signature hash `h`). `responded_signers.insert(S)` → `true`, so `total_weight_rejected += w`. `S` is now in `responded_signers`, but not in `gathered_signatures`.
2. The signer-side state machine legitimately re-evaluates the same block (still hash `h`) and moves `S` from `LocallyRejected` to `LocallyAccepted` — this is a documented, expected transition, not an attacker action: [3](#0-2) 
3. `S` broadcasts `BlockResponse::Accepted` for the same `h`. On the node side, the check is `!block.gathered_signatures.contains_key(&slot_id)` — `S` is not yet in `gathered_signatures`, so it passes, and `total_weight_approved += w` and `S` is added to `gathered_signatures`/`responded_signers`: [4](#0-3) 

Nothing in this path subtracts `w` from `total_weight_rejected`. The result: `total_weight_approved + total_weight_rejected` can now exceed the signer's true, single vote weight, and can exceed `total_weight` in aggregate across multiple flip-floppers. The equality that should hold — "the sum of weight counted across both piles never double-counts a single signer's current vote" — is broken.

### Impact Explanation
The coordinator's wait loop checks the rejection condition before the acceptance condition: [5](#0-4) [6](#0-5) 

Because `total_weight_rejected` can be inflated with stale weight from signers who have since switched to acceptance, `block.total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight` can become true even when a legitimate 70%-weight quorum of *current* acceptances also exists. The coordinator then returns `NakamotoNodeError::SignersRejected` and discards the block instead of returning the gathered signatures — a liveness wedge: a validly, sufficiently-signed block can be treated by the miner's coordinator as rejected, stalling tenure progress even though the correct current vote distribution would have cleared the acceptance threshold.

### Likelihood Explanation
This requires no majority collusion and no special privileges: it only needs (a) at least one signer whose local reevaluation logic legitimately flips a block from `LocallyRejected` to `LocallyAccepted` for the *same* signer-signature-hash (a state machine transition explicitly documented as supported), and (b) that this signer had previously voted reject and the miner/coordinator had already recorded that rejection. A miner re-triggering signer-side reevaluation (e.g., through timing of the `should_reevaluate_reject_reason` path, or another signer's `StateMachineUpdate`/pre-commit unlocking a stalled decision) is squarely within a single miner's control plus normal signer gossip.

### Recommendation
When a signer accepts a block after having previously been recorded as rejecting the exact same `signer_signature_hash`, the coordinator must remove that signer's weight from `total_weight_rejected` before adding it to `total_weight_approved` (and vice versa for the reverse transition), so a signer's weight is attributed to at most one side of the tally at any time. Track vote state per-signer (e.g. `HashMap<slot_id, Vote>`) rather than two independently-gated sets/maps, and recompute both aggregate weights from that single source of truth.

### Proof of Concept
1. Miner proposes block `B` with signer-signature-hash `h`.
2. Signer `S` (weight `w`) validates and rejects `B` for a transient reason (e.g., timing/consensus-not-yet-observed); node's `StackerDBListener` records `total_weight_rejected += w`, `responded_signers = {S}`.
3. State changes such that `should_reevaluate_reject_reason` permits reconsideration of the identical block `h`; `S`'s local `BlockInfo` transitions `LocallyRejected -> LocallyAccepted` per the documented state machine and `S` broadcasts `BlockResponse::Accepted` for hash `h`.
4. Node's listener sees `S` not yet in `gathered_signatures`, so it adds `total_weight_approved += w`, but does not touch/decrement `total_weight_rejected`. The block status now show weight `w` credited on both approve and reject sides simultaneously for the same signer's single current vote.
5. If enough other signers' rejections (each legitimately reversed the same way) accumulate as stale rejection weight past `total_weight - weight_threshold`, `get_block_status`/the coordinator loop returns `SignersRejected` for a block that, under the correct (post-reversal) vote tally, actually cleared the 70% acceptance threshold.

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

**File:** docs/signer-flows.md (L141-147)
```markdown
    PreCommitted --> LocallyAccepted : mark_locally_accepted = WE SIGN
    Unprocessed --> LocallyRejected : mark_locally_rejected
    PreCommitted --> LocallyRejected : mark_locally_rejected
    LocallyRejected --> LocallyAccepted : re-evaluated
    LocallyAccepted --> LocallyRejected : re-evaluated
    LocallyAccepted --> GloballyAccepted : mark_globally_accepted
    LocallyRejected --> GloballyRejected : mark_globally_rejected
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L505-522)
```rust
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
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L541-545)
```rust
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```
