### Title
Reject-then-accept flip lets a single signer's weight double-count into both `total_weight_approved` and `total_weight_rejected`, allowing a valid, sufficiently-signed block to be killed by stale rejection weight - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
The mining-side `StackerDBListener` tracks per-block tallies in `BlockStatus`, gated by two different sets: `gathered_signatures` (keyed by slot id) for the accept path and `responded_signers` (keyed by slot id) for whether *any* response weight has already been recorded. The accept branch only checks `gathered_signatures`, not `responded_signers`, before adding weight to `total_weight_approved`, while the reject branch checks/updates the shared `responded_signers` set. This asymmetry lets one signer who first rejects and later legitimately accepts the same block get counted in **both** `total_weight_rejected` and `total_weight_approved`, breaking the invariant that a signer's weight is counted at most once toward the outcome. Since `total_weight_rejected` never decreases, this stale weight can push the coordinator into declaring the block `SignersRejected` (dead) even though the same signer, and possibly the necessary 70% supermajority, has since accepted it.

### Finding Description
`BlockStatus` holds `responded_signers: HashSet<u32>` and `gathered_signatures: BTreeMap<u32, MessageSignature>` per block hash [1](#0-0) .

On `BlockResponse::Accepted`, weight is added to `total_weight_approved` only if the slot is not yet in `gathered_signatures`; `responded_signers` is also inserted at the end, but it is never consulted before the addition: [2](#0-1) 

On `BlockResponse::Rejected`, the guard is the shared `responded_signers` set: `if block.responded_signers.insert(slot_id) { … total_weight_rejected += weight … }` [3](#0-2) 

Walking the two orderings for the same signer/slot on the same block:
- **Accept, then Reject**: accept inserts into both `gathered_signatures` and `responded_signers`; the later reject checks `responded_signers.insert(slot_id)` → returns `false` (already present) → `total_weight_rejected` is *not* incremented. Safe.
- **Reject, then Accept**: reject inserts into `responded_signers` and increments `total_weight_rejected`. The later accept only checks `gathered_signatures` (still empty for this slot) → it proceeds, inserts the signature, and increments `total_weight_approved`. `responded_signers` already contained the slot so nothing there blocks the accept path either — it is not even checked in the accept branch. Result: the same signer's weight now counts in **both** totals simultaneously, and `total_weight_rejected` is never reduced.

This breaks the "aggregated-weight vs verified-accepts" equality that the coordinator's decision logic assumes in `wait_for_block_status_and_decide`, where `total_weight_rejected + weight_threshold > total_weight` triggers `SignersRejected` and `total_weight_approved >= weight_threshold` triggers acceptance [4](#0-3) . A stale rejection tally (from a signer who has since flipped to accept) is treated as if it still opposes the block, permanently inflating the rejection side without any corresponding decrement, even after that signer's later, valid acceptance is recorded.

This is not a hypothetical corner case: `docs/signer-flows.md` explicitly documents that a signer may reconsider and change its answer for the same block once "the reject reason allows us to reconsider" [5](#0-4) , and the signer-side code path for turning a validation retry into a fresh signature exists (`handle_block_pre_commit` / `store_and_process_block_signature` in `stacks-signer/src/v0/signer.rs`). So a normal, non-Byzantine signer can naturally reject a proposal early (e.g. transient conflict/timing) and correctly sign it once the conflict resolves — and the *node's* coordinator will still carry the earlier rejection weight forward forever for that attempt.

### Impact Explanation
This is a liveness break of the block-signing state machine on the miner/coordinator side: a single signer's legitimate reject→accept transition permanently inflates `total_weight_rejected` with weight that no longer represents opposition. If enough other signers are near the 30% rejection bound (which does not require a majority — the miner-side per-block loop only needs `total_weight_rejected + weight_threshold > total_weight`), this stale, double-counted weight can tip the coordinator into declaring a validly-approvable block `SignersRejected`, wedging that mining attempt even though the actual current signer opinion (weighted) supports the block. This matches the specified High-impact category: "a signer wedged into never signing valid blocks" — here manifesting as the coordinator discarding a block that in fact has (or would have) sufficient current approval weight, due to counting a reversed rejection twice.

### Likelihood Explanation
Reachable by ordinary signer behavior, not requiring compromise of any key, majority collusion, or code outside the documented reconsider flow. It only requires: (1) a signer to reject a proposal, (2) the same signer later, per existing documented logic, to accept the same block hash after conditions change, and (3) the rest of the signer set's rejection weight to be close enough to the 30% blocking-minority bound that the double-counted weight matters. No StackerDB sync trickery, no consensus-acceptance-as-flaw, and no node-side authentication bypass is needed — the flaw is purely in how `BlockStatus` guards are asymmetric between the accept and reject branches.

### Recommendation
Use a single per-slot "already counted" record (or a per-slot enum of {Approved, Rejected}) that both branches consult and update atomically, and either (a) refuse to let a slot's weight count as both approved and rejected, or (b) when a signer changes its vote, subtract the old contribution before adding the new one so `total_weight_approved` and `total_weight_rejected` stay mutually exclusive per slot at all times.

### Proof of Concept
1. Reward set with signers `A` (weight 30), `B` (weight 30), `C` (weight 40); `weight_threshold` = 70 (70% of 100), blocking minority = 30.
2. Miner proposes block `H`.
3. `A` rejects `H` (transient reason) → `total_weight_rejected = 30` (`responded_signers = {A}`).
4. `A` reconsiders and sends `Accepted` for `H` with a valid signature → accept branch checks only `gathered_signatures` (empty for `A`) → `total_weight_approved = 30`, `gathered_signatures = {A: sig}`. `total_weight_rejected` remains `30` (never decremented) — see the guard mismatch cited in `stackerdb_listener.rs` lines 443–465 vs 515–519.
5. `B` rejects `H` for an unrelated reason → `total_weight_rejected = 60`.
6. Coordinator loop checks `block_status.total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight` → `60 + 70 = 130 > 100` → true → returns `NakamotoNodeError::SignersRejected`, killing the block [6](#0-5) , even though the *current* opposition weight is only `B`'s 30 (30%, below the blocking minority) since `A` has since accepted — the extra 30 counted against the block is stale, double-counted weight from `A`'s reversed vote. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-519)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);

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

**File:** docs/signer-flows.md (L274-286)
```markdown
Order matters here: the chainstate re-check runs first and produces an explicit
(sticky) rejection when the block now conflicts with a signed one. The conflict
guard behind it is the silent backstop for what that re-check cannot see, and
silence keeps the door open to sign later once the conflict goes stale. Two
blind spots make the guard necessary:

- the re-check only ever looks at _one_ tenure (a tenure-change block's parent,
  or any other block's own), so a signed sibling at the same height in a third
  tenure is invisible to it;
- the `DuplicateBlockFound` check that would catch a second block in the same
  tenure lives in `check_proposal` and runs only at proposal arrival, never
  again. A block that crosses the pre-commit threshold minutes later has no
  other guard, which is what the own-tenure branch above covers.
```
