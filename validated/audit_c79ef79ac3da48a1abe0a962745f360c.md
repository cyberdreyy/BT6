### Title
Stale rejection weight is never cleared when a signer flips reject→accept in `StackerDBListener`, letting a single signer's weight be double-counted across both tallies - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener::run` maintains two independent counters per proposed block, `total_weight_rejected` and `total_weight_approved`, gated by two different membership checks (`responded_signers` for rejections, `gathered_signatures` for acceptances). When the same signer first rejects and later legitimately accepts the same block (a normal, sanctioned behavior in this codebase — see `should_reevaluate_reject_reason`), the acceptance path adds the signer's weight to `total_weight_approved` but nothing ever removes that signer's weight from `total_weight_rejected`. The signer's weight ends up counted in both piles simultaneously, which can make the "blocking minority" rejection condition spuriously true even though the signer's true, final vote is acceptance.

### Finding Description
In `stacks-node/src/nakamoto_node/stackerdb_listener.rs`, the `Rejected` branch guards its increment with: [1](#0-0) 
i.e. `if block.responded_signers.insert(slot_id) { total_weight_rejected += weight }`.

The `Accepted` branch, however, guards its increment against a *different* set: [2](#0-1) 
`if !block.gathered_signatures.contains_key(&slot_id) { total_weight_approved += weight }`, then unconditionally inserts into both `gathered_signatures` and `responded_signers`.

Consider a signer that first sends `BlockResponse::Rejected` for a block (e.g. it initially could not confirm the parent, or hit a since-resolved reject reason). This inserts its `slot_id` into `responded_signers` and adds its weight to `total_weight_rejected`. If it later legitimately re-evaluates and sends `BlockResponse::Accepted` for the *same* block (a supported, intended path in this codebase per `should_reevaluate_reject_reason`/"For some rejection reasons, a signer will reconsider a block proposal that it previously rejected"), the `Accepted` handler checks `gathered_signatures.contains_key(slot_id)` — which is still empty for this signer — so it enters the "not yet counted" branch and adds the same signer's weight to `total_weight_approved` too. Nothing in this path ever decrements `total_weight_rejected` or removes the signer from the rejection tally.

This is the direct analog of the reported bug class: two independently maintained aggregate quantities (`total_weight_rejected` and `total_weight_approved`) are supposed to reflect a partition of signer votes (each signer counted in at most one bucket), but the code allows a single signer's weight to leak into both buckets after a legitimate vote flip, breaking the "aggregated-weight vs verified-accepts" invariant that downstream consumers (`SignerCoordinator::get_block_status`) rely on.

Notably, `stacks-signer/src/signerdb.rs` implements the correct, symmetric behavior for the analogous in-signer tables (`add_block_signature` clears any prior rejection row for that signer/block — see the `reject_then_accept` test — and `add_block_rejection_signer_addr` refuses to record a rejection if a signature already exists — see the `accept_then_reject` test): [3](#0-2) [4](#0-3) 
The node-side `StackerDBListener`, which the mining coordinator (`SignerCoordinator`) relies on for its block-status decision, does not have this same mutual-exclusion guarantee.

### Impact Explanation
`SignerCoordinator::get_block_status` consumes these two counters and checks the rejection condition *before* the approval condition: [5](#0-4) 
If `total_weight_rejected.saturating_add(weight_threshold) > total_weight` is (spuriously) true due to a stale, uncleared rejection weight left behind by a signer who has since flipped to accept, the coordinator returns `NakamotoNodeError::SignersRejected` and abandons the block — even if the block in fact has (or would soon have) enough real, valid acceptances to cross the 70% threshold. This is a liveness impact: a miner can be wedged into discarding an otherwise-valid, sufficiently-signed block because of one signer's earlier, now-superseded rejection, forcing needless re-proposals/re-mining and potentially unwarranted transaction exclusion (`temporarily_excluded_txids`/`permanently_excluded_txids`) derived from the same stale rejection data. This does not require a majority of signers — a single flipping signer suffices to poison the shared tally used for the mining decision.

It does **not** break the ultimate on-chain safety of block acceptance, because the authoritative check (`NakamotoBlockHeader::verify_signer_signatures` in `stackslib/src/chainstate/nakamoto/mod.rs`) independently recomputes signing weight from the actual `signer_signature` vector on the finalized block using a `HashMap<PublicKey, (Signer, Index)>`, which cannot double-count a single public key. The impact is therefore confined to node/coordinator-level liveness rather than consensus-safety.

### Likelihood Explanation
This requires only a single signer (out of possibly many) to send a rejection and later a legitimate acceptance for the same block proposal — a normal path the codebase explicitly supports and tests for reconsideration of prior rejections (e.g. "For some rejection reasons, a signer will reconsider a block proposal that it previously rejected"). No majority collusion, no forged signatures, and no protocol-version-specific gating are needed; it can be triggered by ordinary signer behavior (e.g. a signer that initially rejects because its node hadn't processed the parent block yet, then accepts once it catches up) or by a rebooting/misbehaving signer replaying an old rejection after making up its mind to sign.

### Recommendation
In `StackerDBListener`'s `Accepted` handler, guard the `total_weight_approved` increment (and any per-txid rejection bookkeping tied to this signer) against `responded_signers` membership rather than (or in addition to) `gathered_signatures`, and when a signer's acceptance supersedes a prior rejection, subtract their weight from `total_weight_rejected` (and clean up any `failed_txids` weight entries attributable to that signer) at the same time it is added to `total_weight_approved`, mirroring the mutual-exclusion invariant already implemented in `stacks-signer/src/signerdb.rs::add_block_signature`/`add_block_rejection_signer_addr`.

### Proof of Concept
1. Node proposes block `B`; `StackerDBListener` tracks `BlockStatus { total_weight_approved: 0, total_weight_rejected: 0, responded_signers: {}, gathered_signatures: {} }` for `B`.
2. Signer `S` (weight `w`) sends `BlockResponse::Rejected(B)` (e.g. due to a transient state mismatch). The `Rejected` handler runs `responded_signers.insert(S)` → true, so `total_weight_rejected += w`.
3. Signer `S` subsequently re-evaluates and sends `BlockResponse::Accepted(B)` for the same block (a supported flow per `should_reevaluate_reject_reason`). The `Accepted` handler checks `gathered_signatures.contains_key(S)` → false (never inserted), so it proceeds: `total_weight_approved += w`, and inserts `S` into both `gathered_signatures` and `responded_signers` (already present).
4. Now `total_weight_rejected` still includes `w` from step 2 (never decremented) *and* `total_weight_approved` includes `w` from step 3. If other signers' rejections push `total_weight_rejected + weight_threshold > total_weight`, `SignerCoordinator::get_block_status` returns `SignersRejected` for block `B` even though `S` (and possibly enough other signers) have actually accepted it, causing the miner to spuriously discard a valid, adequately-signed block.

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

**File:** stacks-signer/src/signerdb.rs (L3234-3263)
```rust
    #[test]
    fn reject_then_accept() {
        let db_path = tmp_db_path();
        let db = SignerDb::new(db_path).expect("Failed to create signer db");

        let block_id = Sha512Trunc256Sum::from_data("foo".as_bytes());
        let address = StacksAddress::burn_address(false);
        let sig1 = MessageSignature([0x11; 65]);

        assert_eq!(db.get_block_signatures(&block_id).unwrap(), vec![]);

        assert!(db
            .add_block_rejection_signer_addr(
                &block_id,
                &address,
                RejectReasonPrefix::InvalidParentBlock
            )
            .unwrap());
        assert_eq!(
            db.get_block_rejection_signer_addrs(&block_id).unwrap(),
            vec![(address.clone(), RejectReasonPrefix::InvalidParentBlock)]
        );

        assert!(db.add_block_signature(&block_id, &address, &sig1).unwrap());
        assert_eq!(db.get_block_signatures(&block_id).unwrap(), vec![sig1]);
        assert!(db
            .get_block_rejection_signer_addrs(&block_id)
            .unwrap()
            .is_empty());
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
