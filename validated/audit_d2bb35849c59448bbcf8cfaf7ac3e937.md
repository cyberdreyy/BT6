### Title
Rejection weight is never cleared when a signer later accepts, letting the same signer's weight double-count in both `total_weight_rejected` and `total_weight_approved` - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener` maintains two independent weight tallies per proposed block — `total_weight_approved` and `total_weight_rejected` — inside a shared `BlockStatus` struct, which `SignerCoordinator` reads to decide whether a block should be treated as accepted or rejected. The guard that prevents double-counting a signer's weight is asymmetric between the two message handlers, so a signer that first rejects a block and later accepts the same block (a supported, legitimate re-evaluation flow in `stacks-signer`) ends up with its weight counted in *both* totals simultaneously.

### Finding Description
`BlockStatus` tracks, per slot id: `responded_signers`, `gathered_signatures`, `total_weight_approved`, and `total_weight_rejected`. [1](#0-0) 

When a `BlockResponse::Rejected` is processed, the weight is added to `total_weight_rejected` guarded only by inserting the slot id into `responded_signers`: [2](#0-1) 

When a `BlockResponse::Accepted` is later processed for the *same* `signer_signature_hash`, the guard checked is `gathered_signatures.contains_key(&slot_id)` — not `responded_signers`: [3](#0-2) 

Because a prior rejection only populates `responded_signers` (never `gathered_signatures`), this gate does not see that the signer already contributed weight via a rejection. The subsequent acceptance therefore adds the signer's weight to `total_weight_approved` on top of the weight that already remains in `total_weight_rejected` from the earlier rejection — that stale rejected weight is never decremented or cleared. The result is that a single signer's weight can be counted toward both the approval tally and the rejection tally for the same block at the same time, breaking the invariant that `total_weight_approved + total_weight_rejected` (for distinct signers) should never exceed `total_weight`, and, more importantly, that a rejection is not silently "carried forward" once the same signer switches to acceptance.

This flip (reject → re-evaluate → accept) for the same block is an explicit, supported behavior in `stacks-signer/src/v0/signer.rs`: `handle_block_proposal` re-runs `should_reevaluate_block` / `should_reevaluate_reject_reason` on a known/previously-tracked block and can transition a `LocallyRejected` `BlockInfo` back through evaluation to `LocallyAccepted` when the reject reason is deemed re-evaluable (per `docs/signer-flows.md`, section 3, and `BlockInfo::check_state`, which explicitly allows `LocallyRejected -> LocallyAccepted` re-evaluation transitions): [4](#0-3) 

Once the local signer state machine flips and re-sends a `BlockResponse::Accepted` message over StackerDB, the miner-side `StackerDBListener` observes both the earlier `Rejected` and the later `Accepted` message for the same `signer_signature_hash`/slot id, and — per the code above — retains the stale rejected weight while also adding the new approved weight.

`SignerCoordinator::wait_for_signer_signature` (or its equivalent polling loop) consumes these two counters independently to decide the outcome: [5](#0-4) 

### Impact Explanation
This maps directly to the "a rejection recounted as an accept" / miscounted-response class called out in scope. The immediate, concrete consequences are:

- `total_weight_rejected` retains weight from a signer who has since accepted the block. If that stale rejected weight, combined with other genuine rejecters, crosses the blocking-minority threshold (`total_weight_rejected + weight_threshold > total_weight`), the coordinator will treat a block as globally rejected (and even permanently exclude "problematic" txids reported in that stale rejection) even though the signer in question no longer actually rejects the block. This is a state-consistency break that can wedge/derail block production based on stale data (liveness impact on mining), and can distort the exclusion of transactions that a supermajority no longer actually considers problematic.
- More critically, because the two counters are not mutually exclusive per signer, the sum `total_weight_approved + total_weight_rejected` can exceed the true `total_weight` of distinct signers who responded, meaning the aggregated weight bookkeeping no longer reflects an honest 1-signer-1-weight-per-vote accounting — this is exactly the "aggregated-weight vs. verified-accepts" equality violation flagged as in-scope.

The one-slot signer needed to trigger this (the signer who flips from reject to accept) is not required to be malicious or majority — a normal signer following the documented re-evaluation flow (e.g., after new burn/stacks-block arrival or state-machine update changes its view) can trigger the inconsistency merely by rejecting then legitimately accepting the same proposal.

### Likelihood Explanation
The reject→accept re-evaluation path is a designed, documented feature of the signer (`should_reevaluate_block`/`should_reevaluate_reject_reason`, `docs/signer-flows.md` §3), not an edge case requiring adversarial behavior. Any timing where a signer's local view changes between the first evaluation and a later re-proposal or replayed pending response (both explicitly supported) is sufficient. No majority collusion or key compromise is needed — a single well-behaved signer's ordinary vote flip is enough to trigger the double count on the miner/coordinator side.

### Recommendation
In the `BlockResponse::Accepted` handler in `stackerdb_listener.rs`, before adding weight to `total_weight_approved`, check whether the slot id is present in `responded_signers` with a prior rejection and, if so, subtract (`saturating_sub`) that signer's weight from `total_weight_rejected` (and remove any of that signer's contributions from `failed_txids`) before crediting it to `total_weight_approved`. Symmetrically, ensure the reverse transition (accept → reject, already guarded via `responded_signers.insert`) is either disallowed or also correctly moves weight between the two tallies rather than silently dropping it. The single source of truth should be "the current tallied weight per responding signer," recomputed consistently on any status transition, analogous to recommending an "update" call before mutating the aggregate in the referenced Blend finding.

### Proof of Concept
1. Miner proposes block B; signer S (weight W) initially rejects it (e.g. `ValidationFailed` or a transient reason). `stackerdb_listener.rs` records: `responded_signers.insert(S.slot)`, `total_weight_rejected += W`.
2. S's local chainstate view updates (e.g. new burn block / state-machine update makes the reject reason re-evaluable) and `handle_block_proposal`/`should_reevaluate_block` transitions S's local `BlockInfo` for B from `LocallyRejected` to `LocallyAccepted`, and S broadcasts `BlockResponse::Accepted` for the same `signer_signature_hash`.
3. `stackerdb_listener.rs` receives the `Accepted` message: since `gathered_signatures` does not contain S's slot id (only `responded_signers` does, from step 1), the gate passes, and `total_weight_approved += W` is applied — while `total_weight_rejected` still includes W from step 1.
4. Now `total_weight_approved + total_weight_rejected` exceeds the true distinct-signer weight, and if other signers also rejected, `total_weight_rejected` may cross the blocking-minority threshold using S's stale weight, causing `SignerCoordinator` to abort with `NakamotoNodeError::SignersRejected` (and permanently/temporarily exclude txids) even though S has since accepted the block, or vice versa the approved tally is inflated with weight not matched by a "clean" current-only rejection accounting.

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

**File:** docs/signer-flows.md (L141-145)
```markdown
    PreCommitted --> LocallyAccepted : mark_locally_accepted = WE SIGN
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
