### Title
`StackerDBListener` double-counts a signer's weight in both `total_weight_approved` and `total_weight_rejected` when a signer reconsiders a rejected block, stalling threshold accounting - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
Signers are allowed by design to reconsider a block they previously rejected and later send an `Accepted` response for it (per `stacks-signer/CHANGELOG.md`: "For some rejection reasons, a signer will reconsider a block proposal that it previously rejected"). The node-side `StackerDBListener`, which tallies `BlockResponse` gossip into `total_weight_approved`/`total_weight_rejected` to decide whether a block is signable or dead, deduplicates each vote type independently rather than deduplicating per-signer across both vote types.

### Finding Description
In `handle` (the message-processing loop), the `Accepted` branch only guards against re-adding weight using `block.gathered_signatures.contains_key(&slot_id)`: [1](#0-0) 

while the `Rejected` branch guards using the *shared* `responded_signers` set: [2](#0-1) 

`responded_signers` is written to by both branches (`block.responded_signers.insert(slot_id)` at line 465 for accept, line 515 for reject), but the *accept* branch never checks it before adding weight - it only checks `gathered_signatures`, a map that is populated solely by accept messages. This makes the outcome order-dependent for a single signer's slot on the same block:

- Reject → Accept: reject adds to `total_weight_rejected` and inserts into `responded_signers`. The later accept sees `gathered_signatures` does **not** contain the slot (still empty), so it unconditionally adds the signer's weight to `total_weight_approved` as well, and inserts a signature. The signer's weight is now counted on both sides of the tally, and the earlier rejection weight is never retracted.
- Accept → Reject: the reverse ordering is correctly guarded, since the reject branch's `responded_signers.insert` returns `false` and the second weight add is skipped.

This breaks the intended invariant that a signer contributes to at most one side of the aggregated-weight tally per block - the exact "aggregated-weight vs. verified-accepts" equality this system is supposed to preserve, and it is the direct structural analog of the Malt `LinearDistributor` bug: an internal counter (`declaredBalance`/here `total_weight_rejected`) is updated once and never reconciled against a later, legitimate state change (vested amount/here a reconsidered vote), so downstream logic (`_forfeit`/here the rejection-threshold check) keeps operating on stale accounting.

### Impact Explanation
`total_weight_rejected` is used to decide when the miner coordinator gives up on a block as globally rejected: [3](#0-2) 

Because a reconsidering signer's earlier rejection weight is never removed from `total_weight_rejected` after they subsequently accept, the coordinator's rejection tally can remain permanently inflated by weight that no longer reflects that signer's live vote. This is a liveness-affecting miscount of votes ("a rejection recounted"/retained inconsistently against a later acceptance) that can cause the coordinator to treat a block as dead (past the reject threshold) even though enough weight has, in reality, moved to acceptance - wedging block signing on stale threshold accounting, achievable by ordinary reconsideration behavior of a single signer's own StackerDB slot (no majority, no other signer's key, no auth_token needed).

### Likelihood Explanation
This requires only a single signer (one StackerDB slot) sending a `Rejected` response followed later by an `Accepted` response for the same `signer_signature_hash` - a scenario explicitly supported by the signer's own "reconsider a previously rejected block" feature. No majority coordination, cryptographic forgery, or privileged access is needed; it can be triggered by normal signer behavior or a single misbehaving/buggy signer's gossip ordering.

### Recommendation
Deduplicate weight accounting per signer/slot across both vote types instead of using independent guards (`gathered_signatures` vs `responded_signers`). Track, per slot, which side (approve/reject) the signer's weight was last credited to, and when a signer switches sides, decrement the previous tally before incrementing the new one - mirroring the fix already described in the CHANGELOG entry "Do not count both a block acceptance and a block rejection for the same signer/block," and extend that guarantee to cover the accept-after-reject (reconsideration) ordering, not just simple duplicate messages of the same type.

### Proof of Concept
1. Miner proposes block `B`; `StackerDBListener` creates a `BlockStatus` entry for `B`'s `signer_signature_hash`.
2. Signer `S` (slot `k`, weight `w`) sends `BlockResponse::Rejected` for `B`. `responded_signers.insert(k)` succeeds → `total_weight_rejected += w`.
3. Signer `S` later reconsiders and sends `BlockResponse::Accepted` for the same `B` (a supported signer behavior per the reconsideration changelog entry). In the accept handler, `gathered_signatures.contains_key(&k)` is `false` (only reject path had run), so `total_weight_approved += w` is applied unconditionally.
4. Result: `S`'s weight `w` is now counted in both `total_weight_approved` and `total_weight_rejected` for block `B`, and `total_weight_rejected` is never decremented despite `S` no longer rejecting - corrupting the coordinator's threshold bookkeeping for this block.

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
