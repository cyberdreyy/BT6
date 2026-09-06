### Title
Stale rejection weight is never cleared on vote-flip, letting the miner's node-side `BlockStatus` double-count a signer's weight and misfire `SignersRejected` ahead of a reached signing threshold - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListener::poll` maintains a per-block `BlockStatus` (`total_weight_approved`, `total_weight_rejected`, `gathered_signatures`, `responded_signers`) that the mining coordinator (`SigningCoordinator::get_block_status` in `stacks-node/src/nakamoto_node/signer_coordinator.rs`) polls to decide whether to keep waiting, give up (`SignersRejected`), or assemble a signature set. The guard that prevents double-adding a signer's weight is asymmetric between the `Accepted` and `Rejected` branches: `Accepted` guards against `gathered_signatures`, `Rejected` guards against `responded_signers`. A signer that legitimately rejects a proposal and later reconsiders and accepts it (a flow the signer state machine explicitly supports) gets its weight added to `total_weight_rejected` first and then, unconditionally, to `total_weight_approved` too, with nothing ever decrementing the stale `total_weight_rejected` contribution.

### Finding Description
In `stacks-node/src/nakamoto_node/stackerdb_listener.rs`:

- `Accepted` handling only adds weight if the slot is not already in `gathered_signatures`: [1](#0-0) 
- `Rejected` handling only adds weight if `responded_signers.insert(slot_id)` succeeds (i.e., first time this slot appears in `responded_signers` at all, whether via Accept or Reject): [2](#0-1) 

Walk through the two possible orderings for a single signer slot `X` with weight `w`:

1. **Accept, then Reject** (safe): `Accepted` adds `w` to `total_weight_approved` and inserts `X` into both `gathered_signatures` and `responded_signers`. A later `Rejected` from `X` fails `responded_signers.insert(X)` (already present), so no weight is added to `total_weight_rejected`. No double count.
2. **Reject, then Accept** (buggy): `Rejected` succeeds `responded_signers.insert(X)` and adds `w` to `total_weight_rejected`. A later `Accepted` from `X` checks only `gathered_signatures.contains_key(X)`, which is still `false` (nothing in the `Rejected` branch ever touches `gathered_signatures`), so it adds `w` to `total_weight_approved` as well. `X`'s weight `w` now sits in *both* `total_weight_rejected` and `total_weight_approved` simultaneously, and nothing in this file ever decrements `total_weight_rejected` when a signer's later message supersedes an earlier rejection.

This exact "flip-flop" is a normal, sanctioned part of the signer protocol: the signer explicitly reconsiders and overturns certain rejection reasons. The signer's own local bookkeeping (`SignerDb`) accounts for this correctly — `add_block_signature` removes a stale rejection row so `get_block_rejection_signer_addrs` returns empty after a later acceptance, as covered by the `reject_then_accept` unit test: [3](#0-2) 

But the node-side `BlockStatus` tracker used by the mining coordinator has no equivalent cleanup path, so it silently accumulates phantom rejection weight that the signer itself has already withdrawn.

The consumer of this state, `SigningCoordinator::get_block_status`, checks the rejection condition *before* the approval condition: [4](#0-3) 

So once enough stale+live rejection weight accumulates to cross `> total_weight - weight_threshold`, the coordinator returns `Err(SignersRejected{...})` and abandons the proposal — even in a state where the real, current votes (including the flipped signer's genuine acceptance) would have reached the 70% approval threshold, because `total_weight_rejected` is inflated by weight that no longer represents a live rejection.

### Impact Explanation
This breaks the equality the coordinator relies on: "rejected weight" is supposed to represent signers currently rejecting the block, mutually exclusive with "approved weight." The bug lets one signer's weight be counted in both buckets at once, so the sum of `total_weight_approved + total_weight_rejected` can exceed `total_weight`. Because the coordinator evaluates the rejection branch first, this can cause the miner to prematurely declare `SignersRejected` on a block that in fact has reached (or is about to reach) legitimate 70% signature weight, wedging that specific block proposal and forcing the miner to churn through exclusion/re-proposal cycles unnecessarily. This is a liveness degradation of the mining/coordinator path driven purely by a single signer's own (legitimate) vote-flip message, requiring no majority collusion and no other signer's key.

### Likelihood Explanation
The trigger condition — a signer rejecting a proposal and later reconsidering to accept it — is an explicitly supported, documented signer behavior (not an attack primitive), so it can occur in ordinary operation any time a rejection reason is reconsidered while the miner is still polling for that block's status. Only the flipping signer's own StackerDB message ordering is needed; no cross-signer coordination or privileged access is required.

### Recommendation
Make the weight accounting symmetric and idempotent per signer regardless of message order: track a single "final response" per slot (e.g., store the accepted/rejected state per `slot_id` instead of two independently-guarded weight counters) and, when a later message from the same slot supersedes an earlier one, decrement the previous bucket's weight before adding to the new bucket — mirroring the sticky-cleanup behavior already implemented in `SignerDb::add_block_signature`/`add_block_rejection_signer_addr`.

### Proof of Concept
1. Node starts polling `BlockStatus` for proposal `P` via `SigningCoordinator::get_block_status`.
2. Signer `X` (weight `w`) sends `BlockResponse::Rejected` for `P`. `stackerdb_listener.rs` inserts `X` into `responded_signers` and adds `w` to `total_weight_rejected`.
3. Signer `X` later reconsiders (per the signer's own `should_reevaluate_reject_reason`/reconsideration flow) and sends `BlockResponse::Accepted` for the same `P`. `stackerdb_listener.rs` checks only `gathered_signatures` (does not contain `X`), so it adds `w` again, now to `total_weight_approved`, and inserts `X` into `gathered_signatures`/`responded_signers` (no-op for the latter).
4. `total_weight_rejected` still includes `w` from step 2; `total_weight_approved` now also includes `w` from step 3 — `X`'s weight is counted in both totals with no code path ever removing it from `total_weight_rejected`.
5. If enough such flips (or a mix of stale flips and other genuine rejecters) push `total_weight_rejected.saturating_add(weight_threshold) > total_weight`, `SigningCoordinator::get_block_status` returns `Err(SignersRejected{...})` even though the real current vote set (including `X`'s and others' acceptances) may have already reached the 70% signing threshold, since the rejected-branch check runs first.

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
