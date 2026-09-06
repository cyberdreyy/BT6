### Title
Signer weight double-counted across approval and rejection tallies when a signer's vote flips - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
In `StackerDBListener`'s message-processing loop, a signer's weight can be counted toward `total_weight_approved` even after that same signer's weight was already counted toward `total_weight_rejected` for the same block (or vice-versa is prevented, but the accept path is not). The rejection path correctly guards against re-counting a signer who already responded, but the acceptance path uses a different, unrelated set (`gathered_signatures`) as its de-duplication key instead of `responded_signers`, so a signer who rejected first and later sends an `Accepted` message is added into `total_weight_approved` without their earlier `total_weight_rejected` contribution being rolled back. This is directly analogous to the CLGauge bug: a previously-tallied quantity (rejected weight) is not "rolled over"/reconciled when a new tally (approved weight) is computed for the same actor, so the two accumulators silently exceed the true total.

### Finding Description
`BlockStatus` tracks two independent counters, `total_weight_approved` and `total_weight_rejected`, plus a `responded_signers: HashSet<u32>` meant to represent "this signer already cast a vote for this block." [1](#0-0) 

For the `Rejected` branch, the code correctly uses `responded_signers` as the single source of truth to avoid double counting:
```
if block.responded_signers.insert(slot_id) {
    block.total_weight_rejected = block.total_weight_rejected.saturating_add(signer_entry.weight);
    ...
}
``` [2](#0-1) 

But for the `Accepted` branch, the guard against re-adding weight is based on `gathered_signatures.contains_key(&slot_id)` — a different map that only tracks whether a *signature* for this slot has previously been recorded — not on `responded_signers`:
```
if !block.gathered_signatures.contains_key(&slot_id) {
    block.total_weight_approved = block.total_weight_approved.saturating_add(signer_entry.weight);
    ...
}
block.gathered_signatures.insert(slot_id, signature);
block.responded_signers.insert(slot_id);
``` [3](#0-2) 

Because `gathered_signatures` and `responded_signers` are separate collections, if a signer's *first* message for a given block is a `Rejected` (which inserts them into `responded_signers` and adds their weight to `total_weight_rejected`, per lines 515-518) and their *second* message for the same block is `Accepted`, the check at line 443 (`!gathered_signatures.contains_key(&slot_id)`) is still true (they never had an entry in `gathered_signatures`), so their weight is added a second time — now to `total_weight_approved` as well. The same signer's weight is now counted in *both* accumulators. This breaks the intended invariant that `total_weight_approved + total_weight_rejected <= total_weight`, i.e. it breaks the "aggregated-weight vs. verified-accepts" equality: the coordinator can believe it has crossed the 70% approval threshold (`total_weight_approved >= self.weight_threshold`, line 467) using weight that is not exclusively backing that outcome, because part of it was already spent against the rejection tally for the same signer.

### Impact Explanation
This is a single-signer-triggerable accounting break in the node-side signer coordinator (`StackerDBListener`/`SignerCoordinator`), which the miner uses to decide when enough signer weight has accumulated to assemble and push a block. A single (potentially malicious or buggy/equivocating) signer can inflate `total_weight_approved` by first rejecting and then accepting the same block proposal, without any other signer's cooperation. This can cause the miner to conclude the 70% approval weight threshold has been reached with less *genuinely and exclusively* approving weight than required, undermining the safety property that a block is only pushed once a real 70% supermajority of distinct signer weight has approved it — an "aggregated-weight vs verified-accepts" equality violation as called out in scope. It does not, by itself, forge a signature (the actual aggregate signature still only includes genuinely verified per-slot signatures from `gathered_signatures`), but it corrupts the threshold bookkeeping that gates whether the miner should trust it has reached quorum, which is a High-risk liveness/safety bookkeeping defect in the vote-tallying logic.

### Likelihood Explanation
Likelihood is high for any environment where a signer can send both a `Rejected` and later an `Accepted` `BlockResponse` for the same `signer_signature_hash` — e.g. a byzantine/equivocating signer, or a legitimate signer that re-evaluates and changes its mind (the codebase elsewhere explicitly supports re-evaluation of blocks, e.g. `should_reevaluate_block`/`LocallyRejected -> LocallyAccepted` transitions documented in `docs/signer-flows.md`). No majority of signers or privileged access is required — a single StackerDB-writing signer slot is sufficient to trigger the double count.

### Recommendation
Use a single, unified data structure (or reconcile against `responded_signers`) to gate weight additions on both the accept and reject code paths, and subtract/roll back any previously-tallied weight for a slot before adding it to the opposite tally when a signer's vote changes for the same block, mirroring how the reject path already guards via `responded_signers`. Concretely, change line 443's guard from `!block.gathered_signatures.contains_key(&slot_id)` to check `responded_signers` (and if the slot previously contributed to `total_weight_rejected`, decrement that counter when moving the same slot's weight into `total_weight_approved`), so the two totals remain mutually exclusive and their sum never exceeds `total_weight`.

### Proof of Concept
1. Miner submits a block proposal `B` (assembled hash `H`) and awaits signer responses via `StackerDBListener`.
2. Signer S (slot `k`, weight `w`) sends a `BlockResponse::Rejected` for `H`. Handler inserts `k` into `responded_signers` and adds `w` to `total_weight_rejected` (lines 515-518).
3. The same signer S subsequently sends a `BlockResponse::Accepted` for the same `H` (e.g., after re-evaluating the proposal, or via equivocation). Handler checks `gathered_signatures.contains_key(&k)` — false, since S never previously sent an `Accepted` message — so it adds `w` to `total_weight_approved` (lines 443-446), then inserts `k` into `gathered_signatures` and (again) into `responded_signers` (lines 464-465).
4. Now `total_weight_approved` includes S's weight `w`, and `total_weight_rejected` still also includes S's weight `w`. If enough other signers approve, `total_weight_approved` can cross `weight_threshold` while `total_weight_approved + total_weight_rejected > total_weight`, demonstrating the corrupted, non-exclusive tally that a single signer flipping its vote can produce.

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
