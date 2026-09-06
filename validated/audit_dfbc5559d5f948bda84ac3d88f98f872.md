### Title
Stale rejection weight is never cleared when a signer flips from reject to accept, allowing a persistently-inflated rejection tally to block a properly-supported proposal - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener` tallies a per-signer weight into `BlockStatus.total_weight_rejected` when it sees a `BlockResponse::Rejected` and into `total_weight_approved` when it sees `BlockResponse::Accepted`, keyed only by `slot_id` presence in `responded_signers`/`gathered_signatures`. Unlike the signer-side `SignerDb`, which explicitly clears a signer's prior rejection record when that signer later signs the block (`add_block_signature`, proven by the `reject_then_accept` unit test), the coordinator-side `BlockStatus` never removes a signer's weight from `total_weight_rejected` once recorded, even after that same signer subsequently sends an `Accepted` for the identical `signer_signature_hash`.

### Finding Description
The signer state machine explicitly supports rejecting a block and later reconsidering it into an acceptance for the same proposal hash (documented in `docs/signer-flows.md` section 3: `LocallyRejected --> LocallyAccepted : re-evaluated`, gated by `should_reevaluate_reject_reason`). The signer-side ledger models this correctly: `SignerDb::add_block_signature` removes the address from the rejection set once it signs [1](#0-0) .

The node-side coordinator that aggregates the same responses for the purpose of gating block push does not mirror this behavior. In `handle` inside `stackerdb_listener.rs`:
- On `Accepted`, weight is added to `total_weight_approved` only if the slot is not already in `gathered_signatures`, and the slot is inserted into `responded_signers` [2](#0-1) .
- On `Rejected`, weight is added to `total_weight_rejected` only once per slot (guarded by `responded_signers.insert(slot_id)`), but nothing ever decrements `total_weight_rejected` for that slot [3](#0-2) .

Because `responded_signers` is a single set shared by both message kinds, a signer that first rejects (adds weight W to `total_weight_rejected`, inserts slot into `responded_signers`) and later accepts the very same proposal hash will pass the accept branch's guard (`!block.gathered_signatures.contains_key(&slot_id)` is still true, since only the reject branch touched `responded_signers`), so weight W is *also* added to `total_weight_approved`. The single signer's weight is now counted in both tallies simultaneously, and the stale rejection weight is never removed.

The `BlockStatus` entry for a given `signer_signature_hash` is created once per `propose_block` call (`self.stackerdb_comms.insert_block(&block.header)`) and is *not* reset across the internal resend loop that retries on `SignatureTimeout` [4](#0-3) ; `reset_rejections` is only invoked as a side effect of a timeout, not on every state transition, so weight recorded from an earlier round for a still-unresolved proposal persists.

### Impact Explanation
The consuming logic in `SignerCoordinator::get_block_status` checks rejection weight first and returns `Err(NakamotoNodeError::SignersRejected{..})` if `total_weight_rejected + weight_threshold > total_weight`, before checking `total_weight_approved >= weight_threshold` [5](#0-4) . Because a flipped signer's weight is never subtracted from `total_weight_rejected`, the coordinator can conclude a blocking-minority rejection using weight that no longer reflects that signer's current (accepting) vote, causing a proposal that has in fact reached legitimate 70% acceptance to instead be rejected/aborted (`SignersRejected`), or requiring the coordinator to falsely believe the reject threshold is closer than it is. This is a miscounted-response bug in the vote tally that the report's "rejection recounted as an acceptance"-class issue maps onto in mirror form (here: a stale rejection is recounted alongside a fresh acceptance from the same signer, corrupting the aggregate weight invariant `approved + rejected <= total_weight`). It degrades liveness of block propagation for that specific proposal/height rather than causing an invalid/non-canonical signature to be produced, since the underlying per-signer signature verification and signer-local bookkeeping (`SignerDb`) remain correct.

### Likelihood Explanation
This requires no majority collusion and no crypto/auth bypass — a single honest signer legitimately reconsidering a rejection (a codepath the protocol explicitly supports and tests, e.g. `should_reevaluate_reject_reason`/`stale_proposal_of_accepted_block_resends_acceptance`) is sufficient to leave the coordinator's `BlockStatus` in an inconsistent state for the remainder of that proposal's lifetime. Because normal operation (proposal resends, reconsideration of stale/outdated rejections) routinely triggers reject→accept transitions, the condition is readily reachable without any adversarial signer.

### Recommendation
When processing a `BlockResponse::Accepted` for a signer slot that is already present in `responded_signers` due to a prior `Rejected` message for the same `signer_signature_hash`, subtract that signer's weight from `total_weight_rejected` (and remove any associated `failed_txids` weight contributions) before adding it to `total_weight_approved`, mirroring the semantics already implemented in `SignerDb::add_block_signature`/`add_block_rejection_signer_addr` on the signer side. Alternatively, track per-slot "current vote" state instead of monotonically-accumulating counters, and recompute the aggregate weights from that per-slot map on each update.

### Proof of Concept
1. Miner proposes block B with `signer_signature_hash = H`; coordinator creates a fresh `BlockStatus` for `H`.
2. Signer S (weight W) initially rejects B with a re-evaluable reason (e.g. `ProposalTooOld`) → coordinator's `total_weight_rejected += W`, `responded_signers.insert(slot_S)`.
3. Miner resends the same proposal for B (still hash `H`) within the same `propose_block` call (no `insert_block` reset occurs on resend).
4. Signer S reconsiders (per `should_reevaluate_reject_reason`) and now sends `Accepted` for `H` → coordinator's guard `!gathered_signatures.contains_key(slot_S)` is true (S never appeared in `gathered_signatures`), so `total_weight_approved += W` as well.
5. Now `total_weight_rejected` still includes S's weight W (never decremented) while `total_weight_approved` also includes W: the two tallies overlap by W, breaking the invariant that a signer's weight should count toward at most one side, and can push `get_block_status`'s reject-threshold check (checked before the accept check) to falsely report `SignersRejected` even though a real 70% of distinct signer weight has accepted.

### Citations

**File:** stacks-signer/src/signerdb.rs (L3234-3264)
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
