## Analysis

The report's bug class — a party's classification/weight silently persisting after its state changes because no invalidation check exists — has a direct analog in the Nakamoto miner's StackerDB-driven signature tally in `stacks-node/src/nakamoto_node/stackerdb_listener.rs`.

`StackerDBListener` maintains, per proposed block, `total_weight_approved` and `total_weight_rejected`, tallied from `BlockAccepted`/`BlockResponse::Rejected` messages that individual signers broadcast over StackerDB. When a `Rejected` message arrives, the handler inserts the signer's `slot_id` into `block.responded_signers` and — only if that insert is new — adds the signer's weight to `total_weight_rejected`: [1](#0-0) 

When an `Accepted` message later arrives from the *same* signer for the *same* block (a legitimate, protocol-permitted vote flip — the signer-flows doc describes exactly this: a signer may reject a sibling block asynchronously and later sign a replacement once conditions change), the code only checks a *different* map, `gathered_signatures`, before adding to `total_weight_approved`: [2](#0-1) 

Nothing subtracts the signer's earlier contribution from `total_weight_rejected`, nor does the accept path check `responded_signers`/prior-reject state at all. So a single signer's stale rejection weight is permanently retained in `total_weight_rejected` even after that signer has since accepted (signed) the block. This breaks the intended one-vote-per-signer invariant behind the aggregated-weight tally: `total_weight_approved + total_weight_rejected` can exceed `total_weight`, and the "globally rejected" liveness-kill check, [3](#0-2) 

can fire (`total_weight_rejected + weight_threshold > total_weight`) purely from accumulated stale rejections that no longer reflect the signers' current votes, even while the same block is independently crossing the 70% acceptance threshold. This is a miner-local liveness wedge: a valid, canonical block that legitimately gathers enough live signatures can still get discarded by the miner as "dead" because of double-counted, unretracted historical rejection weight from signers who already flipped to accept.

This is a genuine finding worth writing up, so here is the strict-format output:

### Title
Stale rejection weight is never retracted when a signer later accepts the same block, corrupting the aggregated-weight tally - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener` tallies `total_weight_approved` and `total_weight_rejected` per proposed block from signer `BlockResponse` messages. The reject path dedups against `responded_signers`, and the accept path dedups against the separate `gathered_signatures` map, but neither path reconciles against the other. A signer who first rejects and later accepts the same block (a normal, protocol-sanctioned sequence, e.g., after re-validating an async sibling per `docs/signer-flows.md`) ends up counted in *both* totals, with the stale rejection weight never removed.

### Finding Description
- On `Rejected`, weight is added to `total_weight_rejected` guarded only by `block.responded_signers.insert(slot_id)` returning `true` (first time seen). [1](#0-0) 
- On `Accepted`, weight is added to `total_weight_approved` guarded only by `!block.gathered_signatures.contains_key(&slot_id)`, and `responded_signers.insert(slot_id)` is called again (a no-op if already true from a prior reject) — but `total_weight_rejected` is never decremented. [2](#0-1) 
- The dead-block liveness check compares only `total_weight_rejected` against the threshold, oblivious to the fact that some of that weight belongs to signers who have since accepted. [3](#0-2) 

This breaks the equality the coordinator relies on: aggregated tallied weight should reflect each signer's *current* verified vote, not a union of every vote it has ever cast for that block hash.

### Impact Explanation
This is a liveness wedge on the miner side. A block that ultimately obtains ≥70% live acceptance weight can still be flagged "globally rejected" (`GR` in the signer-flows diagram) by the coordinator because of unretracted, stale rejection weight from signers who already flipped to accept. This can stall tenure/block production requiring miner retries, and in the worst case can starve otherwise-canonical, sufficiently-signed blocks from ever being pushed by this miner instance, matching the "signer wedged" / liveness-consequence bar for High severity impact (here manifesting as the coordinator wedging valid blocks rather than the signer itself, but rooted in the same signer-message accounting logic in scope).

### Likelihood Explanation
No majority collusion is required — a single signer legitimately switching its vote (reject → accept) for one block is enough to poison the tally, and vote flips are an expected part of the documented signer state machine (asynchronous sibling validation, capitulation). The miner processes every `BlockResponse` it observes over StackerDB from registered signers, so this path is reachable in normal operation, not just adversarially.

### Recommendation
Track each signer's *current* vote for a given block (e.g., a single map from `slot_id` to latest vote/weight) instead of two independently-accumulated running totals. When a new vote from a signer supersedes a prior one, subtract the old weight from its previous bucket before adding the new weight to the new bucket, so `total_weight_approved` and `total_weight_rejected` always reflect the live vote of each responded signer, not the union of all votes it has ever cast.

### Proof of Concept
1. Miner proposes block `B`; signer `S` (weight `w`) initially rejects `B` (e.g., due to an async-validation timing gap) → `total_weight_rejected += w`, `responded_signers = {S}`.
2. `S` later determines `B`'s sibling is not canonical and signs `B` per the documented capitulation/replacement flow → `Accepted` message arrives; `gathered_signatures` does not yet contain `S`, so `total_weight_approved += w`; `total_weight_rejected` is left unchanged at its earlier value.
3. Enough other signers also accept such that `total_weight_approved` crosses `weight_threshold` while `total_weight_rejected` (inflated with `S`'s stale reject weight) independently crosses `total_weight - weight_threshold`, tripping the "globally rejected" `cvar.notify_all()` path in `stacks-node/src/nakamoto_node/stackerdb_listener.rs:567-574` for a block that has in fact reached sufficient live acceptance weight.

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
