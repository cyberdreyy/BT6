### Title
Stale rejection weight is never retracted when a signer flips a Reject to an Accept, letting the miner wrongly declare a live block "globally rejected" — ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener` tallies signer weight into two independent counters, `total_weight_approved` and `total_weight_rejected`, using two different dedup keys. Because a signer's earlier `Rejected` weight is never removed when that same signer later sends `Accepted` for the same block, a single signer's re-evaluation (an explicitly supported state transition, `LocallyRejected → LocallyAccepted`) leaves stale rejection weight permanently counted toward the ">30% blocking" threshold, alongside their new, legitimate approval weight. This double-bookkeeping is the same root-cause shape as the `BathBuddy` bug: one contribution (a signer's weight) is credited into two mutually-exclusive tallies at once because the two tallies are dedup-guarded by different keys instead of one shared, mutually-exclusive state.

### Finding Description
The block-status tally lives in `BlockStatus` [1](#0-0) .

On `Accepted`, the dedup check is against `gathered_signatures` (a `slot_id -> signature` map), and `responded_signers` is inserted unconditionally afterward: [2](#0-1) 

On `Rejected`, the dedup check and the weight increment both hinge on inserting into the *same* `responded_signers` set used by the accept path: [3](#0-2) 

Trace the two possible orderings for one signer, slot `X`, weight `w`:

- **Reject then Accept** (a signer that re-evaluates from `LocallyRejected` to `LocallyAccepted`, a transition the signer-side state machine explicitly allows, per `docs/signer-flows.md` section 2 — `LocallyRejected --> LocallyAccepted : re-evaluated`): the `Rejected` message arrives first, `responded_signers.insert(X)` succeeds, `total_weight_rejected += w`. Later the same signer's `Accepted` arrives; the accept path only checks `gathered_signatures.contains_key(X)`, which is `false` (a separate map), so `total_weight_approved += w` as well. **`w` is now counted in both totals simultaneously**, and `total_weight_rejected` is never decremented — it is a monotonically increasing, stale value that no longer reflects the signer's current vote.
- **Accept then Reject**: the reverse case is intentionally protected (a signature is a "bearer instrument" per the docs, so a later rejection from the same signer is correctly dropped because `responded_signers.insert(X)` already returns `false`).

So the bug is one-directional: it is only the reject→accept re-evaluation path that leaves ghost weight in `total_weight_rejected`.

This stale weight then feeds directly into the miner-side threshold decision in `SignerCoordinator::get_block_status`: [4](#0-3) 

The rejection-threshold check (`total_weight_rejected + weight_threshold > total_weight`) is evaluated before the approval-threshold check (`total_weight_approved >= weight_threshold`), so accumulated *stale* rejection weight from signers who have since switched to accepting can push the miner into concluding the block can never reach 70% approval, even when the live, current signer opinions would actually clear the threshold.

### Impact Explanation
This is a liveness wedge on block production: a single signer re-evaluating its vote from reject to accept — an ordinary, documented, single-signer action requiring no majority, no other signer's key, and no local/auth_token access — permanently pollutes the shared rejection tally. Combined with a small number of other signers holding out or being slow, this stale weight can push the miner's coordinator past the `total_weight_rejected + weight_threshold > total_weight` trip-wire and cause it to abort the proposal via `NakamotoNodeError::SignersRejected` even though the block currently has (or would soon have) enough live approving weight to reach consensus. This matches the "signer wedged into never signing valid blocks" / miscounted-response class of impact called out in the rules, mirroring how `BathBuddy`'s double-counted fee balance corrupted a shared accounting total used to gate distribution.

### Likelihood Explanation
The re-evaluation transition (`LocallyRejected → LocallyAccepted`) is a normal part of the signer's state machine (see `docs/signer-flows.md` section 2, `BlockInfo::check_state`), triggered whenever a rejection reason becomes stale/re-evaluable (`should_reevaluate_reject_reason`) and the block is subsequently re-validated and pre-committed/signed. No adversarial coordination or majority is needed — a single honest signer that initially rejects (e.g., transient validation failure or timing) and later legitimately signs after conditions clear will always leave stale weight behind, so the bug is triggerable in ordinary operation, not just by a malicious actor.

### Recommendation
Track each signer's current vote as a single, mutually-exclusive piece of per-slot state (e.g., `HashMap<u32, Vote>` where `Vote` is `Approved(weight)` or `Rejected(weight)`), and recompute `total_weight_approved`/`total_weight_rejected` by summing over that map rather than maintaining two independently-incremented, never-decremented counters. When a slot transitions from `Rejected` to `Approved`, subtract the stale weight from `total_weight_rejected` (or simply recompute both totals from the single source of truth) so a signer's weight can never be counted toward both thresholds at once.

### Proof of Concept
1. Signer set with total weight 100, `weight_threshold` = 70 (70%), signer `S` has weight 15.
2. `S` initially rejects a proposal (transient reason) → `stackerdb_listener.rs` records `total_weight_rejected = 15` via `responded_signers.insert(S)` at [3](#0-2) .
3. Other signers accrue rejections too, bringing `total_weight_rejected` to 20 from genuinely-opposed signers.
4. `S` re-evaluates (its local reject reason becomes stale/re-evaluable) and signs, sending `Accepted`. The accept-path dedup check only looks at `gathered_signatures` (line 443), not `responded_signers`, so `total_weight_approved += 15` — but `total_weight_rejected` still holds `S`'s stale 15, remaining at 20 total.
5. Suppose subsequently 3 more signers with combined weight 12 send genuine rejections, bringing `total_weight_rejected` to 32. `SignerCoordinator::get_block_status` now evaluates `32 + 70 > 100` → true, and the miner aborts with `NakamotoNodeError::SignersRejected` [4](#0-3) , even though `S`'s current, live vote is Approve — the true current opposition (20 - 15 stale + 12 = 17) would not have crossed the 30-weight blocking minority (`total_weight - weight_threshold = 30`).

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L70-82)
```rust
#[derive(Debug, Clone)]
pub struct BlockStatus {
    /// Set of the slot ids of signers who have responded
    pub responded_signers: HashSet<u32>,
    /// Map of the slot id of signers who have signed the block and their signature
    pub gathered_signatures: BTreeMap<u32, MessageSignature>,
    /// Total weight of signers who have signed the block
    pub total_weight_approved: u32,
    /// Total weight of signers who have rejected the block
    pub total_weight_rejected: u32,
    /// Per-txid rejection tracking from signers
    pub failed_txids: HashMap<Txid, FailedTxInfo>,
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
