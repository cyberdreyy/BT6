### Title
Stale rejection weight is never retracted when a signer later accepts the same block, corrupting the miner-side aggregated weight tally - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
In `StackerDBListener::run` (the node-side "coordinator" that tallies `BlockResponse` messages from signers), the accounting for `total_weight_approved` and `total_weight_rejected` in the shared `BlockStatus` struct is asymmetric: a rejection is only counted once (gated by `responded_signers.insert(slot_id)`), but an acceptance is gated by a *different* set (`gathered_signatures.contains_key(&slot_id)`), so a signer's weight can be added to `total_weight_approved` even after that same signer's weight was already added to `total_weight_rejected`. This is the same class of accounting error as M-17 (a derived/aggregated quantity double-uses/omits a subtraction), except here it is an aggregated-weight-vs-verified-accepts equality that silently breaks: the sum of `total_weight_approved + total_weight_rejected` for a single signer’s weight can exceed that signer's actual, single, current vote.

### Finding Description
`BlockStatus` tracks two independent weight counters and one shared "have we heard from this slot" set: [1](#0-0) 

For an `Accepted` response, the weight is added only if the slot is *not yet* in `gathered_signatures`: [2](#0-1) 

For a `Rejected` response, the weight is added only if the slot is *not yet* in `responded_signers` (which is shared with the `Accepted` branch, since `responded_signers.insert(slot_id)` also runs there): [3](#0-2) 

Because `responded_signers` is written by both branches but `gathered_signatures` is written only by the `Accepted` branch, the two orders are not symmetric:

- Accept-then-Reject: `responded_signers` already contains the slot from the accept, so the later reject's `responded_signers.insert(slot_id)` returns `false` and its weight is correctly **not** added. Safe.
- Reject-then-Accept: the reject adds weight to `total_weight_rejected` and inserts the slot into `responded_signers`. When the same signer later sends `Accepted` for the same block hash, the gate checked is `!block.gathered_signatures.contains_key(&slot_id)` — which is still empty for that slot — so the weight is **also** added to `total_weight_approved`. The stale `total_weight_rejected` contribution from the earlier rejection is never decremented.

The result: for that signer, weight is counted in *both* `total_weight_rejected` and `total_weight_approved` simultaneously, even though the signer currently holds only one (the "accept") position. This breaks the intended invariant that the two tallies reflect a partition of current, distinct signer positions summing to at most `total_weight`.

### Impact Explanation
This weight tally directly drives the miner's decision logic in `SignerCoordinator` (`stacks-node/src/nakamoto_node/signer_coordinator.rs`), which checks the rejection-threshold condition before the acceptance-threshold condition: [4](#0-3) 

Because `total_weight_rejected` can retain a stale contribution from a signer who has since accepted, the "blocking minority" (`total_weight_rejected + weight_threshold > total_weight`) condition can be satisfied earlier and more easily than the real, current signer state warrants. Since this check is evaluated first, it can cause the miner to declare `NakamotoNodeError::SignersRejected` for a block that, based on the signers' actual current votes, should instead have crossed the acceptance threshold. This is a liveness wedge on the miner/coordinator side: valid blocks that would otherwise gather enough live acceptances can be starved out by phantom rejection weight from signers who have already revised their vote to accept, and associated transactions can be temporarily/permanently excluded via the `failed_txids` path that piggybacks on the same stale rejection tally.

### Likelihood Explanation
Any signer that is allowed to change its `BlockResponse` from `Rejected` to `Accepted` for the same `signer_signature_hash` (e.g. after re-validation succeeds on a later attempt, or after a chainstate re-check flips outcome) triggers this without needing a majority of signers or any privileged access — a single signer flipping its own vote is sufficient to poison the shared tally that the one-slot miner/coordinator consumes. The report could not fully confirm from the available code whether the `stacks-signer` v0 logic ever legitimately re-emits `Accepted` after having emitted `Rejected` for the *same* block (this depends on re-validation/reconsideration paths in `stacks-signer/src/v0/signer.rs` that were not fully traced within the available tool budget), so likelihood is assessed as plausible but not fully proven end-to-end within this pass.

### Recommendation
Make the acceptance-weight gate consistent with the rejection-weight gate: when an `Accepted` response is stored for a slot that was previously counted in `total_weight_rejected` (i.e., `responded_signers` already contains the slot but `gathered_signatures` does not), retract the stale rejected weight (and any associated `failed_txids` weight) before/while adding the new approved weight, e.g. using a single per-slot "current vote" record instead of two independently-gated accumulators.

### Proof of Concept
1. Signer S (slot `k`, weight `w`) sends `BlockResponse::Rejected` for block `B`. `stackerdb_listener.rs` adds `w` to `block.total_weight_rejected` and inserts `k` into `responded_signers`.
2. Signer S later sends `BlockResponse::Accepted` for the same `B` (e.g., after re-validating and succeeding). Since `gathered_signatures` does not yet contain `k`, `stackerdb_listener.rs` adds `w` again, this time to `block.total_weight_approved`.
3. `block.total_weight_rejected` still includes `w` from step 1 — nothing decrements it.
4. `SignerCoordinator::run` (or equivalent polling loop) evaluates `total_weight_rejected + weight_threshold > total_weight` using the inflated `total_weight_rejected`, which can now trip true earlier than it should, causing the coordinator to reject/exclude the block/txids even though the *current* aggregate of live signer positions (`total_weight_approved` alone, ignoring the stale reject) may already meet or nearly meet `weight_threshold`. [2](#0-1) [3](#0-2) [4](#0-3)

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
