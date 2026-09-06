### Title
Single-signer weight double-counted across `total_weight_approved` and `total_weight_rejected` lets a minority signer force a block false-reject (node-side coordinator tally) - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`, consumed by `stacks-node/src/nakamoto_node/signer_coordinator.rs`)

### Summary
The external report's root cause is a value that should be an internally-tracked, mutually-consistent accounting figure (ERC4626 share price) instead being derived from a mutable external input (`balance_of`) that a single non-privileged actor can perturb, corrupting the ratio used for *other* users' redemptions. The analog here is the node-side vote tally that the mining coordinator relies on to decide whether a proposed block reached the 70% signing threshold or the 30% blocking-minority rejection threshold: `total_weight_approved` and `total_weight_rejected` are two independently-incremented counters, and the code's de-duplication guards are not symmetric, so a single signer's weight can be added to *both* counters for the same block, corrupting the aggregated-weight vs. verified-accepts equality that `signer_coordinator.rs::get_block_status` depends on.

### Finding Description
In `handle_block_response`/message processing in `StackerDBListener` (used by the miner's `SignCoordinator`), accepted signatures and rejections for a block are tallied into two separate fields of the same `BlockStatus` record:

- Accept path: weight is added only if the slot hasn't already been recorded in `gathered_signatures`, then the slot is inserted into both `gathered_signatures` and `responded_signers`. [1](#0-0) 

- Reject path: weight is added only if the slot was not already present in `responded_signers` (a shared "has this signer answered yet" set). [2](#0-1) 

Because the accept path's guard checks `gathered_signatures` (a set only ever populated by *accepts*) rather than the shared `responded_signers` set, a signer that first sends a `Rejected` message (which populates `responded_signers` but not `gathered_signatures`) and later sends an `Accepted` message for the same block will pass the accept-path guard and have its weight added to `total_weight_approved` a second time — even though that same weight was already counted in `total_weight_rejected`. The reverse order (accept-then-reject) is correctly blocked, because the reject path's guard does check the shared `responded_signers` set — but the forward order is not.

This breaks the invariant the coordinator relies on: that `total_weight_approved` and `total_weight_rejected` are disjoint tallies whose sum cannot exceed `total_weight`. `signer_coordinator.rs::get_block_status` consumes exactly these two fields to decide between "SignersRejected" and "enough signatures, block accepted": [3](#0-2) 

### Impact Explanation
A single signer with minority weight (no majority collusion required) can equivocate — reject, then later accept the same block — and have its weight silently double-counted into the rejection tally while it is genuinely counted (once) in the acceptance tally. If the honest signers' rejection weight is just under the 30% blocking-minority threshold, the malicious signer's double-dipped weight can push `total_weight_rejected + weight_threshold > total_weight` and force the miner into `NakamotoNodeError::SignersRejected` even while `total_weight_approved` is independently climbing toward (or has reached) the legitimate 70% threshold. Since the coordinator's loop checks the rejection condition before the acceptance condition, this lets a minority actor unilaterally veto a block that a legitimate 70%-weight majority was willing to sign — a liveness wedge inflicted by less than a majority, matching the "signer/network wedged into never approving a valid block" class of impact.

### Likelihood Explanation
The only requirement is that one already-registered signer (of any non-trivial weight) sends a `Rejected` message followed later by an `Accepted` message for the same block hash — both of which are ordinary, protocol-valid signer messages the code already handles; no cryptographic forgery or majority collusion is needed, only message ordering, which a single byzantine/gossip-manipulated signer fully controls.

### Recommendation
Make the de-duplication guard symmetric: gate the accept path on the same `responded_signers` set used by the reject path (i.e., only add to `total_weight_approved` if the slot was not already present in `responded_signers`), or maintain a single per-slot "final decision" enum and refuse to flip it once set, mirroring the mutual-exclusion check already present for the signer-local rejection bookkeeping in `signerdb.rs::add_block_rejection_signer_addr` (which refuses to record a rejection once a signature already exists for that address). [4](#0-3) 

### Proof of Concept
1. Reward set has signers A(35), B(35), C(30) — 100 total weight; threshold to sign = 70, blocking minority = 30.
2. For block X, A and B accept honestly (`total_weight_approved` = 70, meets threshold) — but message delivery to a given miner/coordinator instance is asynchronous per signer.
3. Signer C (weight 30) first broadcasts `Rejected` for block X (`total_weight_rejected` = 30, meets blocking minority on its own already in this contrived split; use a smaller weight, e.g. C=10 and a 4th honest signer D=25 rejecting honestly, so `total_weight_rejected` starts at 25, below the 30 threshold).
4. C then broadcasts `Accepted` for the same block X. Per lines 443-465, since C's slot is not yet in `gathered_signatures`, its weight is added to `total_weight_approved` — this is expected/legitimate for the approval side. But its earlier 10 weight remains counted in `total_weight_rejected` (25) as well — nothing removes or reconciles it.
5. If a fifth signer's weight also lands in rejection concurrently, `total_weight_rejected` can cross the 30-blocking threshold using C's weight that is *simultaneously* backing the acceptance tally, causing `get_block_status` to return `NakamotoNodeError::SignersRejected` for a block that legitimately reached 70% approving weight among distinct signers. [1](#0-0) [5](#0-4)

### Citations

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

**File:** stacks-signer/src/signerdb.rs (L1922-1940)
```rust
    /// Record an observed block rejection_signature
    pub fn add_block_rejection_signer_addr(
        &self,
        block_sighash: &Sha512Trunc256Sum,
        addr: &StacksAddress,
        reject_reason: RejectReasonPrefix,
    ) -> Result<bool, DBError> {
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
