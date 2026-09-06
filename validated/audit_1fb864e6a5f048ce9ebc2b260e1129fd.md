### Title
Stale rejection weight not cleared on signer vote-switch inflates `total_weight_rejected`, letting a one-signer flip prematurely signal block failure - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener::run` (the node's mining-coordinator loop) tracks two independent, monotonically-increasing tallies per proposed block, `total_weight_approved` and `total_weight_rejected`, each guarded by its own de-duplication set (`gathered_signatures` for accepts, `responded_signers` shared for both). When a signer legitimately switches its vote from `Rejected` to `Accepted` for the same block — a transition the signer-side state machine explicitly allows (`LocallyRejected --> LocallyAccepted : re-evaluated`) — the coordinator adds the signer's weight to `total_weight_approved` but never removes it from `total_weight_rejected`, because there is no code path that decrements a prior rejection when a later acceptance from the same signer arrives.

### Finding Description
In `stackerdb_listener.rs`, the `BlockResponse::Accepted` branch only guards against double counting via `block.gathered_signatures.contains_key(&slot_id)`: [1](#0-0) 

The `BlockResponse::Rejected` branch guards via a separate map, `block.responded_signers.insert(slot_id)`: [2](#0-1) 

Because these are two disjoint bookkeeping structures with no cross-invalidation, a signer that first rejects (incrementing `total_weight_rejected` and inserting into `responded_signers`) and later re-evaluates and accepts the same block (which the signer-side `BlockInfo::check_state` transition table explicitly permits, `LocallyRejected -> LocallyAccepted`) causes `total_weight_approved` to be incremented too, while `total_weight_rejected` is left untouched. The signer's weight now counts toward *both* totals simultaneously — the two derived aggregates are no longer synchronized with the true, current set of votes, exactly analogous to the reported class where a derived running total (`reserve.totalUsage`) drifts from the state that should drive it (`usageIndex`/`totalSupply`) because updates on one path are not mirrored to the other.

This is reachable by a single non-malicious signer under gossip conditions alone (a re-proposal or new information causing `should_reevaluate_block`/re-evaluation to flip a prior rejection to acceptance), requiring no majority and no key compromise — it only requires the node to have already recorded a rejection from that signer for a hash it later accepts.

### Impact Explanation
`total_weight_rejected` feeds directly into the rejection-threshold check that wakes any thread waiting on the block's outcome: [3](#0-2) 

Because the stale rejection weight is never cleared, the tally can cross the "impossible-to-approve" threshold (`total_weight_rejected + weight_threshold > total_weight`) even while the true, current opinion of the signer set (as reflected by `gathered_signatures`/`total_weight_approved`) is trending toward acceptance. The coordinator/miner can therefore prematurely conclude the block has failed and move to build a competing block or tenure-extend while the actual signature-gathering path is still live — a state-machine equality break between "recorded rejection tally" and "true current signer opinion." This most closely maps to a liveness/miscount concern (a stale, un-synchronized aggregate driving a consensus-adjacent decision), rather than a signature/authenticity break; it does not let an invalid/non-canonical block get signed, nor does it recount a rejection as an acceptance in the strict sense (the accept message is real and independently signature-verified). Given the strict categories in scope (Critical: invalid/non-canonical/conflicting signing, rejection recounted as acceptance, cross-context signature; High: signer wedged from signing valid blocks / acting on stale threshold), this bug is best characterized as contributing to a **stale-threshold decision** on the node side, but I was not able to fully trace how `cvar.notify_all()`'s wakeup is consumed downstream (e.g., whether it actually aborts mining of the still-viable block or is merely advisory) within the available indexed context, so I cannot conclusively confirm this rises to the "High" bar (signer wedged into never signing valid blocks) versus a lower-severity operational nuance.

### Likelihood Explanation
Vote flips from reject to accept are an explicit, documented, non-adversarial part of the signer protocol (`should_reevaluate_block`, `should_reevaluate_reject_reason`, and the `LocallyRejected -> LocallyAccepted` transition are all first-class, expected behaviors), so the preconditions are common rather than contrived. However, whether the resulting stale tally actually changes node/miner behavior in a materially harmful way could not be fully confirmed from the visible code because the consumer of the `cvar` wakeup for the rejection path was not reached in the explored context.

### Recommendation
Track vote weight per-signer as a single current vote (e.g., a map from `slot_id` to `Accepted`/`Rejected`) and recompute `total_weight_approved`/`total_weight_rejected` from that authoritative per-signer state whenever a new response arrives, rather than maintaining two independently-incremented running counters. This mirrors the external report's recommendation to synchronize the derived total with the canonical source of truth (per-signer current vote) rather than incrementally patching two totals that can diverge.

### Proof of Concept
1. Node proposes block `B`; signer `S` (weight `w`) sends `BlockResponse::Rejected` for `B`. Coordinator sets `responded_signers.insert(S)`, `total_weight_rejected += w`.
2. `S` re-evaluates (e.g., new information makes the reject reason re-evaluable per `should_reevaluate_reject_reason`) and sends `BlockResponse::Accepted` for the same `B`.
3. Coordinator checks `gathered_signatures.contains_key(S)` → false → `total_weight_approved += w`; `responded_signers.insert(S)` was already true, so the reject branch is never re-entered to decrement anything (and there's no decrement code even if it were).
4. Now both `total_weight_approved` and `total_weight_rejected` include `w`, even though `S`'s current, valid vote is only `Accepted`. If enough other signers are in a similar transient rejected-then-accepted state, `total_weight_rejected + weight_threshold > total_weight` can fire and trigger `cvar.notify_all()` for "enough rejections," while acceptance signatures needed for the real threshold are still accumulating validly in `gathered_signatures`.

*Note: due to indexing limits, the full downstream consumer of the rejection-path `cvar` wakeup (what the miner does after being notified) was not visible in the retrieved context; a Devin session with full repo access would be needed to confirm the exact operational consequence.*

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
