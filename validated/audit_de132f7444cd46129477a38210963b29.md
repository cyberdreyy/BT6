### Title
Reject-then-Accept vote flip lets a single signer's weight be double-counted across `total_weight_approved` and `total_weight_rejected` - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener` tallies each signer's `BlockResponse` weight into either `total_weight_approved` or `total_weight_rejected`, gated by two *different* dedup sets. Because a rejection is never retracted when the same signer later accepts, a single one-slot signer flipping its vote can leave stale weight in `total_weight_rejected` while also being (correctly, but independently) added to `total_weight_approved`, breaking the implicit invariant that the two tallies are mutually exclusive and can wedge the miner's aggregation loop into a false `SignersRejected` outcome.

### Finding Description
In `handle_block_response` (stackerdb_listener.rs), the `Accepted` branch only guards double counting via `block.gathered_signatures.contains_key(&slot_id)`: [1](#0-0) 

while the `Rejected` branch guards via a separate, shared `block.responded_signers` set: [2](#0-1) 

Both branches insert into `responded_signers` (Accepted at line 465, Rejected at line 515), so an **Accept-then-Reject** flip is correctly suppressed (the second `responded_signers.insert(slot_id)` returns `false`, so no rejected weight is added). But the reverse order, **Reject-then-Accept**, is not: the Accepted branch never checks `responded_signers`, only `gathered_signatures`, which is untouched by a prior rejection. So:

1. Signer S (one StackerDB slot, its own key) sends `Rejected` for block B → `responded_signers.insert(S)` succeeds → `total_weight_rejected += weight(S)`.
2. S later sends `Accepted` for the same B (valid signature over the real sighash, no other signer or key involved) → `gathered_signatures.contains_key(S)` is `false` → `total_weight_approved += weight(S)`.
3. `total_weight_rejected` is never decremented — S's weight now counts in *both* tallies simultaneously, and the loop in `SignerCoordinator::get_block_status` evaluates the reject-abort condition before checking approval: [3](#0-2) 

Because `total_weight_rejected` carries stale weight that logically belongs to a signer who has since approved, the sum `total_weight_rejected + total_weight_approved` can exceed `total_weight`, and the `total_weight_rejected.saturating_add(weight_threshold) > total_weight` "blocking minority" check can trip earlier/incorrectly than it would if the stale weight were correctly cleared on flip. This directly parallels the analog bug class already fixed once at the `SignerDb` layer for the signer's own local state (see `stacks-signer/src/signerdb.rs` `reject_then_accept`/`accept_then_reject` tests, which enforce mutual exclusivity there): [4](#0-3) 
but the miner-side `StackerDBListener` tally (a separate, newer aggregation path) does not carry the same guarantee.

### Impact Explanation
This is a liveness wedge, not a forging of extra signature weight: `total_weight_approved` still only reflects real, verified signatures (`gathered_signatures`), so the miner cannot be tricked into shipping a block without a genuine 70% signature set. The consequence is that `total_weight_rejected` can be inflated with weight that no longer represents that signer's current position, causing the coordinator to prematurely conclude "70% approval is impossible" (`SignersRejected`) for a block that a single signer's earlier-but-superseded rejection is artificially blocking, even though the same signer has since accepted. This wedges the specific block-mining attempt and forces the miner to abandon and re-propose, degrading liveness triggerable by a single one-slot signer via ordinary StackerDB gossip (no majority, no other signer's key, no auth token needed).

### Likelihood Explanation
Trivially reachable: any registered signer can send two `BlockResponse` messages (Reject then Accept) for the same block over its own legitimate StackerDB slot — this requires no coordination with other signers and no special timing beyond normal reconsideration flows that the signer implementation already supports (see `should_reevaluate_reject_reason` / re-proposal flows in `stacks-signer/src/v0/signer.rs`, which legitimately allow a signer to reconsider and accept a block it previously rejected).

### Recommendation
Make the `Accepted` branch's dedup check consistent with the `Rejected` branch: before adding to `total_weight_approved`, check (and if necessary clear) any prior contribution recorded in `total_weight_rejected` for the same `slot_id` (and vice versa), or use a single per-slot "current vote" map instead of two independently-gated counters/sets, mirroring the mutual-exclusivity already enforced in `SignerDb::add_block_signature`/`add_block_rejection_signer_addr`.

### Proof of Concept
1. Start a `SignerCoordinator`/`StackerDBListener` tracking a block `B` with signer S at `slot_id = k`, `weight(S) = w`.
2. Have S publish `SignerMessageV0::BlockResponse(BlockResponse::Rejected(...))` for `B` → observe `total_weight_rejected == w`, `responded_signers == {k}`.
3. Have S publish `SignerMessageV0::BlockResponse(BlockResponse::Accepted(...))` for the same `B` with a valid signature → observe `total_weight_approved == w` while `total_weight_rejected` remains `w` (never reset), i.e. `total_weight_approved + total_weight_rejected == 2w`, violating the expected invariant that a signer contributes to at most one tally at a time.

Note: I was not able to execute this scenario in a live test harness (no filesystem/terminal access in this mode); the trace above is derived directly from reading the cited handler logic in `stackerdb_listener.rs`.

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

**File:** stacks-signer/src/signerdb.rs (L3265-3298)
```rust
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
