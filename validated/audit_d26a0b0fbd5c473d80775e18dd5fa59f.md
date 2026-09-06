### Title
Miner's per-block weight tally double-counts a signer who rejects a block and later reconsiders and accepts it, breaking the accept/reject weight invariant and allowing spurious global rejection of a signable block - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
The Nakamoto miner-side `StackerDBListener` tallies signer weight for a proposed block into two independent counters, `total_weight_approved` and `total_weight_rejected`, gated by two different, non-exclusive membership sets (`gathered_signatures` for approvals, `responded_signers` shared by both approvals and rejections). Because the signer protocol explicitly allows a signer to reject a block and later reconsider and accept the same block (`should_reevaluate_reject_reason` in `stacks-signer/src/v0/signer.rs`), a single signer's weight can end up counted in *both* `total_weight_rejected` and `total_weight_approved` for the same `signer_signature_hash`, without ever being subtracted from the rejected total. This is the same class of defect as GHSA-8gw7-4j42-w388: the "recorded verdict" (rejected/accepted weight tallies) is not kept consistent with the actual, current set of verified per-signer stances, so the aggregate can silently violate the invariant that a signer's weight should count toward at most one side.

### Finding Description
`BlockStatus` tracks per-block tallying state: [1](#0-0) 

In the `BlockResponse::Accepted` branch, weight is added to `total_weight_approved` only if the slot is not already in `gathered_signatures`: [2](#0-1) 

In the `BlockResponse::Rejected` branch, weight is added to `total_weight_rejected` only if the slot is not already in `responded_signers` — a *separate* set that is also written to by the acceptance branch (line 465, `block.responded_signers.insert(slot_id)`): [3](#0-2) 

The two gating sets are not the same set, and neither branch ever removes a slot's weight from the *other* tally when the signer's verdict changes. Since a signer's `handle_block_proposal` flow explicitly supports switching from `Rejected` to `Accepted` for the same block (see `should_reevaluate_reject_reason` / `should_reevaluate_block` in `stacks-signer/src/v0/signer.rs`, which re-runs evaluation and eventually calls `determine_response`/`create_block_acceptance` for the same `signer_signature_hash` after a prior rejection was recorded), the sequence:

1. Signer S rejects block B (weight w added to `total_weight_rejected`, slot recorded in `responded_signers`).
2. Signer S later reconsiders (a documented, non-malicious path) and accepts block B (weight w added to `total_weight_approved`, because `gathered_signatures` does not yet contain S's slot).

results in weight `w` being counted in *both* tallies simultaneously, so `total_weight_approved + total_weight_rejected` can exceed `self.total_weight`. This breaks the aggregated-weight vs. verified-accepts equality the coordinator relies on to decide whether a block is globally accepted or globally rejected.

The rejection-threshold check that can fire on this corrupted tally: [4](#0-3) 

fires `cvar.notify_all()` for "enough rejections" based on the double-counted `total_weight_rejected`, even though the same weight is simultaneously present in `total_weight_approved`. The waiter on this condition (elsewhere in the miner/coordinator code, not shown in the excerpts retrieved) treats crossing the rejection threshold as a signal that ≥70% acceptance is now impossible; with the double count, this can trigger with less real, distinct rejecting weight than the threshold actually requires.

### Impact Explanation
This is an aggregated-weight vs. verified-accepts equality break as covered by the report's scope: the coordinator's rejected/approved weight sums no longer partition the signer set correctly, so the miner can incorrectly conclude a block is "globally rejected" (or compute a distorted percentage) while some of that rejecting weight in fact belongs to a signer who has since accepted the block. Practically, this can cause the miner to abandon a block that was in fact on a legitimate path to reaching the acceptance threshold, forcing it to discard/re-propose, which is a liveness degradation for block production (the miner is repeatedly wedged into giving up on blocks it should be able to get signed). It does not require a majority of signers or any signer's private key — it can be triggered by the natural, protocol-sanctioned reject→reconsider→accept transition of a single signer, orchestrated by a one-slot miner that proposes a block in a way that some signers initially reject (e.g., for a reconsiderable reason) and later re-accept once conditions change.

### Likelihood Explanation
Moderate. The reject→accept reconsideration path is a documented part of the normal protocol flow (`should_reevaluate_reject_reason`), not an edge case requiring an adversarial signer; it is expected to happen under ordinary conditions (e.g., stale sortition view causing early rejection, later resolved). A miner or attacker with the ability to shape when proposals are (re-)broadcast, or ordinary network timing variance across signers, is likely to hit this window during normal operation, especially with signers near the reject/accept decision boundary.

### Recommendation
Track a single per-slot "current verdict" (Accepted/Rejected/Unknown) rather than two independently-gated weight accumulators, and when a signer's `signer_signature_hash`-scoped verdict changes, subtract the previously counted weight from the old bucket before adding it to the new one, so that `total_weight_approved + total_weight_rejected` for a given block never double-counts a single signer's weight.

### Proof of Concept
Conceptual reproduction (network-level or unit-test level, no key access beyond the participating signer's own key is required):
1. Configure the miner/coordinator with N signers where signer S has weight w.
2. Cause S to send `BlockResponse::Rejected` for block B with a reconsiderable reject reason (per `should_reevaluate_reject_reason`), e.g. because the block was momentarily seen as stale relative to S's view. `stackerdb_listener.rs` records `total_weight_rejected += w` and `responded_signers.insert(S)`.
3. Re-propose the same block B (same `signer_signature_hash`) after the condition that caused S's rejection is resolved; S now re-evaluates and sends `BlockResponse::Accepted` for B.
4. `stackerdb_listener.rs`'s accepted branch sees `!gathered_signatures.contains_key(S)` is true (S's slot was never added there), so it adds `total_weight_approved += w` as well — `total_weight_rejected` is never decremented.
5. Observe `total_weight_approved + total_weight_rejected > self.total_weight` for block B, and, with a few more genuinely rejecting signers, observe the check at lines 567–574 fire a global-rejection signal even though S's current, real vote is "accept." [2](#0-1) [3](#0-2) [4](#0-3)

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
