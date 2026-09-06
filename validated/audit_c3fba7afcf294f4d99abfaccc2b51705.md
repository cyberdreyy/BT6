## Analog Vulnerability Found

### Title
Stale, Double-Counted Rejection Weight in `StackerDBListener::main_loop` Can Wedge Block Finalization — ([File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`])

### Summary
The node-side `StackerDBListener` accumulates `total_weight_approved` and `total_weight_rejected` per `BlockStatus` entry as `BlockResponse` messages arrive from signers. When a signer flips its vote for the same block sighash from `Rejected` to `Accepted` (a state transition explicitly allowed by `BlockInfo::check_state`, i.e. `LocallyRejected -> LocallyAccepted`), the coordinator adds that signer's weight into `total_weight_approved` but never removes the earlier weight it already added into `total_weight_rejected`. The same asymmetry exists in the opposite direction. This is the same accounting-omission bug class as the reported `rewardTokensClaimed` never being decremented in `Vepoch.sol`: a per-entity value is incremented on one action but never adjusted when a later action supersedes it, so the aggregate diverges from ground truth.

### Finding Description
`BlockStatus` tracks, per proposed block, `total_weight_approved`, `total_weight_rejected`, `responded_signers`, and `gathered_signatures`: [1](#0-0) 

On `BlockResponse::Accepted`, weight is added to `total_weight_approved` guarded only by `!block.gathered_signatures.contains_key(&slot_id)` — there is no check of, or adjustment to, `total_weight_rejected`: [2](#0-1) 

On `BlockResponse::Rejected`, weight is added to `total_weight_rejected` guarded only by `block.responded_signers.insert(slot_id)` — there is no check of, or adjustment to, `total_weight_approved`/`gathered_signatures`: [3](#0-2) 

Because `responded_signers` is a single set shared across both paths, a signer that first rejects (inserted into `responded_signers`, weight added to `total_weight_rejected`) and later re-evaluates and accepts the same proposal will pass the accepted branch's `gathered_signatures` guard (it was never in `gathered_signatures`) and get its weight added a second time into `total_weight_approved`, while the earlier contribution to `total_weight_rejected` is never subtracted. The reverse ordering (accept-then-reject) hits the mirrored gap: `total_weight_rejected` gains the weight while the earlier `total_weight_approved` contribution and the slot's entry in `gathered_signatures` are left untouched.

This is the exact same class as the external report: an accounting field (`rewardTokensClaimed` there, `total_weight_approved`/`total_weight_rejected` here) accumulates monotonically per action but is never reconciled when a later action on the same entity supersedes the earlier one, so the aggregate no longer equals the true current state (verified accepts/rejects for that slot).

Contrast this with the fix already applied at the SignerDB layer for the equivalent local accounting (`add_block_rejection_signer_addr` explicitly refuses to record a rejection if a signature already exists for that signer/block, and the CHANGELOG documents "Do not count both a block acceptance and a block rejection for the same signer/block"): [4](#0-3) [5](#0-4) 

That guard exists in the *signer's own* database bookkeeping, but the analogous guard is missing in the *miner-node coordinator's* in-memory `BlockStatus` tally in `stackerdb_listener.rs`, which is the structure that actually drives the block-acceptance/rejection decision (`signer_coordinator.rs`'s `get_block_status` reads `total_weight_approved`/`total_weight_rejected` directly): [6](#0-5) 

### Impact Explanation
`total_weight_rejected` no longer equals the sum of weights of signers currently rejecting the block — it is inflated by any signer weight that flipped from reject to accept (or, symmetrically, `total_weight_approved` is inflated by weight that flipped from accept to reject). This breaks the aggregated-weight vs. verified-accepts/rejects equality the coordinator relies on at `stacks-node/src/nakamoto_node/signer_coordinator.rs:509-540`: the miner can compute `total_weight_rejected + weight_threshold > total_weight` and abort a *valid, canonical* block proposal as `SignersRejected` even though the true, current rejecting weight is below the blocking-minority threshold. Because `reset_rejections` only clears rejection state on a full-proposal timeout/retry and does not correct this per-signer double count on ordinary vote flips, this can wedge a specific block proposal's finalization purely from ordinary re-evaluation gossip traffic, which is explicitly an in-scope liveness consequence (a wedge in the signing/coordination state machine).

### Likelihood Explanation
This requires only a single signer (one-slot) to legitimately change its vote on the same block sighash — a state transition the protocol explicitly supports (`LocallyRejected -> LocallyAccepted` "re-evaluated" per `BlockInfo::check_state`/`docs/signer-flows.md` section 2) and that occurs in normal operation whenever a rejection reason becomes stale/re-evaluable. No majority collusion, no key compromise, and no auth-token access is needed — ordinary StackerDB gossip of two sequential, individually valid `BlockResponse` messages from one signer is sufficient.

### Recommendation
When processing `BlockResponse::Accepted`, if the slot is present in `responded_signers` from a prior rejection (i.e., not yet in `gathered_signatures`), subtract that signer's weight from `total_weight_rejected` (and analogously, remove it from any per-txid `failed_txids` weight it contributed) before/while adding it to `total_weight_approved`. Symmetrically, when processing `BlockResponse::Rejected` for a slot already present in `gathered_signatures`, remove that signer's weight from `total_weight_approved` and its entry from `gathered_signatures` before adding it to `total_weight_rejected`. This keeps `total_weight_approved`/`total_weight_rejected` equal to the sum of weights of signers whose *current* vote is accept/reject, matching the invariant already enforced in `stacks-signer/src/signerdb.rs::add_block_rejection_signer_addr`.

### Proof of Concept
1. Node proposes block `B` and calls `insert_block`, initializing `BlockStatus{ total_weight_approved: 0, total_weight_rejected: 0, ... }` (`stackerdb_listener.rs:693-704`).
2. Signer S (weight `w`) initially rejects `B` (e.g. stale view) → `BlockResponse::Rejected` received; `responded_signers.insert(S)` succeeds, `total_weight_rejected += w` (`stackerdb_listener.rs:515-518`).
3. Signer S re-evaluates (chainstate now confirms `B`) and signs it → `BlockResponse::Accepted` received; since `S` was never in `gathered_signatures`, `total_weight_approved += w` is added (`stackerdb_listener.rs:443-446`); `total_weight_rejected` is left at `w` — never decremented.
4. Repeat for enough signers so that the *stale* `total_weight_rejected` sum (which now double-counts flipped signers) exceeds `total_weight - weight_threshold`, while the *true* current rejecting weight is actually near zero.
5. `signer_coordinator.rs::get_block_status` evaluates `block_status.total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight` as true and returns `NakamotoNodeError::SignersRejected`, discarding a block that in reality has enough live approvals and no live blocking rejections — a liveness wedge for that tenure.

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

**File:** stacks-signer/src/signerdb.rs (L1929-1940)
```rust
        // If this signer/block already has a signature, do not allow a rejection
        let sig_qry = "SELECT EXISTS(SELECT 1 FROM block_signatures WHERE signer_signature_hash = ?1 AND signer_addr = ?2)";
        let sig_args = params![block_sighash, addr.to_string()];
        let exists = self.db.query_row(sig_qry, sig_args, |row| row.get(0))?;
        if exists {
            warn!("Cannot add block rejection because a signature already exists.";
                "signer_signature_hash" => %block_sighash,
                "signer_address" => %addr,
                "reject_reason" => ?reject_reason
            );
            return Ok(false);
        }
```

**File:** stacks-signer/CHANGELOG.md (L132-135)
```markdown
### Changed

- Do not count both a block acceptance and a block rejection for the same signer/block. Also ignore repeated responses (mainly for logging purposes).
- Database schema updated to version 16
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-540)
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
```
