### Title
A signer's stale rejection weight is never removed after it later accepts the same block, letting a rejection be double-counted alongside an acceptance - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListener` aggregates per-block signing weight in `total_weight_approved` and `total_weight_rejected` as `BlockResponse` messages arrive from signers over StackerDB. The de-duplication guards used for the two message kinds are asymmetric: the `Accepted` handler only checks `block.gathered_signatures.contains_key(&slot_id)` before adding weight, while the `Rejected` handler checks `block.responded_signers.insert(slot_id)`. Because the `Accepted` branch also inserts into `responded_signers` (but the `Rejected` branch never removes from `gathered_signatures` or decrements `total_weight_rejected`), a signer that first rejects and later accepts the same block has its weight added to *both* `total_weight_rejected` and `total_weight_approved`, permanently. This breaks the implicit invariant that a signer's weight should only ever count once, and only toward its current/latest verdict, mirroring the `LibACL` `sysAdminCount` class of bug: a counter that is incremented/decremented from multiple code paths without checking whether the actor is already reflected in the tracked state.

### Finding Description
In `stacks-node/src/nakamoto_node/stackerdb_listener.rs`:

- `Rejected` handling (lines 486-591): weight is added exactly once, guarded by `block.responded_signers.insert(slot_id)` returning `true` (i.e., first time this slot has responded at all): [1](#0-0) 

- `Accepted` handling (lines 386-470): weight is added guarded only by `!block.gathered_signatures.contains_key(&slot_id)`, and only *afterward* does the code also insert into `responded_signers`: [2](#0-1) 

Sequence that triggers the bug for a single signer/slot `S` with weight `w` on block `B`:
1. `S` sends `Rejected(B)`. `responded_signers.insert(S)` returns `true` (new), so `total_weight_rejected += w`.
2. `S` later sends `Accepted(B)` for the same block (e.g., after re-evaluating, a delayed/duplicated network message, or a deliberately crafted double message from a malicious signer). The guard checked is `!gathered_signatures.contains_key(S)`, which is still true because only the `Accepted` path ever populates `gathered_signatures`. So `total_weight_approved += w` as well. `responded_signers.insert(S)` is now a no-op since `S` is already present, but that does not undo step 1's damage — `total_weight_rejected` is never decremented.

Result: `S`'s weight `w` is now counted in **both** `total_weight_rejected` and `total_weight_approved` for the same block, for as long as this in-memory `BlockStatus` entry exists. This violates the intended invariant that `total_weight_approved + total_weight_rejected <= total_weight` (each signer contributing to at most one side), the direct analog of `sysAdminCount` in the report being left inconsistent with the real on-chain role state after a grant/revoke sequence performed by the same actor.

The reverse order (Accept then Reject) is protected, because by the time `Rejected` is processed, `responded_signers.insert(S)` already returns `false` (it was added by the earlier `Accepted`), so the reject weight is correctly suppressed. Only the Reject→Accept order is vulnerable.

### Impact Explanation
`SignerCoordinator::get_block_status` (`stacks-node/src/nakamoto_node/signer_coordinator.rs`) consumes these two counters directly to decide the fate of a proposed block: [3](#0-2) 

Because the rejection check (`total_weight_rejected + weight_threshold > total_weight`) is evaluated first, and it is fed by a value that can retain phantom/stale rejection weight from a signer who has since accepted, this can cause the coordinator to wrongly conclude a "blocking minority" has been reached (`SignersRejected`) even though the true current rejecting weight (excluding the flip-flopped signer) does not warrant it. This is a liveness wedge: a signer whose latest, valid verdict is "accept" can still contribute to permanently blocking that block from ever reaching consensus in that round, forcing the miner to retry/rebuild — matching the High-impact class "a signer wedged into never signing valid blocks" via a rejection that is never correctly retracted. It does not directly produce an invalid/non-canonical signed block since `total_weight_approved` is fed from actually-verified signatures (`verify_signer_signatures` in `stackslib/src/chainstate/nakamoto/mod.rs` independently re-derives weight from valid recovered signatures at chain-acceptance time), but it corrupts the *node-side accounting* used to decide whether to keep waiting, retry, or drop transactions/miners, i.e., a liveness/DoS-adjacent wedge rooted in a broken counter invariant, directly analogous to the reported bug class.

### Likelihood Explanation
This requires only a single signer (one StackerDB slot) sending two `BlockResponse` messages for the same block in the Reject→Accept order — no majority collusion, no other signer's key, and no local/auth-token access is needed; it is reachable purely via the public StackerDB gossip channel that `StackerDBListener` already consumes. A malicious signer can trigger it deliberately by crafting exactly this two-message sequence; it could also occur unintentionally if a signer legitimately changes its verdict (e.g., re-validates and switches after a chainstate re-check) and the two messages are processed by the listener in that order, which is plausible given asynchronous StackerDB delivery.

### Recommendation
Make the weight bookkeeping symmetric and state-machine-consistent:
1. Track a signer's current verdict per slot (e.g., an enum `Accepted`/`Rejected`) instead of two independently-guarded sets/maps.
2. When a new verdict for a slot arrives that differs from a previously recorded one, subtract the old verdict's weight from its tally before adding the weight to the new tally (or simply refuse to change a verdict once recorded, consistent with the `signed_self` immutability already used in `stacks-signer/src/v0/signer.rs`).
3. Ensure `total_weight_approved + total_weight_rejected <= total_weight` holds as an invariant after every update, and add an assertion/test enforcing this.

### Proof of Concept
1. Node proposes block `B` to reward-cycle signer set including signer slot `S` with weight `w`.
2. `StackerDBListener` receives `BlockResponse::Rejected(B)` from `S`: `responded_signers.insert(S)` → `true`; `total_weight_rejected += w` (`stackerdb_listener.rs:515-518`).
3. `StackerDBListener` receives `BlockResponse::Accepted(B)` from the same `S` (duplicate/delayed message, or `S` deliberately double-sends): `gathered_signatures.contains_key(S)` is `false` (never set by the reject path), so `total_weight_approved += w` (`stackerdb_listener.rs:443-446`); `gathered_signatures.insert(S, sig)`; `responded_signers.insert(S)` is now a no-op.
4. Final state: `total_weight_rejected` still includes `w` from step 2 (never reverted) and `total_weight_approved` includes `w` from step 3 — `S`'s weight is double counted across the two pools, corrupting `SignerCoordinator::get_block_status`'s accept/reject decision for `B`.

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
