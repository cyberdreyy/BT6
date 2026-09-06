### Title
Signer weight double-counted across reject-then-accept flip inflates both approval and rejection tallies - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
The node-side `StackerDBListener` that tallies signer `BlockResponse` votes for a proposed block guards against double-counting a *repeated* vote of the same kind, but not against a *flip* from `Rejected` to `Accepted` by the same signer. A signer that rejects a block and later re-considers and accepts it (which the signer-side state machine explicitly allows, e.g. once a conflicting sibling goes stale) has its weight added to `total_weight_approved` without ever being removed from `total_weight_rejected`. This breaks the aggregated-weight-vs-verified-accepts equality the node relies on to decide whether a proposal has been accepted or rejected by the signer set.

### Finding Description
`BlockStatus` tracks two independent counters, `total_weight_approved` and `total_weight_rejected`, plus a `responded_signers: HashSet<u32>` and `gathered_signatures: BTreeMap<u32, MessageSignature>`. [1](#0-0) 

On `BlockResponse::Accepted`, weight is added to `total_weight_approved` guarded only by `gathered_signatures.contains_key(&slot_id)`: [2](#0-1) 

On `BlockResponse::Rejected`, weight is added to `total_weight_rejected` guarded by `responded_signers.insert(slot_id)`, and the accept path also inserts into `responded_signers`, which correctly prevents a *reject-after-accept* flip from being double counted (insert returns false since the slot id is already present): [3](#0-2) 

However, the reverse direction is not guarded: the accept branch never checks `responded_signers` before adding to `total_weight_approved`, and there is no code anywhere in this handler (nor in `signer_coordinator.rs`'s consumer of `BlockStatus`) that subtracts a signer's earlier rejected weight from `total_weight_rejected` when that same signer later accepts. So a signer that rejects, then later accepts the same block (a legitimate, signer-authorized transition — see `stacks-signer/src/v0/signer.rs`'s pre-commit/sibling-conflict logic where a signer may "refuse to sign for now" and sign later once a conflict goes stale) ends up counted in *both* buckets simultaneously: once in `total_weight_rejected` (from the earlier vote) and once in `total_weight_approved` (from the later vote).

By contrast, the equivalent logic on the signer's own local `SignerDb` explicitly clears a stale rejection when a signature for the same block/signer is later recorded, demonstrating that the two directions are intentionally meant to be symmetric elsewhere in the codebase: [4](#0-3) 

This is directly analogous to the external report's root cause: most transfer call-sites were updated to use the safe pattern, but a few (`removeLiquidity`, `rescue`, `skim`) were left on the old, unguarded pattern, producing an inconsistency that breaks an invariant the rest of the code assumes holds everywhere. Here, the `Rejected`-guard-before-`Accepted`-add direction was hardened (rejecting after accepting is a no-op), but the symmetric case — accepting after rejecting — was left unguarded, silently violating the "weight per signer is single-counted" invariant the coordinator's threshold math depends on.

### Impact Explanation
`signer_coordinator.rs::get_block_status` consumes exactly these two counters to decide the miner's outcome for a proposal: [5](#0-4) 

Because a flipped signer's weight is simultaneously counted in `total_weight_rejected` and `total_weight_approved`, `total_weight_rejected + total_weight_approved` can exceed `total_weight` — a state the threshold arithmetic (`total_weight_rejected.saturating_add(weight_threshold) > total_weight`) never anticipates once real per-signer weight should be mutually exclusive. This is the "aggregated-weight vs verified-accepts" equality the miner's decision logic assumes holds. The consequence is a genuine safety/liveness defect: the node can declare `SignersRejected` (with real transaction-exclusion side effects via `temporarily_excluded_txids`/`permanently_excluded_txids`) purely because of stale rejection weight left over from a signer who has since flipped to accepting — even while the real, current signer set has legitimately reached the 70% approval threshold. This is a liveness wedge on block production driven purely by inflated/miscounted aggregated weight, not by any real supermajority disagreement.

### Likelihood Explanation
Any single signer (one StackerDB slot, its own key only — no majority collusion required) can trigger this by rejecting a proposal and later accepting it. This is not an attack-only path; it is a state transition the signer software itself performs as designed (the pre-commit/conflict-resolution logic in `stacks-signer/src/v0/signer.rs` explicitly reject-then-later-signs the same block once a conflict goes stale, and the "sibling" test scenarios `stale_sibling_replaced_when_canonical_tip_below` etc. in `stacks-signer/src/v0/tests.rs` demonstrate this flip happens in normal operation). So the bug is reachable purely through gossip-visible, legitimately-signed messages from one signer, without needing majority collusion or key compromise.

### Recommendation
When processing `BlockResponse::Accepted`, check whether the signer's slot id was previously counted in `total_weight_rejected` (e.g. by tracking which bucket a `responded_signers` entry belongs to, or storing per-slot vote kind) and, if so, subtract that signer's weight from `total_weight_rejected` before adding it to `total_weight_approved`, mirroring the mutual-exclusivity guarantee that `SignerDb::add_block_signature`/`add_block_rejection_signer_addr` already implement on the signer side.

### Proof of Concept
1. Node proposes block `B` to `N` signers with total weight `W` and threshold `weight_threshold = ceil(0.7 * W)`.
2. Signer `S` (weight `w`) sends `BlockResponse::Rejected` for `B`. `stackerdb_listener.rs` sets `total_weight_rejected += w` and `responded_signers.insert(slot_id_S)`.
3. Later, the conflict/sibling situation that made `S` reject resolves (e.g. the sibling block times out per `stale_sibling_replaced_when_canonical_tip_below`), and `S`'s own signer logic legitimately signs and broadcasts `BlockResponse::Accepted` for `B`.
4. `stackerdb_listener.rs` checks only `gathered_signatures.contains_key(slot_id_S)` (false, since `S` never accepted before) and adds `total_weight_approved += w`, without touching `total_weight_rejected`.
5. Now `total_weight_rejected` and `total_weight_approved` both include `w`, so `total_weight_rejected + total_weight_approved` can exceed `W`. If enough other signers are still near the rejection boundary, `total_weight_rejected.saturating_add(weight_threshold) > total_weight` can hold (using stale rejection weight from `S`) at the same moment `total_weight_approved >= weight_threshold` would also hold — the coordinator's `if/else if` ordering in `get_block_status` (lines 509-541) evaluates the rejection branch first, so the miner treats the proposal as rejected and excludes transactions, even though the true, current signer set has reached real approval consensus.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L486-518)
```rust
                    SignerMessageV0::BlockResponse(BlockResponse::Rejected(rejected_data)) => {
                        let (lock, cvar) = &*self.blocks;
                        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");

                        let Some(block) = blocks.get_mut(&rejected_data.signer_signature_hash)
                        else {
                            info!(
                                "StackerDBListener: Received rejection for block that we did not request. Ignoring.";
                                "signer_signature_hash" => %rejected_data.signer_signature_hash,
                                "slot_id" => slot_id,
                                "signer_set" => self.signer_set,
                            );
                            continue;
                        };

                        let rejected_pubkey = match rejected_data.recover_public_key() {
                            Ok(rejected_pubkey) => {
                                if rejected_pubkey != signer_pubkey {
                                    warn!("StackerDBListener: Recovered public key from rejected data does not match signer's public key. Ignoring.");
                                    continue;
                                }
                                rejected_pubkey
                            }
                            Err(e) => {
                                warn!("StackerDBListener: Failed to recover public key from rejected data: {e:?}. Ignoring.");
                                continue;
                            }
                        };

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
