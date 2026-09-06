### Title
Signer weight double-counted across `total_weight_approved` and `total_weight_rejected` when a signer changes its vote - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
The mining-node-side `StackerDBListener` tallies signer responses for a proposed block into two independent weight counters, `total_weight_approved` and `total_weight_rejected`, using two *different* de-duplication keys (`gathered_signatures` for accepts, `responded_signers` for the "already counted" gate). Because a signer is allowed by the signer state machine to change its verdict from a rejection to an acceptance (`LocallyRejected --> LocallyAccepted: re-evaluated`), the same signer's weight can end up added to both counters for the same block, breaking the invariant that the sum of the two weight pools should never exceed a signer's own weight once.

### Finding Description
`handle_signer_messages` in `stacks-node/src/nakamoto_node/stackerdb_listener.rs` processes two message kinds for the same tracked block:

- `BlockResponse::Accepted`: weight is added to `total_weight_approved` only if `!block.gathered_signatures.contains_key(&slot_id)` [1](#0-0) , and then unconditionally `block.gathered_signatures.insert(slot_id, signature); block.responded_signers.insert(slot_id);` [2](#0-1) .
- `BlockResponse::Rejected`: weight is added to `total_weight_rejected` only if `block.responded_signers.insert(slot_id)` returns `true` (i.e., first time this slot is seen at all) [3](#0-2) .

Because the two branches gate on different sets (`gathered_signatures` vs. `responded_signers`), a signer that first **rejects** and later **accepts** the same block causes:
1. On the reject: `responded_signers.insert(slot_id)` → `true` (first time) → weight added to `total_weight_rejected`.
2. On the later accept: `gathered_signatures.contains_key(slot_id)` is still `false` (this map was never touched by the reject branch) → weight is *also* added to `total_weight_approved`.

The same signer's weight is now counted in both pools simultaneously, and neither pool is ever decremented when a vote flips. This breaks the equality that should hold between "verified distinct responses" and the "aggregated weight" used to drive the miner's decision logic in `SignerCoordinator::get_block_status` (`stacks-node/src/nakamoto_node/signer_coordinator.rs`), which polls exactly these two totals to decide whether to treat a block as accepted (`total_weight_approved >= self.weight_threshold`, line 541 in the earlier excerpt) or globally rejected (`total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight`) [4](#0-3) .

This is the structural analog of the bond-escalation bug: the report's bug is an asymmetric conditional that fails to reconcile two counters (pledges-for/pledges-against) that should be tracked and settled together; here the asymmetric dedup key (`gathered_signatures` vs. `responded_signers`) similarly fails to reconcile the approve/reject weight pools when a single actor's status moves from one pool to the other, letting that actor's weight linger in the stale pool while also being freshly counted in the new one.

### Impact Explanation
This is a liveness wedge for the mining node's signing round rather than a chain-safety break: the inflated, never-decremented `total_weight_rejected` can push the sum `total_weight_rejected + weight_threshold` past `total_weight` purely because of stale rejection weight from a signer who has since re-evaluated and accepted, causing the coordinator to abort the round with `NakamotoNodeError::SignersRejected` even though a legitimate 70%-weight acceptance is otherwise reachable. This matches the "signer wedged... acting on a stale... threshold" class of impact — the node can be starved from ever completing a signing round for an otherwise valid block, forcing repeated re-proposals/timeouts. It does not, by itself, let an invalid/non-canonical block get pushed, because the actual signature bundle handed to the chain (`block_status.gathered_signatures`) and the on-chain `verify_signer_signatures` check independently dedup by public key, so this does not defeat consensus-level verification.

### Likelihood Explanation
Reachable purely through normal signer behavior and the documented state machine (`LocallyRejected --> LocallyAccepted: re-evaluated`), which the signer flow docs explicitly describe as a supported transition (e.g., a signer rejects a proposal, then re-evaluates and signs a resubmission or after a conflict goes stale). No majority collusion or key compromise is required — a single signer's ordinary vote-flip triggers the double counting.

### Recommendation
- **Short term**: Gate weight accounting for both branches on the same de-duplication key (e.g., always check/insert into `responded_signers` before adding weight in either branch, and if a signer moves from rejected to accepted, subtract its weight from `total_weight_rejected` before adding it to `total_weight_approved`, or vice versa).
- **Long term**: Add unit/integration tests exercising a signer that first rejects then accepts (and the reverse) the same block, asserting `total_weight_approved + total_weight_rejected` never exceeds the signer's own weight contribution and never exceeds `self.total_weight`.

### Proof of Concept
1. A block `B` is proposed to `N` signers with total weight `W` and `weight_threshold = 0.7W`.
2. Signer `S` (weight `w`) initially rejects `B` (e.g., due to a stale chain view) — `total_weight_rejected` increases by `w` via `stackerdb_listener.rs:515-518`.
3. Enough other signers reject to bring `total_weight_rejected` close to, but not over, the blocking-minority threshold (`W - weight_threshold`).
4. Signer `S` re-evaluates (per the documented `LocallyRejected --> LocallyAccepted` transition) and sends a fresh `Accepted` message for the same block hash.
5. In `stackerdb_listener.rs:443-446`, since `S`'s slot is not yet in `gathered_signatures`, `total_weight_approved` increases by `w` as well — `S`'s weight is now counted in both `total_weight_rejected` and `total_weight_approved`.
6. If one more signer independently rejects afterward, `total_weight_rejected` (still inflated by `S`'s stale weight) can cross `total_weight - weight_threshold`, and `SignerCoordinator::get_block_status` returns `Err(NakamotoNodeError::SignersRejected{..})` (`signer_coordinator.rs:509-540`), aborting the round even though `S` and the majority actually support the block.

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-446)
```rust
                        if !block.gathered_signatures.contains_key(&slot_id) {
                            block.total_weight_approved = block
                                .total_weight_approved
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L464-465)
```rust
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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-522)
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
```
