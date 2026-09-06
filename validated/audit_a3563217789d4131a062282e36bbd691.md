### Title
Signer weight double-counted in both `total_weight_approved` and `total_weight_rejected` for the same block after a reject-then-accept sequence - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener::run` tracks per-block signer aggregation via two independent gates: the reject path gates on `block.responded_signers.insert(slot_id)`, while the accept path gates on `block.gathered_signatures.contains_key(&slot_id)`. Because these are two different sets, a slot that has already contributed weight to `total_weight_rejected` (via `responded_signers`) can subsequently contribute its full weight again to `total_weight_approved` (via the disjoint `gathered_signatures` check), so the same signer's weight is counted in both ledgers simultaneously for the same `signer_signature_hash`.

### Finding Description
The intended invariant is that a signer's weight should be attributed to at most one "current stance" for a given block: either accepted or rejected, not both. The code instead maintains two independently-gated tallies:

- Reject branch: `if block.responded_signers.insert(slot_id) { block.total_weight_rejected += signer_entry.weight; ... }` [1](#0-0) 
- Accept branch: `if !block.gathered_signatures.contains_key(&slot_id) { block.total_weight_approved += signer_entry.weight; ... } block.gathered_signatures.insert(slot_id, signature); block.responded_signers.insert(slot_id);` [2](#0-1) 

Trace for the claimed sequence (same slot_id, same `signer_signature_hash` H, block H present in `self.blocks`):
1. `Rejected` message arrives for slot_id first: `responded_signers` does not contain slot_id yet, so `insert` returns `true`, and `total_weight_rejected += weight`. [1](#0-0) 
2. `Accepted` message arrives later for the same slot_id and same H: the gate checked is `gathered_signatures.contains_key(&slot_id)`, which is empty for this block (no accept was ever recorded for this slot before), so the condition is true and `total_weight_approved += weight` is executed unconditionally of `responded_signers`/`total_weight_rejected` state. [3](#0-2) 

At no point does the accept branch check `responded_signers` (which already includes this slot_id from step 1) nor does it decrement `total_weight_rejected`. The result: for block H, `total_weight_approved` and `total_weight_rejected` both include this one signer's weight, which breaks the equality "aggregated accept weight == weight of signers whose *current* stance is accept for H."

The comment on `reset_rejections` explicitly acknowledges the asymmetry by design ("Block approvals cannot be cleared because an old approval could always be used to make a block reach the approval threshold") [4](#0-3) , but that mechanism only fires on explicit proposal retry/timeout (`reset_rejections`) and clears `total_weight_rejected` to 0 while re-adding approvers into `responded_signers`; it does not address the ordinary reject→accept sequence happening without a `reset_rejections` call, where both weights accumulate concurrently in the live `BlockStatus`.

No signature-domain, auth-token, or majority-signer requirement is needed here: both messages (`BlockRejection` and `BlockAccepted`) as described are genuine messages from one honest signer's slot, and the attacker (holding a single miner/signer slot) only needs to relay/gossip them through the StackerDB channel that any observer of that signer's chunk can already read and, since StackerDB gossip has no anti-replay/ordering enforcement, an already-published or intercepted genuine Accepted/Rejected chunk pair for the same block can be delivered to the listener in reject-then-accept order.

### Impact Explanation
This breaks the accounting invariant behind block finalization: `total_weight_approved >= self.weight_threshold` triggers `cvar.notify_all()` to signal the miner/coordinator that the block has enough votes to broadcast [5](#0-4) , while `total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight` triggers the reject-side notification that the block can never accumulate enough approvals [6](#0-5) . With one signer's weight double-booked, both conditions can become true simultaneously off the same underlying weight budget, meaning the aggregation state can simultaneously indicate "enough to approve" and "enough to guarantee rejection" for the same block — inconsistent bookkeeping that inflates the approve tally beyond what is backed by distinct, currently-accepting signers. This maps to a "rejection recounted as acceptance"-style accounting defect (Critical category per the prompt's taxonomy), since it can push `total_weight_approved` past `weight_threshold` using weight that should have been excluded/still counted against rejection.

### Likelihood Explanation
Preconditions are modest: a normal reward cycle, an outstanding proposed block H tracked in `self.blocks`, and one honest signer's genuine Rejected message for H followed at some point by that same signer's genuine Accepted message for H (or two chunks captured/replayed by an attacker holding one slot with StackerDB gossip access). The attacker does not need any signer's private key or the validation `auth_token` — they only relay/gossip chunk data. This is realistic-but-narrow: it depends on an honest signer actually changing its stance from reject to accept for the identical `signer_signature_hash` and that transition being observed by the listener as two separate messages, or on an attacker replaying two already-existing valid chunks for that slot in the right order to the listener's chunk receiver. The repo context does not show the wiki/mermaid-adjacent transport-layer replay protections; I was not able to fully verify within this session whether StackerDB chunk versioning/dedup logic elsewhere in the node (outside `stackerdb_listener.rs`) prevents delivering a stale (already-superseded) chunk to trigger this exact re-processing — that would require checking the StackerDB chunk push/pull versioning and event-dispatch dedup paths, which were out of scope/not fully traced here.

### Recommendation
Gate the accept branch's weight increment on the same `responded_signers` set used by the reject branch (or unify them into one "current stance" map keyed by slot_id with an enum {Accepted, Rejected}), and when a slot transitions from rejected to accepted, subtract its weight from `total_weight_rejected` before adding it to `total_weight_approved` (and vice versa), so a slot's weight is attributed to exactly one tally at a time.

### Proof of Concept
Rust test plan against `StackerDBListener` (using its public/`pub(crate)` test surface):
1. Construct a `StackerDBListener` with a reward set containing at least one signer of nonzero weight; call `StackerDBListenerComms::insert_block` for block H to seed `self.blocks` with a zeroed `BlockStatus`.
2. Feed a `StackerDBChunksEvent`/message tuple `(slot_id, pubkey, SignerMessageV0::BlockResponse(BlockResponse::Rejected(BlockRejection{signer_signature_hash: H, ...})))` through the same code path exercised by `run()` (or directly invoke the match arm logic if exposed) and assert `blocks[H].total_weight_rejected == signer_weight` and `blocks[H].responded_signers.contains(&slot_id)`.
3. Feed `(slot_id, pubkey, SignerMessageV0::BlockResponse(BlockResponse::Accepted(BlockAccepted{signer_signature_hash: H, signature: <valid sig over H>, ...})))` for the same slot_id.
4. Assert `blocks[H].total_weight_approved == signer_weight` and `blocks[H].total_weight_rejected == signer_weight` simultaneously, i.e. `blocks[H].total_weight_approved + blocks[H].total_weight_rejected == 2 * signer_weight > signer_weight`, demonstrating the same signer's weight was counted twice for block H.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L706-722)
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
```
