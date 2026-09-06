### Title
Node-side StackerDB tally lets a single signer double-count its weight in both `total_weight_approved` and `total_weight_rejected` for the same block - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListener`'s in-memory block tally, which the mining coordinator (`SignerCoordinator::get_block_status`) uses to decide whether a proposed block has crossed the 70% acceptance threshold or the >30% rejection threshold, does not enforce the same "accept XOR reject, per signer" invariant that the signer-side `SignerDb` explicitly enforces. A single signer that first rejects a block and later accepts it (or vice versa in the unprotected direction) has its weight added to `total_weight_approved` without ever being removed from `total_weight_rejected`, breaking the invariant that a signer's weight can only count toward one side of the tally.

### Finding Description
The stacks-signer's own local bookkeeping (`stacks-signer/src/signerdb.rs::add_block_rejection_signer_addr`) explicitly guards against this: it queries whether a signature already exists for that signer/block and refuses to record a rejection if so [1](#0-0) . This exact protection was called out as a fix in the CHANGELOG: "Do not count both a block acceptance and a block rejection for the same signer/block" [2](#0-1) , and is unit-tested in both directions (`reject_then_accept`, `accept_then_reject`) [3](#0-2) .

That same protection is **not** present in the node-side tally maintained by `StackerDBListener`, which the mining coordinator relies on to gate block push/rejection decisions. In the `Rejected` branch, the code guards on `responded_signers`: [4](#0-3) 

But in the `Accepted` branch, the guard checks a *different* set — `gathered_signatures` — rather than `responded_signers`: [5](#0-4) 

Concretely: if signer S first sends `BlockResponse::Rejected` for block B, `responded_signers.insert(slot_id)` succeeds and `total_weight_rejected` is incremented by S's weight [4](#0-3) . If S then sends `BlockResponse::Accepted` for the same B, `gathered_signatures.contains_key(&slot_id)` is false (S never accepted before), so the Accepted branch adds S's weight to `total_weight_approved` as well [6](#0-5) . Nothing ever decrements `total_weight_rejected`, and there is no check against `responded_signers`/prior rejection state before crediting the acceptance. The result: S's weight is now counted in *both* tallies simultaneously, so `total_weight_approved + total_weight_rejected` can exceed `self.total_weight` (an invariant that should hold if each signer's weight counts toward at most one side).

This is structurally the same bug class as the reported `opSAR` issue: a value is "corrected"/updated for one interpretation (sign flip / vote flip) without properly undoing the earlier state, producing an internally inconsistent result that differs from the semantically correct one (here, a signer's net vote should be exactly one bucket, not both).

### Impact Explanation
This directly matches the specified Critical impact category "a rejection recounted as an accept." The coordinator's threshold checks read `total_weight_approved`/`total_weight_rejected` directly from this shared struct (`get_block_status`), e.g. `status.total_weight_approved < self.weight_threshold` and `block_status.total_weight_approved >= self.weight_threshold` [7](#0-6) [8](#0-7) . Because a single signer's weight can be double-registered, the sum of the two tallies breaks its expected bound relative to `total_weight`, undermining the reliability of both the "approved" and "rejected" tallies the miner uses to decide whether to push a block or reset/retry. Depending on signer weight distribution, this can let a stale reject persist alongside a genuine later accept (or the reverse), producing tally values that no longer correspond to the true, current set of signer votes — the equality the coordinator relies on ("aggregated-weight equals verified, current per-signer accepts") is broken.

### Likelihood Explanation
Triggerable by a single signer/StackerDB slot (no majority required): the signer simply needs to broadcast a `Rejected` response followed later by an `Accepted` response for the same block hash — both are individually validly signed messages that pass signature verification [9](#0-8) . This can occur either through a buggy/reconsidering signer implementation (the v0 signer *does* legitimately reconsider some prior rejections and re-evaluate, per `should_reevaluate_reject_reason` / `should_reevaluate_block` in the docs) or a malicious signer intentionally sending both messages. No special access beyond normal StackerDB write privileges (which every registered signer already has) is needed.

### Recommendation
Mirror the signer-side fix in the node-side tally: before crediting a `BlockResponse::Accepted` to `total_weight_approved`, check whether the same `slot_id` already contributed to `total_weight_rejected` (via `responded_signers` or an explicit per-slot vote record) and, if so, either reject the switched vote or properly move the weight from the rejected bucket to the approved bucket (subtracting it from `total_weight_rejected` first). Use a single authoritative "did-this-signer-already-respond, and how" map instead of two independently-gated collections (`gathered_signatures` vs `responded_signers`) so that a signer's weight can only ever count toward one side of the tally at a time, matching the invariant already enforced in `stacks-signer/src/signerdb.rs::add_block_rejection_signer_addr`.

### Proof of Concept
1. Miner proposes block B and enters `SignerCoordinator::get_block_status`, awaiting signer responses.
2. Signer S (with weight W) sends a valid `BlockResponse::Rejected` for B. `StackerDBListener` records: `responded_signers.insert(S)` succeeds, `total_weight_rejected += W`.
3. Later, the same signer S sends a valid `BlockResponse::Accepted` for the same B (e.g., after reconsidering, or maliciously). `gathered_signatures.contains_key(S)` is `false` (first acceptance from S), so `total_weight_approved += W` is applied unconditionally, with no check against S's prior rejection.
4. Now `total_weight_approved + total_weight_rejected` includes S's weight `W` twice, violating the expected invariant that each signer contributes to exactly one bucket, and the tallies used by the coordinator's threshold checks no longer reflect the true, current set of signer votes.

### Citations

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

**File:** stacks-signer/src/signerdb.rs (L3234-3298)
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

    #[test]
    fn accept_then_reject() {
        let db_path = tmp_db_path();
        let db = SignerDb::new(db_path).expect("Failed to create signer db");

        let block_id = Sha512Trunc256Sum::from_data("foo".as_bytes());
        let address = StacksAddress::burn_address(false);
        let sig1 = MessageSignature([0x11; 65]);

        assert_eq!(db.get_block_signatures(&block_id).unwrap(), vec![]);

        assert!(db.add_block_signature(&block_id, &address, &sig1).unwrap());
        assert_eq!(
            db.get_block_signatures(&block_id).unwrap(),
            vec![sig1.clone()]
        );
        assert!(db
            .get_block_rejection_signer_addrs(&block_id)
            .unwrap()
            .is_empty());

        assert!(!db
            .add_block_rejection_signer_addr(
                &block_id,
                &address,
                RejectReasonPrefix::InvalidParentBlock
            )
            .unwrap());
        assert_eq!(db.get_block_signatures(&block_id).unwrap(), vec![sig1]);
        assert!(db
            .get_block_rejection_signer_addrs(&block_id)
            .unwrap()
            .is_empty());
    }
```

**File:** stacks-signer/CHANGELOG.md (L132-135)
```markdown
### Changed

- Do not count both a block acceptance and a block rejection for the same signer/block. Also ignore repeated responses (mainly for logging purposes).
- Database schema updated to version 16
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L411-426)
```rust
                        let Ok(valid_sig) = signer_pubkey.verify(block_sighash.bits(), &signature)
                        else {
                            warn!(
                                "StackerDBListener: Got invalid signature from a signer. Ignoring."
                            );
                            continue;
                        };
                        if !valid_sig {
                            warn!(
                                "StackerDBListener: Processed signature but didn't validate over the expected block. Ignoring";
                                "signature" => %signature,
                                "signer_signature_hash" => %block_sighash,
                                "slot_id" => slot_id,
                            );
                            continue;
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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L405-409)
```rust
                    if status.total_weight_rejected != rejections {
                        return false;
                    }
                    // enough signatures?
                    return status.total_weight_approved < self.weight_threshold;
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L541-545)
```rust
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```
