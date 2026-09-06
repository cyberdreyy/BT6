### Title
Stale rejection weight not decremented when a signer flips a vote from Reject to Accept for the same block, causing the miner's signer coordinator to miscount consensus - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`BlockStatus` in the miner-side StackerDB listener tracks `total_weight_approved` and `total_weight_rejected` as two independent, additive counters per block sighash. When a signer that has already rejected a block later re-evaluates the same proposal (same `signer_signature_hash`) and accepts it, its weight is correctly added to `total_weight_approved`, but its previously-recorded weight in `total_weight_rejected` is never removed. This is the same class of bug as the external report: a "withdrawn"/superseded contribution is added to a new bucket without being subtracted from the old one, corrupting the aggregate that downstream logic treats as authoritative.

### Finding Description
`BlockStatus` holds two independently-accumulated weights: [1](#0-0) 

On `Accepted`, weight is added keyed only on whether the slot is already in `gathered_signatures` (i.e., whether this signer already signed): [2](#0-1) 

On `Rejected`, weight is added keyed on `responded_signers.insert(slot_id)`: [3](#0-2) 

If a signer's `Rejected` message arrives first (adding to `total_weight_rejected` and inserting `slot_id` into `responded_signers`), and then, for the *same* `signer_signature_hash`, that signer later sends `Accepted` (a legitimate re-evaluation flow — see below), the `Accepted` branch only checks `gathered_signatures`, which is still empty for that signer, so it adds the weight to `total_weight_approved` too. Nothing in either branch subtracts the signer's earlier contribution from `total_weight_rejected`. The signer's weight now double-counts across both buckets.

This flip is a real, protocol-supported path, not a hypothetical: the signer explicitly re-evaluates a block it previously rejected when the reject reason is one of the "re-evaluable" reasons, and it can end up producing an `Accepted` response for the identical sighash on reproposal: [4](#0-3) [5](#0-4) 

The signer's own local `SignerDb`, by contrast, correctly enforces mutual exclusion between the two tallies: adding a signature deletes any prior rejection row, and adding a rejection is refused if a signature already exists: [6](#0-5) [7](#0-6) 

The miner-side `BlockStatus` tracker in `stackerdb_listener.rs` has no equivalent guard, so it diverges from the signer's own ground truth: the signer's DB has removed the stale rejection, but the miner's in-memory tally still counts it.

### Impact Explanation
`SigningCoordinator::get_block_status` uses `total_weight_rejected` and `total_weight_approved` from this same shared `BlockStatus` to decide the outcome of a proposal round, checking the rejection-crosses-30% condition before the acceptance-crosses-70% condition: [8](#0-7) 

Because `total_weight_rejected` can retain weight from a signer that has since flipped to acceptance, an aggregate that should legitimately clear the 70% acceptance threshold can instead spuriously cross the 30% blocking-rejection threshold first (or take longer than it should to clear acceptance, depending on timing of arrival), causing the coordinator to conclude the block was rejected when the current, up-to-date signer set actually accepts it. This uses the rejection branch to permanently exclude transactions it believes are "genuinely problematic": [9](#0-8) 

This is a liveness/miscounted-response bug: a stale, superseded vote is never retracted from the aggregate, so the coordinator's decision no longer reflects the true, current state of signer opinion — directly analogous to the reported bug where `withdrawnStakingEarnings`/`withdrawnFeeEarnings` were never reduced, corrupting subsequent share computations.

### Likelihood Explanation
This requires only a single signer (one StackerDB slot) reconsidering and flipping its vote on the exact same block proposal (same `signer_signature_hash`) — a state transition the signer protocol explicitly supports via `should_reevaluate_reject_reason` for reasons such as `NotFoundError`, `UnknownParent`, `ConnectivityIssues`, `TestingDirective`, `NoSignerConsensus`, etc. No majority collusion, no other signer's key, and no privileged access is needed — a miner/relayer simply needs to reproposal the same block content after a transient rejection condition clears, which the codebase itself tests for (e.g. reproposal with the same sighash): [10](#0-9) 

### Recommendation
In `stacks-node/src/nakamoto_node/stackerdb_listener.rs`, make `total_weight_approved` and `total_weight_rejected` mutually exclusive per signer slot, mirroring the guarantee already implemented in `stacks-signer/src/signerdb.rs`:
- Before adding a signer's weight to `total_weight_approved` on `Accepted`, check whether that slot previously contributed to `total_weight_rejected` and, if so, subtract its weight from `total_weight_rejected` (and vice versa for `Rejected` after a prior `Accepted`, defensively).
- Alternatively, track a single `HashMap<slot_id, Vote>` (Accepted/Rejected + weight) and recompute `total_weight_approved`/`total_weight_rejected` by summing over that map, so a flip only ever contributes once, to the currently-recorded side.

### Proof of Concept
1. Signer `S` (slot `k`, weight `w`) receives a block proposal and rejects it for a re-evaluable reason (e.g. `NotFoundError`) → `stackerdb_listener` records `total_weight_rejected += w`, `responded_signers.insert(k)`.
2. The condition causing the rejection clears; the miner reproposes the identical block (same `signer_signature_hash`).
3. `S` re-evaluates per `should_reevaluate_reject_reason` (returns `true` for `NotFoundError`), revalidates, and this time signs, broadcasting `Accepted`.
4. `stackerdb_listener`'s `Accepted` handler checks only `gathered_signatures.contains_key(&k)` (false, since `S` never signed before) and adds `total_weight_approved += w`, but never decrements `total_weight_rejected`.
5. `BlockStatus` for this sighash now shows `w` counted in both `total_weight_approved` and `total_weight_rejected` simultaneously, inflating both tallies used by `SigningCoordinator::get_block_status`'s decision logic.

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

**File:** stacks-signer/src/v0/signer.rs (L1481-1531)
```rust
    /// Determine if an already tracked block should be re-evaluated based on a new block proposal for it.
    /// Returns true if the block should be re-evaluated, false if it should be ignored.
    fn should_reevaluate_block(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        block_info: &BlockInfo,
        block_proposal: &BlockProposal,
    ) -> bool {
        let signer_signature_hash = block_info.block.header.signer_signature_hash();
        if block_info.globally_approved_and_responded() {
            info!("{self}: received a block proposal for a globally accepted block to which we have already responded. Ignoring.";
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_info.block.block_id(),
                "block_height" => block_info.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "timestamp" => block_info.block.header.timestamp,
                "signed_group" => block_info.signed_group,
                "signed_self" => block_info.signed_self,
                "valid" => ?block_info.valid
            );
            return false;
        }
        if !should_reevaluate_reject_reason(block_info) {
            if block_info.state == BlockState::PreCommitted {
                // We validated this block but haven't signed it. Signing requires the
                // pre-commit threshold and the conflict checks in `handle_block_pre_commit`.
                // Re-broadcast our pre-commit and re-run that evaluation instead of
                // responding with a signature directly, so a re-proposed block can't
                // bypass those checks.
                info!(
                    "{self}: received a block proposal for a block we have pre-committed to but not signed. Re-evaluating the pre-commit.";
                    "signer_signature_hash" => %signer_signature_hash,
                    "block_id" => %block_info.block.block_id(),
                    "block_height" => block_info.block.header.chain_length,
                    "burn_height" => block_proposal.burn_height,
                    "consensus_hash" => %block_info.block.header.consensus_hash
                );
                self.send_block_pre_commit(signer_signature_hash.clone());
                let address = self.stacks_address.clone();
                self.handle_block_pre_commit(
                    stacks_client,
                    sortition_state,
                    &address,
                    &signer_signature_hash,
                );
                return false;
            }
            if let Some(block_response) = self.determine_response(block_info) {
                self.send_block_response(&block_info.block, block_response);
```

**File:** stacks-signer/src/v0/signer.rs (L2705-2739)
```rust
/// Determine if a block should be re-evaluated based on its rejection reason˝
fn should_reevaluate_reject_reason(block_info: &BlockInfo) -> bool {
    if let Some(reject_reason) = &block_info.reject_reason {
        match reject_reason {
            RejectReason::ValidationFailed(ValidateRejectCode::UnknownParent)
            | RejectReason::ValidationFailed(ValidateRejectCode::NotFoundError)
            | RejectReason::NoSortitionView
            | RejectReason::ConnectivityIssues(_)
            | RejectReason::TestingDirective
            | RejectReason::InvalidTenureExtend
            | RejectReason::ConsensusHashMismatch { .. }
            | RejectReason::NoSignerConsensus
            | RejectReason::NotRejected
            | RejectReason::Unknown(_) => true,
            RejectReason::ValidationFailed(_)
            | RejectReason::RejectedInPriorRound
            | RejectReason::SortitionViewMismatch
            | RejectReason::ReorgNotAllowed
            | RejectReason::InvalidBitvec
            | RejectReason::PubkeyHashMismatch
            | RejectReason::InvalidMiner
            | RejectReason::NotLatestSortitionWinner
            | RejectReason::InvalidParentBlock
            | RejectReason::DuplicateBlockFound
            | RejectReason::IrrecoverablePubkeyHash
            | RejectReason::ProblematicTransactions
            | RejectReason::ProposalTooOld => {
                // No need to re-validate these types of rejections.
                false
            }
        }
    } else {
        false
    }
}
```

**File:** stacks-signer/src/signerdb.rs (L1870-1905)
```rust
    /// Record an observed block signature
    pub fn add_block_signature(
        &self,
        block_sighash: &Sha512Trunc256Sum,
        signer_addr: &StacksAddress,
        signature: &MessageSignature,
    ) -> Result<bool, DBError> {
        // Remove any block rejection entry for this signer and block hash
        let del_qry = "DELETE FROM block_rejection_signer_addrs WHERE signer_signature_hash = ?1 AND signer_addr = ?2";
        let del_args = params![block_sighash, signer_addr.to_string()];
        self.db.execute(del_qry, del_args)?;

        // Insert the block signature
        let qry = "INSERT OR IGNORE INTO block_signatures (signer_signature_hash, signer_addr, signature) VALUES (?1, ?2, ?3);";
        let args = params![
            block_sighash,
            signer_addr.to_string(),
            serde_json::to_string(signature).map_err(DBError::SerializationError)?
        ];
        let rows_added = self.db.execute(qry, args)?;

        let is_new_signature = rows_added > 0;
        if is_new_signature {
            debug!("Added block signature.";
                "signer_signature_hash" => %block_sighash,
                "signer_address" => %signer_addr,
                "signature" => %signature
            );
        } else {
            debug!("Duplicate block signature.";
                "signer_signature_hash" => %block_sighash,
                "signer_address" => %signer_addr,
                "signature" => %signature
            );
        }
        Ok(is_new_signature)
```

**File:** stacks-signer/src/signerdb.rs (L1922-1941)
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

**File:** stacks-node/src/tests/signer/v0/missing_burn_block_proposal.rs (L64-68)
```rust
///   `ValidationFailed(NotFoundError)`.
/// - Upon reproposal, the block is fully revalidated and rejected again
///   with the same error.
/// - The rejection is treated as re-evaluable rather than terminal.
fn signer_reevaluates_proposal_with_missing_burn_view() {
```
