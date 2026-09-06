### Title
Asymmetric Response-Tracking Guards Let an Equivocating Signer's Weight Be Counted in Both Accept and Reject Tallies - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
In the node-side `StackerDBListener` that tallies signer `BlockResponse` messages for the miner coordinator, the guard used to decide whether to add a signer's weight differs between the `Accepted` and `Rejected` branches. This asymmetry lets a signer who first rejects a block and later accepts it (or vice versa, though only one direction is exploitable) have its weight counted into *both* `total_weight_approved` and `total_weight_rejected` for the same block, permanently corrupting the aggregated tallies the coordinator relies on to decide whether a block is signable or dead.

### Finding Description
`handle_block_response`-equivalent logic in the listener's message loop tracks two independent weight accumulators per block, `total_weight_approved` and `total_weight_rejected`, gated by two different guards:

- Accept path: weight is only added `if !block.gathered_signatures.contains_key(&slot_id)` [1](#0-0) , then it unconditionally inserts into both `gathered_signatures` and `responded_signers` [2](#0-1) .
- Reject path: weight is only added `if block.responded_signers.insert(slot_id)` returns true (i.e., first time this slot appears in `responded_signers`) [3](#0-2) .

Because the Accept path checks membership in `gathered_signatures` (not `responded_signers`), and the Reject path never populates `gathered_signatures`, the two accumulators are not mutually exclusive for the same slot: a signer that sends `Rejected` first (adding its weight to `total_weight_rejected` and marking `responded_signers`) and later sends `Accepted` for the same block will pass the Accept-path guard (its slot is not yet in `gathered_signatures`), so its weight is *also* added to `total_weight_approved`, while its earlier weight in `total_weight_rejected` is never removed. The single slot's weight now double-counts across both tallies. (The reverse order, Accept-then-Reject, is correctly deduplicated because the Reject-path guard checks `responded_signers`, which the Accept path already populated.)

Both tallies feed the coordinator's liveness signals: `total_weight_approved >= self.weight_threshold` wakes waiters expecting enough signatures [4](#0-3) , and `total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight` wakes waiters to treat the block as no-longer-approvable [5](#0-4) . With the reject-then-accept double count, `total_weight_rejected` can retain stale weight from a signer that has actually moved to accept, making the "rejection makes 70% impossible" condition trigger with fewer genuinely-rejecting signers than the real signer set contains. This breaks the intended equality between "aggregated rejected weight" and "verified, currently-standing rejections."

### Impact Explanation
This is a liveness-class issue on the miner/coordinator side: a single signer (which the scan rules permit, "a one-slot miner plus gossip") that equivocates — first broadcasting a `Rejected` response and later an `Accepted` response for the same `signer_signature_hash` — can leave stale rejection weight in the tally. If enough signers behave this way (or even one, depending on `weight_threshold`/`total_weight` proportions), the coordinator can be made to believe a block's approval is impossible (`total_weight_rejected + weight_threshold > total_weight`) even though the real, currently-standing set of rejecting signers is smaller. This can cause the miner to abandon a validly-approvable block/tenure attempt, functioning as a state-machine wedge on the coordinator's block-status wait loop (`get_block_status` / `propose_block`'s retry loop in `stacks-node/src/nakamoto_node/signer_coordinator.rs`), which is exactly the kind of "signer/coordinator wedged into not completing a valid signing round" impact called out as in-scope. No signature is forged and no invalid block is ever signed — the impact is purely on the accuracy of the aggregated tallies used for liveness decisions.

### Likelihood Explanation
This requires only one signer's own key and normal in-set message flow: a signer signing `Rejected` for a block and later signing `Accepted` for the exact same block hash — a sequence that is either a genuine change-of-mind (well within honest protocol variance under re-evaluation, e.g. `should_reevaluate_block`), or a directly triggerable byzantine action by a single signer/gossip participant. It does not require compromising anyone else's key, a majority, or any node-local access, so it is readily reachable by a single malicious signer.

### Recommendation
Unify the guard used by both branches so that a slot's weight can only be counted in exactly one of `total_weight_approved` / `total_weight_rejected` at a time: e.g., have the Accept path also gate on `block.responded_signers.insert(slot_id)` (or on `!block.gathered_signatures.contains_key(&slot_id) && !previously_rejected`), and when a signer flips from reject to accept (or vice versa), decrement the previous bucket's weight before adding to the new one, keeping the two totals mutually exclusive and reflective of each signer's *latest* stance.

### Proof of Concept
1. A signer (slot `S`, weight `W`) sends `BlockResponse::Rejected` for block `B`. The listener adds `W` to `total_weight_rejected` and marks `S` in `responded_signers` [6](#0-5) .
2. The same signer (still within the same block-proposal round, e.g. after `should_reevaluate_block` flips its decision) sends `BlockResponse::Accepted` for the same block `B`.
3. In the Accepted handler, `block.gathered_signatures.contains_key(&slot_id)` is `false` (slot `S` was never inserted there), so `W` is added again to `total_weight_approved`, while `total_weight_rejected` still contains `W` from step 1 [7](#0-6) .
4. Result: `total_weight_approved + total_weight_rejected > total_weight` is now possible for this block, and the "rejection makes 70% impossible" liveness check can fire on stale weight that no longer represents an active rejecting signer.

Note: I was not able to fully trace how `get_block_status` in `signer_coordinator.rs` ultimately consumes the wake-up signals (the file was only partially read before the iteration limit), so I cannot state with certainty whether the coordinator treats the "rejection threshold" wakeup as strictly terminal (aborting the proposal) versus merely re-polling; this affects the precise severity of the liveness impact and should be verified by reading `get_block_status` in full.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L467-470)
```rust
                        if block.total_weight_approved >= self.weight_threshold {
                            // Signal to anyone waiting on this block that we have enough signatures
                            cvar.notify_all();
                        }
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L501-518)
```rust
                        let rejected_pubkey = match rejected_data.recover_public_key() {
                            Ok(rejected_pubkey) => {
                                if rejected_pubkey != signer_pubkey {
                                    warn!("StackerDBListener: Recovered public key from rejected data does not match signer's public key. Ignoring.");
                                    continue;
                                }
                                rejected_pubkey
                            }
                            Err(e) => {
                                warn!("StackerDBListener: Failed to recover public key from rejected data: {e:?}. Ignoring.");
                                continue;
                            }
                        };

                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L567-574)
```rust
                        if block
                            .total_weight_rejected
                            .saturating_add(self.weight_threshold)
                            > self.total_weight
                        {
                            // Signal to anyone waiting on this block that we have enough rejections
                            cvar.notify_all();
                        }
```
