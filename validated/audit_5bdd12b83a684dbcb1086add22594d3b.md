### Title
Reject-then-accept vote flip lets a signer's own weight be double-counted into both `total_weight_rejected` and `total_weight_approved`, letting a small rejecting minority force a premature `SignersRejected` on an otherwise-signable block - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener::run` tracks per-block tallies in a `BlockStatus` struct with two independent counters, `total_weight_approved` and `total_weight_rejected`, gated by a single `responded_signers: HashSet<u32>` and a separate `gathered_signatures: BTreeMap<u32, MessageSignature>`. [1](#0-0)  The `Rejected` arm only records a rejection weight if `responded_signers.insert(slot_id)` succeeds (i.e., the signer hasn't been seen at all yet), while the `Accepted` arm only gates on `gathered_signatures.contains_key(&slot_id)`, ignoring `responded_signers` entirely. [2](#0-1) [3](#0-2)  As a result, a signer that first rejects a block and later legitimately re-evaluates and signs it (an explicitly documented, expected transition: `LocallyRejected --> LocallyAccepted : re-evaluated`) has its weight added to `total_weight_rejected` *and*, on the later accept, added to `total_weight_approved` too — the stale rejected weight is never subtracted. [4](#0-3) 

### Finding Description
`BlockStatus` is meant to enforce that each signer's weight counts toward at most one side of the tally (approve or reject) so the coordinator's threshold arithmetic (`total_weight_approved`, `total_weight_rejected`, `weight_threshold`, `total_weight`) remains a coherent partition of the reward set's weight. This is the exact accounting invariant broken in the external report's analog: a balance/mapping is incremented for an actor without the corresponding compensating decrement, producing an inconsistent state.

Concretely:
- On `BlockResponse::Rejected`, weight is added to `total_weight_rejected` and `slot_id` is inserted into `responded_signers`. [3](#0-2) 
- On a later `BlockResponse::Accepted` from the *same* signer for the *same* block (a supported protocol path per the signer's `BlockInfo` state machine, which allows `LocallyRejected --> LocallyAccepted` on re-evaluation), the check is only `!block.gathered_signatures.contains_key(&slot_id)`, which is still true, so `total_weight_approved` is incremented by that signer's weight as well. [2](#0-1) 
- Nothing in the `Accepted` path removes the previously accrued weight from `total_weight_rejected`.

The only place `total_weight_rejected` is ever cleared is `reset_rejections`, and that is invoked solely on a full response-timeout inside `SignerCoordinator::get_block_status`, not on a per-signer vote flip. [5](#0-4)  Until that timeout fires, the coordinator's live view of `total_weight_rejected` includes weight from signers who have since switched to accepting.

The coordinator's decision loop checks the rejection-quorum condition *before* the acceptance-quorum condition:
```
if block_status.total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight {
    ... return Err(NakamotoNodeError::SignersRejected { ... })
} else if block_status.total_weight_approved >= self.weight_threshold {
    ... return Ok(...)
}
``` [6](#0-5)  If the phantom stale-rejected weight pushes `total_weight_rejected` past the blocking-minority threshold (`total_weight - weight_threshold`, i.e., >30% weight) at the moment the coordinator polls, the miner declares the block dead via `SignersRejected` even though the real, current set of votes (after the flip) may already have reached — or been about to reach — the 70% approval threshold. This breaks the "aggregated-weight vs verified-accepts" equality the coordinator relies on: `total_weight_rejected` no longer reflects the set of signers currently rejecting the block; it reflects the set of signers who have *ever* rejected it, super-set of the current rejecters.

### Impact Explanation
This is a liveness break, not requiring a majority of signers — only whatever weight is needed to approach the ~30% blocking-minority threshold together with the residual weight left over from one or more signers who flipped their vote from reject to accept. Because `LocallyRejected --> LocallyAccepted` re-evaluation is a normal, expected signer behavior (documented in the state machine), this can occur without any signer acting maliciously or needing majority collusion — natural races between a signer's initial rejection (e.g., due to a stale view) and its subsequent correction after `should_reevaluate_block`/`should_reevaluate_reject_reason` are exactly the scenario the signer's state machine is designed to handle. The miner, however, treats the flip-flopping signer's earlier rejection as permanent, artificially inflating `total_weight_rejected` and causing the coordinator to abort a proposal (`SignersRejected`) that should otherwise have been signable, forcing the miner to rebuild/exclude transactions and retry — a direct hit to block-production liveness for that tenure.

### Likelihood Explanation
Medium-High. The signer-side re-evaluation path (`should_reevaluate_block`, `LocallyRejected --> LocallyAccepted`) is a core, intentional part of the protocol, not a contrived edge case, so vote flips from reject to accept for the same block happen under ordinary operating conditions (e.g., resolution of a competing/rival block, timing races during pre-commit). Reaching the ~30% blocking-minority threshold with a mix of "genuine current rejecters" plus "stale rejecters who have since accepted" requires far less than a network majority, especially in signer sets with concentrated weight among a few large signers.

### Recommendation
Make `Accepted` and `Rejected` processing mutually exclusive per signer, and make vote transitions correct the tally instead of only ever adding to it:
- Before incrementing `total_weight_approved` on an `Accepted` message, check `responded_signers` (not just `gathered_signatures`); if the signer previously registered a rejection, subtract their weight from `total_weight_rejected` (or remove them from whatever data structure backs it) before adding it to `total_weight_approved`.
- Symmetrically, if an already-accepted signer later sends a `Rejected` (should not normally happen, but for defense-in-depth), do not allow silent no-ops that leave stale approved weight uncorrected either.
- Alternatively, replace the two separate weight accumulators with a single `HashMap<u32, Vote>` keyed by slot id (storing the latest vote and its weight) and recompute `total_weight_approved`/`total_weight_rejected` from that map on each update, guaranteeing the invariant `approved_weight + rejected_weight + not_yet_responded_weight == total_weight` at all times.

### Proof of Concept
1. Reward set has signers A (weight 25), B (weight 25), C (weight 50); `total_weight = 100`, `weight_threshold` (70%) = `70`, blocking minority = `30`.
2. Miner proposes block; A initially rejects (stale info) → `total_weight_rejected = 25`.
3. A re-evaluates locally (`should_reevaluate_block`/`LocallyRejected --> LocallyAccepted`) and sends `Accepted` → `stackerdb_listener.rs`'s `Accepted` arm only checks `gathered_signatures`, so `total_weight_approved` becomes `25`; `total_weight_rejected` remains `25` (never decremented).
4. Suppose B genuinely rejects (view mismatch) → `total_weight_rejected = 25 + 25 = 50`.
5. `SignerCoordinator::get_block_status` polls: `total_weight_rejected (50) + weight_threshold (70) = 120 > total_weight (100)` → coordinator returns `Err(SignersRejected)` and aborts the block, even though only B (weight 25, i.e., 25% — below the 30% blocking minority on its own) is currently rejecting; A's stale, retracted rejection is what pushed the tally over the line.
6. Without the stale weight bug, `total_weight_rejected` should be `25` (B only) at this point, which does not trigger `SignersRejected`, and once C accepts (`total_weight_approved` reaches `25 + 50 = 75 ≥ 70`), the block would be correctly signed and pushed.

This scenario needs no signer majority, no forged signatures, and no malicious actor — a single honest signer's documented reject→accept re-evaluation, combined with a genuinely rejecting minority under 30%, is sufficient to wedge block production for that proposal.

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

**File:** docs/signer-flows.md (L142-145)
```markdown
    Unprocessed --> LocallyRejected : mark_locally_rejected
    PreCommitted --> LocallyRejected : mark_locally_rejected
    LocallyRejected --> LocallyAccepted : re-evaluated
    LocallyAccepted --> LocallyRejected : re-evaluated
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
