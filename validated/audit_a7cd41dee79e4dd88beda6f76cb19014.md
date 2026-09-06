Confirmed the exact accumulation logic. Now finalizing the analysis.

### Title
Signer vote flip (Rejected→Accepted) causes weight double-counting in `BlockStatus`, letting a single signer inflate `total_weight_approved` while stale `total_weight_rejected` is never reconciled - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener::run` maintains two independent, incrementally-accumulated counters per proposed block, `total_weight_approved` and `total_weight_rejected`, inside `BlockStatus`. When a signer's `BlockResponse::Rejected` is processed first, its weight is added to `total_weight_rejected` and the slot is recorded in `responded_signers`. If that same signer later sends `BlockResponse::Accepted` for the same block (a legal vote flip, or crafted by a byzantine/one-slot-controlled signer), the accepted-handling branch only guards against double counting via `gathered_signatures.contains_key(&slot_id)` — a set that is populated exclusively by the accepted branch — not via `responded_signers`. Consequently the signer's weight is added a second time into `total_weight_approved` without ever decrementing the stale entry left in `total_weight_rejected`. [1](#0-0) [2](#0-1) 

### Finding Description
The bug class mirrors the reported `collectedEther` issue: an accumulator (`total_weight_approved`/`total_weight_rejected`) is incremented by a signer's full weight without accounting for the fact that the same weight may already be reflected in the "other" bucket due to the signer's earlier message. The fix in the external report nets the increment against the amount already accounted for (`msg.value - retEther`); here the analogous missing step is to net a vote-flip against the prior tally (e.g., subtract the signer's weight from `total_weight_rejected` when they switch to `Accepted`, or vice versa).

Concretely:
- On `Accepted`: the guard is `if !block.gathered_signatures.contains_key(&slot_id)` before adding to `total_weight_approved` [3](#0-2) . This says nothing about whether the signer already rejected.
- On `Rejected`: the guard is `if block.responded_signers.insert(slot_id)` before adding to `total_weight_rejected` [2](#0-1) , so a signer who already accepted cannot inflate rejected weight again (since `responded_signers` was already populated during the accept branch) — the asymmetry is only exploitable in the reject-then-accept order.
- After a Reject→Accept flip: `total_weight_rejected` still contains the signer's stale weight (never removed), and `total_weight_approved` now also contains it. The invariant that each signer's weight is counted at most once across the two tallies (`total_weight_approved + total_weight_rejected ≤ total_weight`) is broken.

This directly affects `SignerCoordinator::get_block_status`'s decision logic in `stacks-node/src/nakamoto_node/signer_coordinator.rs`, which checks rejection first: `if block_status.total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight` before checking approval [4](#0-3) . Because the rejected bucket keeps a stale weight that the signer has since retracted by accepting, the miner can be pushed into believing a blocking minority (>30%) exists and abort/`SignersRejected` a block that in fact should be signable, even though every currently-voting signer's true, latest intent would allow the 70% approval threshold to be reached. This is a liveness wedge: the miner's/coordinator's decision is driven by an equality (`sum of per-signer weight across the two mutually-exclusive tallies ≤ total signer weight`) that the code silently breaks.

`reset_rejections` (invoked only on proposal timeout) is the sole place that clears `total_weight_rejected` and prunes `responded_signers` back to the currently-accepted set [5](#0-4) , so the stale double-count persists for the full lifetime of a block proposal round until a timeout, not merely a transient window.

### Impact Explanation
This is a High-severity liveness issue: a single signer (using only their own key/slot, no majority required) can leave the coordinator's rejected-weight tally artificially inflated after retracting their rejection, causing `get_block_status` to falsely conclude a blocking minority exists and abort a proposal that legitimate, up-to-date signer sentiment would otherwise approve. Repeated across proposals this wedges block production (a miner "wedged into never signing valid blocks" per the impact criteria), without needing a majority of colluding signers.

### Likelihood Explanation
Triggering requires only that one signer (or a party who can inject one signer's two consecutive StackerDB messages, e.g. a signer that legitimately changes its mind, or a malicious signer intentionally sending Reject then Accept) send a `BlockResponse::Rejected` followed later by a `BlockResponse::Accepted` for the same `signer_signature_hash` while the proposal round is still open. No cryptographic forgery, no majority, and no access to another party's key is needed — this is entirely within the normal message surface a signer is expected to use, and vote changes are a realistic operational scenario (e.g. a signer that initially rejects due to a transient chain-state check, then re-validates and accepts once conditions change, as described in `docs/signer-flows.md` sections 5–6).

### Recommendation
When processing `BlockResponse::Accepted` for a signer already present in `responded_signers` due to a prior `Rejected`, subtract that signer's weight from `total_weight_rejected` (and vice versa for a hypothetical Accept→Reject flip) before adding it to the new bucket, so that a signer's weight is always reflected in at most one bucket — analogous to computing the net delta (`msg.value - retEther`) instead of blindly adding `msg.value`. Concretely, replace the `gathered_signatures.contains_key` guard in the `Accepted` branch with a check against `responded_signers`, and if the signer flips from rejected to accepted, perform `total_weight_rejected = total_weight_rejected.saturating_sub(signer_entry.weight)` prior to adding to `total_weight_approved`.

### Proof of Concept
1. Node proposes block B; `StackerDBListenerComms::insert_block` initializes `BlockStatus{total_weight_approved:0, total_weight_rejected:0, responded_signers:{}}`.
2. Signer S (slot_id=5, weight=W) sends `BlockResponse::Rejected` for B. Handler: `responded_signers.insert(5)` → true, `total_weight_rejected += W`.
3. Signer S later sends `BlockResponse::Accepted` for B (e.g., after re-evaluating chainstate). Handler checks `gathered_signatures.contains_key(&5)` → false (never populated), so `total_weight_approved += W`; `gathered_signatures.insert(5, sig)`; `responded_signers.insert(5)` (no-op, already present).
4. Now `total_weight_rejected == W` (stale, from step 2) and `total_weight_approved == W` (from step 3) simultaneously — S's weight double-counted, and never decremented from `total_weight_rejected`.
5. `SignerCoordinator::get_block_status` evaluates `total_weight_rejected.saturating_add(weight_threshold) > total_weight` first; combined with other signers' genuine rejections, the stale `W` can push this condition true even though S has since approved, causing the coordinator to return `NakamotoNodeError::SignersRejected` for a block that should otherwise reach the 70% approval threshold.

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
