### Title
Reject→Accept vote flip lets a single signer double-book its weight into both `total_weight_approved` and `total_weight_rejected`, breaking the aggregated-weight-vs-verified-accepts equality — ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener`'s per-block vote tally (`BlockStatus.total_weight_approved` / `total_weight_rejected`) is meant to represent a 1-signer-1-vote weighted count over a single `signer_signature_hash`. The `Accepted` branch only guards against double-counting by checking `gathered_signatures`, while the `Rejected` branch guards by checking `responded_signers` — and `Accepted` unconditionally inserts into `responded_signers` too. This makes the "already voted" guard asymmetric: an `Accepted`-then-`Rejected` flip is blocked (sticky-accept), but a `Rejected`-then-`Accepted` flip is **not** blocked, so the same signer's weight is added to `total_weight_rejected` once and later also to `total_weight_approved`, without ever being removed from the rejected bucket.

### Finding Description
In the `Accepted` handler [1](#0-0) , weight is added only if `!block.gathered_signatures.contains_key(&slot_id)`, then both `gathered_signatures` and `responded_signers` are updated.

In the `Rejected` handler [2](#0-1) , weight is added only if `block.responded_signers.insert(slot_id)` returns `true` (i.e., first time this slot is seen at all — regardless of message kind).

Consequence:
- If signer `S` sends `Rejected` first: `responded_signers` now contains `S`'s slot, `total_weight_rejected += weight(S)`.
- If `S` then sends `Accepted` for the *same* `block_sighash`: `gathered_signatures` does not yet contain `S`'s slot (only `Accepted` messages populate it), so the `Accepted` branch's guard passes and `total_weight_approved += weight(S)` as well. `S`'s weight is now counted in both buckets, permanently, for the lifetime of that `BlockStatus` entry — there is no code path anywhere in this function that decrements a prior vote when a signer's later message contradicts it.
- The reverse order (`Accepted` then `Rejected`) is correctly blocked because the `Rejected` guard checks `responded_signers`, which was already populated by the earlier `Accepted`.

The cryptographic checks upstream are legitimate for both message kinds — the recovered/verified public key must match the slot's known signer key [3](#0-2)  for `Accepted`, and similarly for `Rejected` [4](#0-3)  — so this is not a spoofing bug; it is a single legitimate signer (using only its own stackerdb slot) that can trigger the double count deliberately.

This directly breaks the aggregated-weight-vs-verified-accepts equality that `SignerCoordinator::run` relies on to decide the fate of a block proposal: it reads `block_status.total_weight_rejected` and `block_status.total_weight_approved` as if they were disjoint partitions of signer weight over `self.total_weight` [5](#0-4) . With the bug, `total_weight_approved + total_weight_rejected` can exceed `total_weight`, i.e., the invariant "each signer's weight counted at most once across the two outcome buckets" is violated by a single signer's own crafted message sequence.

### Impact Explanation
This does not let the approval bucket cross 70% with less real accepting weight than required (each `Accepted` is only counted once per slot via the `gathered_signatures` guard), so it cannot make the coordinator accept a block that lacks genuine 70% signature weight — no Critical safety break (no invalid/non-canonical block gets signed as a direct result of this bug alone).

It does, however, let a single signer's weight remain stuck in `total_weight_rejected` forever even after that signer legitimately/deliberately later sends `Accepted` for the exact same block. Because the rejection-threshold check in `SignerCoordinator::run` re-evaluates `block_status.total_weight_rejected` on every loop iteration [6](#0-5) , this phantom stuck rejection weight combines with any other genuinely-opposing signers' weight. A signer or small coalition can pad the rejected tally with weight that should logically have been retracted, pushing the miner into treating a block as `SignersRejected` (rejecting the block and excluding/temporarily-banning its transactions) sooner than warranted by the real live opposition — a liveness degradation on block production/tenure progress that does not require a signer majority, only the attacker's own slot and their own real weight. This matches the "liveness wedge" impact class (miner/coordinator wedged into discarding otherwise-signable blocks), analogous to the report's push-strategy DOS in spirit: an asymmetric bookkeeping guard lets one participant's own message sequence corrupt a shared aggregate that other honest participants depend on.

### Likelihood Explanation
Reachable by any single reward-cycle signer using only its own StackerDB write slot — no cooperation, no majority weight, and no protocol-version gymnastics are required. The attacker simply needs to author two `SignerMessageV0::BlockResponse` messages for the same `signer_signature_hash`: first `Rejected(...)` (any `RejectCode`), then `Accepted(...)` with a real valid signature over that hash — both trivially producible with the signer's own private key. Honest, well-behaved signers are unlikely to naturally hit this order in practice (the docs describe rejection as "sticky" at the local-signer decision level, i.e. `Signer` code itself won't emit Reject-then-Accept for the same proposal), but a malicious/byzantine signer is not bound by that local state machine and can write arbitrary StackerDB chunks directly.

### Recommendation
Make the "already responded" bookkeeping symmetric and idempotent per (slot_id, kind), and support vote-updates correctly: track the signer's *current* recorded outcome (accept vs reject) per slot for a given block, and when a new, differently-signed message arrives from the same slot, subtract the previously counted weight from whichever bucket it was in before adding it to the new bucket — rather than only guarding one direction. Concretely, replace the two independent guards (`gathered_signatures` for Accept, `responded_signers` for Reject) with a single per-slot "current vote" map that is consulted (and mutated) by both branches, ensuring `total_weight_approved + total_weight_rejected` always reflects the sum of *unique, current* signer votes and never double- or stale-counts a slot's weight.

### Proof of Concept
1. Miner proposes block `B`; `SignerCoordinator` opens a `BlockStatus` entry for `block_sighash = H(B)` with `total_weight_approved = total_weight_rejected = 0`.
2. Malicious signer `S` (weight `w_S`, holding valid private key for slot `k`) writes a `BlockResponse::Rejected` chunk for hash `H(B)` to its StackerDB slot.
   - `StackerDBListener` processes it: `responded_signers.insert(k)` succeeds → `total_weight_rejected += w_S` [2](#0-1) .
3. `S` then overwrites its slot with a `BlockResponse::Accepted` chunk for the *same* hash `H(B)`, with a valid signature.
   - `StackerDBListener` processes it: signature verifies [3](#0-2) ; `gathered_signatures.contains_key(k)` is `false` (never populated by the earlier Reject), so `total_weight_approved += w_S` as well [1](#0-0) .
4. Now `total_weight_rejected` and `total_weight_approved` both include `w_S`, so `total_weight_approved + total_weight_rejected > total_weight - w_S_others_not_yet_voted` — i.e., the two buckets are no longer a disjoint partition of `self.total_weight`. If other genuinely-rejecting signers' weight, combined with this phantom `w_S` stuck in the rejected bucket, crosses `self.total_weight - self.weight_threshold`, `SignerCoordinator::run` returns `NakamotoNodeError::SignersRejected` for block `B` even though the *current* honest opposition (excluding `S`, who ultimately accepted) may be below the 30% blocking minority [7](#0-6) .

Note: I was not able to fully trace whether `reset_rejections`/timeout logic (referenced at `signer_coordinator.rs:555`) or any other component elsewhere resets `responded_signers`/`gathered_signatures` per-signer on a timer; my search for those symbols in `stackerdb_listener.rs` did not return matches in the final iteration due to tool limitations, so I could not confirm whether a timeout-based reset mitigates repeated exploitation across long-lived proposals. This should be verified in a live session before finalizing severity.

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L411-426)
```rust
                        let Ok(valid_sig) = signer_pubkey.verify(block_sighash.bits(), &signature)
                        else {
                            warn!(
                                "StackerDBListener: Got invalid signature from a signer. Ignoring."
                            );
                            continue;
                        };
                        if !valid_sig {
                            warn!(
                                "StackerDBListener: Processed signature but didn't validate over the expected block. Ignoring";
                                "signature" => %signature,
                                "signer_signature_hash" => %block_sighash,
                                "slot_id" => slot_id,
                            );
                            continue;
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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L501-513)
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
