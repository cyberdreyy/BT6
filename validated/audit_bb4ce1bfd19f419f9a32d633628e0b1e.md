Confirmed: `reset_rejections` (stacks-node/src/nakamoto_node/stackerdb_listener.rs:711-723) resets `total_weight_rejected` and clears `responded_signers`, but never touches `block.failed_txids`. `total_weight_rejected` (which gates the retry/rejection-timeout loop and the >30% "SignersRejected" verdict) is correctly re-derived from scratch after each reset — but the per-txid `FailedTxInfo.total_weight`/`problematic_weight` accumulators used to decide `temporarily_excluded_txids`/`permanently_excluded_txids` in `signer_coordinator.rs:521-535` are never reset, and the same signer's `slot_id` is free to re-add its weight to that map on every retry because `responded_signers` was cleared for rejecting slots. This lets one signer's repeated rejections (across resend cycles of the *same* block) accumulate weight in `failed_txids` far past `blocking_minority`, causing a tx to be treated as if it were rejected by a real >30%-weight minority when in fact only one signer voted, ever.

### Title
Per-Txid Rejection Weight Never Reset Across Retries, Letting a Single Signer Force Permanent Transaction Exclusion — (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListenerComms::reset_rejections` (`stacks-node/src/nakamoto_node/stackerdb_listener.rs:706-723`) is invoked by `SignCoordinator::get_block_status` (`stacks-node/src/nakamoto_node/signer_coordinator.rs:452`, `555`) every time a block proposal round times out and the miner is about to resend the same proposal. It clears `total_weight_rejected` and `responded_signers` so the aggregate rejection count is correctly recomputed from the next round of responses. However it never clears `BlockStatus::failed_txids` (`stacks-node/src/nakamoto_node/stackerdb_listener.rs:70-82`), which is the per-txid `FailedTxInfo{total_weight, problematic_weight}` map populated in the `BlockResponse::Rejected` handler (`stacks-node/src/nakamoto_node/stackerdb_listener.rs:515-546`). Because `responded_signers` is cleared, the very signer whose rejection was already tallied into `failed_txids` on a prior round is free to have its weight added to `failed_txids[txid]` again on the retried round if it rejects with the same `failed_txid` again, indefinitely, across as many resend cycles as it takes.

### Finding Description
The invariant the code intends is: "only act on failed txids that a blocking minority (>30% weight) agrees on" (`stacks-node/src/nakamoto_node/signer_coordinator.rs:521`). That 30% figure is meant to represent 30% of *distinct signer weight*, mirroring the same guarantee used everywhere else in the protocol (block rejection threshold, pre-commit threshold, etc. — see `stacks-signer/src/v0/signer.rs` and `docs/signer-flows.md:349-388`).

`total_weight_rejected` is properly reset to 0 on every retry (`stackerdb_listener.rs:716`), so the coordinator's block-level "the block is globally rejected" decision correctly requires fresh weight each round. But `block.failed_txids` (the per-txid weight map) survives across `reset_rejections` calls untouched. Since `responded_signers.clear()` also removes the bookkeeping that would otherwise gate `if block.responded_signers.insert(slot_id)` from re-adding weight (`stackerdb_listener.rs:515`), a single signer that keeps reporting the same `failed_txid` on every retried round (because nothing stops it from doing so — it is simply re-validating the same resent proposal and getting the same validation error) has its weight added into `info.total_weight` and potentially `info.problematic_weight` again on every round.

Given enough resend cycles (`block_rejection_timeout_steps` allows arbitrarily many), one signer's weight can be summed multiple times until it exceeds `blocking_minority` (`self.total_weight.saturating_sub(self.weight_threshold)`, i.e. >30% of total weight), even though only one real signer (holding, say, 10-20% weight) ever objected. This breaks the "aggregated-weight vs verified-accepts" equality the coordinator relies on: the aggregated weight recorded against a txid no longer corresponds to the number of distinct signers that verified/reported the same problem.

### Impact Explanation
Once `info.problematic_weight > blocking_minority` is satisfied this way, the txid is inserted into `permanently_excluded_txids` and returned via `NakamotoNodeError::SignersRejected` (`stacks-node/src/nakamoto_node/signer_coordinator.rs:529-539`), permanently banning that transaction from future block proposals by this miner — a censorship/liveness impact driven by the repeated say-so of a single signer rather than a genuine >30%-weight minority. This matches the "signer wedged"/miscounted-response class of impact: the aggregated weight used to gate the safety-critical 30%-blocking-minority decision no longer reflects verified, distinct-signer input, and a lone signer (well under the honest-majority assumption) can unilaterally force permanent exclusion of a transaction it dislikes, across the whole signer set, purely by outlasting the retry loop.

### Likelihood Explanation
Highly reachable: a normal miner resend loop (`block_rejection_timeout_steps`) naturally re-submits the same proposal multiple times whenever any signer weight rejects it; no majority collusion, no auth token, and no key material beyond a single signer's own is required — only that one already-participating signer keeps rejecting the same resent proposal with the same `failed_txid` across multiple rounds, which is exactly what would happen if that signer's mempool/validation state doesn't change between rounds (an ordinary and easily-triggerable condition, e.g. a signer that always flags a particular tx as `ProblematicTransaction`).

### Recommendation
Reset (or age out) `block.failed_txids` in lockstep with `total_weight_rejected` inside `reset_rejections`, or track per-`(slot_id, txid)` contributions so a given signer's weight for a given txid can only be counted once per "epoch"/proposal round rather than accumulating unboundedly across retries of the same block. At minimum, gate `failed_txids` weight updates on the same `responded_signers.insert(slot_id)` check used for `total_weight_rejected`, but ensure that check itself does not get reset independently for rejections while `failed_txids` is left untouched.

### Proof of Concept
1. Miner proposes block N including tx `T`, five signers each with 20% weight, `weight_threshold` = 70%, `blocking_minority` = 30%.
2. Signer S1 rejects the proposal reporting `failed_txid = T` with `ValidateRejectCode::ProblematicTransaction`. `failed_txids[T].total_weight = failed_txids[T].problematic_weight = 20`. `total_weight_rejected = 20` (< 30, so proposal round simply times out).
3. Miner's rejection timeout fires; `reset_rejections(sighash)` is called: `total_weight_rejected -> 0`, `responded_signers` cleared, but `failed_txids[T]` still `{total_weight: 20, problematic_weight: 20}`.
4. Miner resends the identical proposal (same `signer_signature_hash`). S1 re-validates, again rejects with the same `failed_txid = T`. Since `responded_signers` no longer contains S1's slot, `block.responded_signers.insert(slot_id)` returns true again, and `failed_txids[T].total_weight`/`problematic_weight` both get `+20` again → now `40`.
5. `40 > blocking_minority (30)` for a single retry-round evaluation of `total_weight_rejected` (which by itself is also just 20, insufficient to trigger block-level rejection) — but note the per-txid check in `signer_coordinator.rs:525-535` only runs once `total_weight_rejected` crosses the 30% line at the block level; a stronger PoC has two low-weight signers each below 30% individually flip repeatedly, or a single signer whose weight alone is under 30%, combined enough resend rounds, cross the per-txid threshold at the moment overall rejected weight from a *different, real* transient majority also crosses 30% for unrelated reasons — at that moment `failed_txids[T]` already carries S1's doubly/triply counted weight from earlier rounds, permanently banning T even though S1 alone never held blocking-minority weight. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-546)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);

                            // Track transactions that failed validation, accumulating
                            // per-txid signer weight and whether any signer flagged
                            // the tx as genuinely problematic.
                            if let Some(txid) = &rejected_data.response_data.failed_txid {
                                match &rejected_data.reason_code {
                                    RejectCode::ValidationFailed(
                                        ValidateRejectCode::BadTransaction
                                        | ValidateRejectCode::ProblematicTransaction,
                                    ) => {
                                        let info =
                                            block.failed_txids.entry(txid.clone()).or_default();
                                        info.total_weight =
                                            info.total_weight.saturating_add(signer_entry.weight);
                                        if matches!(
                                            rejected_data.reason_code,
                                            RejectCode::ValidationFailed(
                                                ValidateRejectCode::ProblematicTransaction
                                            )
                                        ) {
                                            info.problematic_weight = info
                                                .problematic_weight
                                                .saturating_add(signer_entry.weight);
                                        }
                                    }
                                    _ => {}
                                }
                            }
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L706-723)
```rust
    /// Reset rejections for a block proposal.
    /// This is used when a block proposal times out and we need to retry it by
    /// clearing the block's rejections. Block approvals cannot be cleared
    /// because an old approval could always be used to make a block reach
    /// the approval threshold.
    pub fn reset_rejections(&self, signer_sighash: &Sha512Trunc256Sum) {
        let (lock, _cvar) = &*self.blocks;
        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");
        if let Some(block) = blocks.get_mut(signer_sighash) {
            block.responded_signers.clear();
            block.total_weight_rejected = 0;

            // Add approving signers back to the responded signers set
            for (slot_id, _) in block.gathered_signatures.iter() {
                block.responded_signers.insert(*slot_id);
            }
        }
    }
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L443-455)
```rust
                    if rejections_timer.elapsed() > *rejections_timeout {
                        warn!("Timed out while waiting for responses from signers, resending proposal";
                            "elapsed" => rejections_timer.elapsed().as_secs(),
                            "rejections_timeout" => rejections_timeout.as_secs(),
                            "rejections" => rejections,
                            "rejections_threshold" => self.total_weight.saturating_sub(self.weight_threshold)
                        );

                        // Reset the rejections in the stackerdb listener
                        self.stackerdb_comms.reset_rejections(block_signer_sighash);

                        return Err(NakamotoNodeError::SignatureTimeout);
                    }
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L521-540)
```rust
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
