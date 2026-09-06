### Title
Stale rejection weight is never revoked when a signer flips Reject→Accept, corrupting the miner's aggregated vote tally - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListener` tracks per-block `total_weight_approved` and `total_weight_rejected` using two independent membership sets (`gathered_signatures` and `responded_signers`) that are not kept consistent with each other. When a signer legitimately re-evaluates a block from rejection to acceptance (a transition the signer state machine explicitly allows), the node-side tally keeps the stale rejection weight forever, breaking the aggregated-weight-vs-verified-accepts equality the miner's coordinator relies on to decide whether a block is accepted or globally rejected.

### Finding Description
In `stackerdb_listener.rs`, the `Accepted` branch only guards double counting via `gathered_signatures`: [1](#0-0) 
and the `Rejected` branch only guards double counting via `responded_signers`: [2](#0-1) 

Because the acceptance path inserts into `responded_signers` too, and the rejection path checks `responded_signers` (not `gathered_signatures`), the two orderings are asymmetric:

- **Accept-then-Reject**: `responded_signers` already contains the slot from the Accept, so the later Reject's `responded_signers.insert(slot_id)` returns `false` and `total_weight_rejected` is correctly *not* incremented.
- **Reject-then-Accept**: the Reject only touches `responded_signers` (not `gathered_signatures`), so `total_weight_rejected` is incremented. When the Accept later arrives, `gathered_signatures` does not yet contain the slot, so `total_weight_approved` is *also* incremented — but `total_weight_rejected` is never decremented.

The result: after a signer flips from Reject to Accept for the same block, that signer's weight remains counted in **both** `total_weight_approved` and `total_weight_rejected` simultaneously. This directly corrupts the miner's `SignerCoordinator::get_block_status` loop, which evaluates the rejection condition before the acceptance condition: [3](#0-2) 

Vote flip-flopping (Reject → Accept) is not a hypothetical/malicious-only path — the v0 signer state machine explicitly documents and permits it: `LocallyRejected --> LocallyAccepted : re-evaluated`, per the block lifecycle description in `docs/signer-flows.md`: [4](#0-3) 
and is reachable via the `should_reevaluate_reject_reason`/`should_reevaluate_block` re-evaluation logic in the signer itself: [5](#0-4) 

Note that the signer-side `SignerDb` (`signerdb.rs`) correctly guards this scenario for its own local tallying (a rejection is blocked once a signature exists for the same signer/block, and adding a signature clears any prior rejection row, as shown by `add_block_rejection_signer_addr` and the `reject_then_accept`/`accept_then_reject` tests): [6](#0-5) [7](#0-6) 
But the node-side `stackerdb_listener.rs` implementation lacks the analogous cross-check, so the two aggregate counters can double-book a single signer's weight — exactly the "value counted twice because a deduction/state update on one side was never propagated to the other" pattern in the source H-3 report (fees paid once but counted again in `marketFunds`).

### Impact Explanation
`SignerCoordinator::get_block_status` checks the rejection condition (`total_weight_rejected + weight_threshold > total_weight`) before the acceptance condition (`total_weight_approved >= weight_threshold`): [3](#0-2) 
If stale rejection weight from a flipped signer pushes `total_weight_rejected` past the blocking-minority threshold, the miner declares `NakamotoNodeError::SignersRejected` and discards the block even though the true, current, verified set of accepting signers has actually reached (or would reach) the real 70% acceptance threshold. This is a liveness wedge: a block that legitimately has enough current signer support can never be finalized/pushed because the miner's own aggregated-weight bookkeeping is corrupted by an untracked vote flip. It breaks the "aggregated-weight vs verified-accepts" equality the coordinator is supposed to enforce, and is reachable by a single signer's ordinary (or intentionally crafted) message sequence over StackerDB gossip — no majority collusion required.

### Likelihood Explanation
Reject→Accept transitions are a documented, normal part of the v0 signer's re-evaluation flow (not merely a Byzantine action), so this can occur organically whenever a signer initially rejects a proposal (e.g., due to a transient validation failure or stale chainstate view) and then legitimately re-evaluates to acceptance after the proposal is re-sent. A single non-majority signer/gossip participant can also trigger it deliberately by sending a `Rejected` `BlockResponse` followed by an `Accepted` one for the same `signer_signature_hash`, without needing any additional signer's key or majority coordination.

### Recommendation
When processing an `Accepted` `BlockResponse` for a slot that is already present in `responded_signers` due to a prior `Rejected` message (i.e., not yet in `gathered_signatures`), the rejected weight previously added for that slot must be reversed (`total_weight_rejected -= signer_entry.weight`, and remove the slot from any per-txid `failed_txids` weight it contributed to) before/while adding the acceptance weight. Symmetrically, ensure the `Rejected` handler cannot leave `total_weight_approved` stale if a signer later moves from Accept back to Reject (the code should treat `gathered_signatures` and `responded_signers`/rejected weight as a single mutually-exclusive per-signer vote state rather than two independently-updated sets, mirroring the mutual-exclusion already implemented in `SignerDb::add_block_rejection_signer_addr`/`add_block_signature`).

### Proof of Concept
1. Node's `StackerDBListener` is tracking a block `B` with signer `S` (weight `w`).
2. `S` broadcasts `BlockResponse::Rejected(B)`. Handler executes `responded_signers.insert(S)` → true → `total_weight_rejected += w`. [2](#0-1) 
3. `S` re-evaluates (or an attacker crafts) and broadcasts `BlockResponse::Accepted(B)` with a valid signature. Handler checks `!gathered_signatures.contains_key(S)` → true (never touched) → `total_weight_approved += w`; `gathered_signatures.insert(S, sig)`; `responded_signers.insert(S)` (no-op, already present). [1](#0-0) 
4. Now `total_weight_rejected` still contains `w` from step 2, and `total_weight_approved` contains `w` from step 3 — `S`'s weight is counted on both sides simultaneously, with no code path ever decrementing `total_weight_rejected`.
5. In `SignerCoordinator::get_block_status`, if the resulting `total_weight_rejected` (inflated by the stale `w`) satisfies `total_weight_rejected.saturating_add(weight_threshold) > total_weight`, the miner returns `SignersRejected` and abandons block `B` even though the genuine, current accepting weight (which now legitimately includes `S`) may have already reached `weight_threshold`. [3](#0-2)

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

**File:** docs/signer-flows.md (L140-149)
```markdown
    Unprocessed --> PreCommitted : mark_pre_committed
    PreCommitted --> LocallyAccepted : mark_locally_accepted = WE SIGN
    Unprocessed --> LocallyRejected : mark_locally_rejected
    PreCommitted --> LocallyRejected : mark_locally_rejected
    LocallyRejected --> LocallyAccepted : re-evaluated
    LocallyAccepted --> LocallyRejected : re-evaluated
    LocallyAccepted --> GloballyAccepted : mark_globally_accepted
    LocallyRejected --> GloballyRejected : mark_globally_rejected
    GloballyAccepted --> [*]
    GloballyRejected --> [*]
```

**File:** stacks-signer/src/v0/signer.rs (L1505-1529)
```rust
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
