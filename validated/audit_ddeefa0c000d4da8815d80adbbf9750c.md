### Title
Weight double-counted across accept/reject tallies when a signer flips its vote via `BlockAccepted` after `BlockRejected` - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener` tallies each signer's response toward two independent counters, `total_weight_approved` and `total_weight_rejected`, that the miner treats as mutually exclusive shares of `total_weight` (used to decide "70% approved" vs "blocking >30% rejected"). The two branches use inconsistent de-duplication keys: the `Accepted` branch gates its weight addition on `block.gathered_signatures.contains_key(&slot_id)`, while the `Rejected` branch gates on the *shared* `block.responded_signers` set. A signer that legitimately re-evaluates a block from `LocallyRejected` to `LocallyAccepted` (an explicitly supported state transition per the signer state machine) will have its weight counted into `total_weight_rejected` first, and then *also* counted into `total_weight_approved` later, because the accept-branch guard never checks `responded_signers`. This breaks the aggregated-weight-vs-verified-accepts equality the miner relies on to gate block acceptance/rejection.

### Finding Description
In `stackerdb_listener.rs`, `BlockAccepted` handling (lines 386-465):

```
if !block.gathered_signatures.contains_key(&slot_id) {
    block.total_weight_approved = block.total_weight_approved.saturating_add(signer_entry.weight);
    ...
}
block.gathered_signatures.insert(slot_id, signature);
block.responded_signers.insert(slot_id);
```

`BlockRejected` handling (lines 486-518):
```
if block.responded_signers.insert(slot_id) {
    block.total_weight_rejected = block.total_weight_rejected.saturating_add(signer_entry.weight);
    ...
}
``` [1](#0-0) [2](#0-1) 

The `Rejected` path uses `responded_signers.insert()` (a `HashSet<u32>`) as its sole de-dup gate for *both* whether to log and whether to add weight, and this same set is also written to by the `Accepted` path. The `Accepted` path's weight-add decision, however, is keyed on `gathered_signatures` (a `HashMap<slot_id, MessageSignature>`), not `responded_signers`.

Sequence that reaches the bug (single signer, no majority needed):
1. Signer S proposes/validates a block, and for whatever legitimate reason (a stale reorg check, a `RECHECK` failure per the documented pre-commit re-check flow) sends `BlockResponse::Rejected`. The listener increments `total_weight_rejected` by S's weight and inserts S's `slot_id` into `responded_signers`.
2. The state-machine explicitly allows `LocallyRejected -> LocallyAccepted: re-evaluated` [3](#0-2) , e.g. triggered by `should_reevaluate_reject_reason`/`should_reevaluate_block` on a fresh proposal replay, or by S eventually reaching the pre-commit/signature threshold on a later look at the same block hash. S subsequently signs and broadcasts `BlockResponse::Accepted` for the *same* `signer_signature_hash`.
3. The listener's `Accepted` handler checks `!block.gathered_signatures.contains_key(&slot_id)` — true, since S never signed before — so it adds S's weight to `total_weight_approved` as well. `responded_signers.insert(slot_id)` is a no-op since S is already present, but that set was never consulted by the accept branch anyway.

Result: S's weight now counts in *both* `total_weight_approved` and `total_weight_rejected` simultaneously, for the same block. The sum `total_weight_approved + total_weight_rejected` can exceed `total_weight`, so the two conditions the miner uses to decide the block's fate are no longer mutually exclusive.

### Impact Explanation
`signer_coordinator.rs::get_block_status` uses these two counters as if they partition `total_weight`:
```
if block_status.total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight {
    ... return Err(NakamotoNodeError::SignersRejected { ... })
} else if block_status.total_weight_approved >= self.weight_threshold {
    ... return Ok(signatures)
}
``` [4](#0-3) 

Because the reject branch is checked first, a stale/duplicated rejection weight (from a signer who has since accepted) can push `total_weight_rejected` over the blocking-minority threshold even though the *current*, non-stale rejecting weight is below that minority — i.e. the miner can be made to treat a block as `SignersRejected` (and abandon/retimeout it, discard associated tx exclusions) when the signer set has, in fact, legitimately reached the 70% approval threshold. This is a liveness wedge on block production: a single signer's benign vote flip (reject-then-accept on re-evaluation, which the signer-side state machine explicitly permits) can cause the miner to spuriously fail a block that should have been accepted, forcing retries/timeouts and wasting/misdirecting the transaction-exclusion logic (`temporarily_excluded_txids`/`permanently_excluded_txids`) that is gated on the inflated `total_weight_rejected`.

### Likelihood Explanation
This requires only one signer to exhibit the documented reject-then-accept re-evaluation behavior on the same block hash, which the codebase's own state diagram treats as a normal, expected transition (`LocallyRejected -> LocallyAccepted: re-evaluated`), not an adversarial action. It needs no majority, no key compromise, and no protocol-version mismatch — any signer that rejects, then reconsiders and signs (e.g. because a conflicting/reorg condition it originally saw resolved, or because it processes a re-sent proposal) triggers the double count via ordinary gossip traffic the miner already listens to.

### Recommendation
Make the `Accepted` and `Rejected` branches use one consistent, mutually-exclusive accounting scheme: gate the weight increment for both branches on the same "have we already counted this slot toward either total" check (e.g., a single set/map keyed by `slot_id` that records "counted as approved" or "counted as rejected"), and when a signer's response flips from rejected to accepted (or vice versa), subtract the previously-counted weight from the old bucket before adding it to the new one, so that at all times `total_weight_approved + total_weight_rejected <= total_weight` and each signer's weight is attributed to exactly one side.

### Proof of Concept
1. Reward set has signers A (weight w_A), B, C, ... with `total_weight` and `weight_threshold` computed via `compute_voting_weight_threshold`.
2. Miner proposes block `H`. Signer A rejects `H` (e.g. transient chainstate re-check failure). `StackerDBListener` records: `total_weight_rejected += w_A`, `responded_signers = {A}`.
3. A re-evaluates `H` (state machine permits `LocallyRejected -> LocallyAccepted`) and later broadcasts `BlockResponse::Accepted(H)` with a valid signature.
4. `StackerDBListener`'s accept handler checks `gathered_signatures.contains_key(A)` — false — so it does `total_weight_rejected` unchanged, `total_weight_approved += w_A`.
5. Now `total_weight_approved + total_weight_rejected = w_A + (sum of other real approvals/rejections)`, exceeding `total_weight` by `w_A`.
6. If enough additional signers legitimately reject afterward, `total_weight_rejected` (still inflated by stale `w_A`) crosses `total_weight - weight_threshold` sooner than it should, causing `get_block_status` to return `SignersRejected` for a block that a correct (non-inflated) tally would still show as pending or even accepted — a liveness fault triggerable by one signer's ordinary vote reconsideration.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L486-518)
```rust
                    SignerMessageV0::BlockResponse(BlockResponse::Rejected(rejected_data)) => {
                        let (lock, cvar) = &*self.blocks;
                        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");

                        let Some(block) = blocks.get_mut(&rejected_data.signer_signature_hash)
                        else {
                            info!(
                                "StackerDBListener: Received rejection for block that we did not request. Ignoring.";
                                "signer_signature_hash" => %rejected_data.signer_signature_hash,
                                "slot_id" => slot_id,
                                "signer_set" => self.signer_set,
                            );
                            continue;
                        };

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

**File:** docs/signer-flows.md (L140-149)
```markdown
    Unprocessed --> PreCommitted : mark_pre_committed
    PreCommitted --> LocallyAccepted : mark_locally_accepted = WE SIGN
    Unprocessed --> LocallyRejected : mark_locally_rejected
    PreCommitted --> LocallyRejected : mark_locally_rejected
    LocallyRejected --> LocallyAccepted : re-evaluated
    LocallyAccepted --> LocallyRejected : re-evaluated
    LocallyAccepted --> GloballyAccepted : mark_globally_accepted
    LocallyRejected --> GloballyRejected : mark_globally_rejected
    GloballyAccepted --> [*]
    GloballyRejected --> [*]
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
