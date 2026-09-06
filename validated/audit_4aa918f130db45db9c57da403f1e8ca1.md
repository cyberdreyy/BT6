### Title
Node-side StackerDB aggregator double-counts a signer's weight in both accept and reject pools after a vote switch — ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener`'s per-block weight aggregator (used by `signer_coordinator.rs`'s `wait_for_block_acceptance` loop) gates the *accepted* path and the *rejected* path with two different membership sets. Once a signer has rejected a block, its weight is never retracted from `total_weight_rejected` if that same signer later sends an `Accepted` response for the same block. The signer's weight then appears in both `total_weight_approved` and `total_weight_rejected` simultaneously, breaking the aggregated-weight vs. verified-accepts equality the coordinator relies on to decide whether a block is globally accepted or rejected.

### Finding Description
The gate for adding rejection weight is `block.responded_signers.insert(slot_id)`, which returns `true` only the first time this slot is seen at all (accept or reject): [1](#0-0) 

The gate for adding approval weight is a *different* set, `block.gathered_signatures`, checked with `contains_key`: [2](#0-1) 

`responded_signers.insert(slot_id)` is also called in the accept branch, but only *after* the weight has already been added and only for later reject-suppression purposes: [3](#0-2) 

Walking the two orders:
- Accept → Reject: signer accepts first, `responded_signers` gets the slot id inserted at line 465. When the later `Rejected` message arrives, `responded_signers.insert(slot_id)` at line 515 returns `false` (already present), so `total_weight_rejected` is *not* incremented. This direction is correctly guarded.
- Reject → Accept: signer rejects first, `responded_signers.insert(slot_id)` at line 515 returns `true`, and `total_weight_rejected` is incremented. When the signer subsequently sends `Accepted` for the same block (a legitimate, expected path — signers are explicitly allowed to reconsider and switch their vote once a reject reason becomes stale, per the "reconsider a block proposal previously rejected" behavior documented in the CHANGELOG and implemented via `should_reevaluate_reject_reason`/`should_reevaluate_block` in `stacks-signer/src/v0/signer.rs`), the check at line 443 (`!block.gathered_signatures.contains_key(&slot_id)`) is `true` because `gathered_signatures` has never seen this slot, so `total_weight_approved` is *also* incremented. Nothing in the accept branch subtracts the earlier contribution to `total_weight_rejected`.

The result: this signer's weight is counted in both `total_weight_approved` and `total_weight_rejected` at the same time, for the rest of the block's lifetime in this aggregator instance.

This directly contradicts the mutual-exclusivity the signer's own local ledger enforces (`stacks-signer/src/signerdb.rs`), where switching from reject to accept explicitly clears the prior rejection record: [4](#0-3) 

The node-side aggregator in `stackerdb_listener.rs` has no equivalent invariant.

### Impact Explanation
`signer_coordinator.rs::wait_for_block_acceptance` (or its equivalent poll loop) checks the rejection condition before the acceptance condition: [5](#0-4) 

Because a signer who has switched reject→accept still contributes stale weight to `total_weight_rejected`, that stale weight can push `total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight` to true even though the same signer's current (and only valid) opinion is "accept." This can cause the coordinator to declare `SignersRejected` for a block that in fact has legitimate accept weight ≥ threshold, i.e., the aggregated weight diverges from the set of currently-valid accepts — exactly the "aggregated-weight vs verified-accepts" equality break called out as a Critical class in the analog rules. It can also, in the opposite direction, delay or corrupt the accept tally reported back into `gathered_signatures`, since a block whose rejection tally is inflated by stale weight may never converge to a clean accept decision even as a real 70% of current votes are "accept."

### Likelihood Explanation
This requires no majority, no other signer's key, and no privileged access — it is triggered purely by ordinary signer behavior over StackerDB gossip: a single signer rejecting a proposal for a reason that later becomes stale (e.g., a timing/validation race) and then legitimately switching to accept the same block, a path the signer software explicitly supports (`should_reevaluate_reject_reason` in `stacks-signer/src/v0/signer.rs`). Any observer of StackerDB (including the coordinating node itself) processing both messages in that order hits the bug deterministically.

### Recommendation
Use a single per-signer vote-state map (e.g., `HashMap<u32, Vote>` with `Accepted`/`Rejected` variants) instead of two independently-gated collections (`gathered_signatures` vs `responded_signers`/`total_weight_rejected`). On receiving a new vote for a slot that already has a recorded vote of the opposite kind, subtract the old contribution from the corresponding total before adding the new contribution to the other total — mirroring the mutual exclusivity already implemented in `stacks-signer/src/signerdb.rs`'s `add_block_signature`/`add_block_rejection_signer_addr`.

### Proof of Concept
1. Node's `StackerDBListener` is waiting on a block with `weight_threshold` requiring ~70% weight.
2. Signer S (weight W) sends `BlockResponse::Rejected` for block B. `responded_signers` gains S's slot; `total_weight_rejected += W`.
3. S later legitimately re-evaluates and sends `BlockResponse::Accepted` for the same B (its earlier reject reason became stale, an explicitly supported behavior). `gathered_signatures` does not yet contain S's slot, so `total_weight_approved += W` as well; `total_weight_rejected` is never decremented.
4. Now `total_weight_approved + total_weight_rejected` exceeds `self.total_weight` by W, and if enough other signers reject even for unrelated/legitimate reasons, `total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight` can become true purely due to S's stale double-counted weight, causing `SignersRejected` even though S's live, final vote was Accept.

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-446)
```rust
                        if !block.gathered_signatures.contains_key(&slot_id) {
                            block.total_weight_approved = block
                                .total_weight_approved
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L464-465)
```rust
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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L505-545)
```rust
                counters.set_miner_current_rejections_timeout_secs(rejections_timeout.as_secs());
                counters.set_miner_current_rejections(rejections);
            }

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
