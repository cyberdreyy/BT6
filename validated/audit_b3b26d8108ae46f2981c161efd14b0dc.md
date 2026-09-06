### Title
Reject-then-Accept Sequence Lets a Single Signer Be Double-Counted in Both the Rejection and Approval Weight Tallies - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
The oracle bug ("duplicate signatures let one signer inflate `valid_signer_counter`") has a direct analog in the node-side signer-response tally kept by `StackerDBListener`. `BlockStatus` tracks `total_weight_approved` and `total_weight_rejected` as two independent, monotonically-increasing counters. The guard that prevents double-counting a slot's weight is implemented differently, and asymmetrically, for the two counters: the *accept* path gates on `gathered_signatures.contains_key(&slot_id)`, while the *reject* path gates on `responded_signers.insert(slot_id)`. Because `responded_signers` is shared and updated by *both* paths, a signer who first sends a `Rejected` message and later sends an `Accepted` message for the exact same block gets its weight added to `total_weight_rejected` **and** to `total_weight_approved` — the same slot's weight is counted in both pools, breaking the intended equality that a signer's weight can back only one side of the vote.

### Finding Description
`BlockStatus` (`stacks-node/src/nakamoto_node/stackerdb_listener.rs:70-82`) holds: [1](#0-0) 

On `BlockResponse::Accepted`, weight is added only if the slot is not already in `gathered_signatures`, and then `responded_signers` and `gathered_signatures` are both updated: [2](#0-1) 

On `BlockResponse::Rejected`, weight is added only if `responded_signers.insert(slot_id)` succeeds (i.e., the slot has not previously "responded" at all): [3](#0-2) 

Walking through the two possible orderings for the same `slot_id`:

- **Accept → Reject**: Accept sets `responded_signers.insert(slot_id)` (line 465). The later Reject calls `responded_signers.insert(slot_id)` which now returns `false` since it's already present, so the reject branch is correctly skipped — no double count.
- **Reject → Accept**: Reject sets `responded_signers.insert(slot_id)` (returns `true` the first time) and adds to `total_weight_rejected`. The later Accept checks `gathered_signatures.contains_key(&slot_id)`, which is still `false` (nothing was ever inserted into `gathered_signatures` by the reject path), so the accept branch adds the same slot's weight to `total_weight_approved` as well.

This means a single signer that emits a `Rejected` response and then an `Accepted` response for the identical `signer_signature_hash` has its weight double-booked into two supposedly mutually exclusive tallies — exactly the "single signer bypasses/duplicates its contribution to a threshold decision" bug class from the oracle report, just split across two counters instead of one.

Notably, the equivalent client-side data structure in `stacks-signer/src/signerdb.rs` was explicitly hardened against this (see the `reject_then_accept` test, which asserts that adding a signature clears any prior rejection record for that signer address), and the project's own CHANGELOG documents the fix: "Do not count both a block acceptance and a block rejection for the same signer/block." [4](#0-3) [5](#0-4) 

That fix was applied to the signer's own `SignerDb`, but the parallel bookkeeping in the node-side `StackerDBListener::BlockStatus` (used by the mining coordinator to decide whether to accept/reject a proposed block) was not given the same treatment — there is no code path in the reject/accept handlers of `stackerdb_listener.rs` that subtracts previously-counted weight or that gates both paths on the same guard.

### Impact Explanation
The coordinator (`stacks-node/src/nakamoto_node/signer_coordinator.rs`) consumes these two counters to decide the fate of a block, checking the rejection condition first: [6](#0-5) 

Because `total_weight_rejected` can include weight from signers who later also (genuinely) accepted, the "blocking minority" check (`total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight`) can fire using inflated/non-independent weight. This can cause the coordinator to erroneously declare a legitimately-signable block as `SignersRejected`, discard it, and even mark transactions as `temporarily_excluded_txids`/`permanently_excluded_txids` based on a fabricated rejection majority that doesn't correspond to genuinely-rejecting, independent signers. This is a liveness/safety break of the "aggregated weight vs. verified accepts" equality the threshold logic is supposed to enforce, and it is reachable by any single signer simply emitting a reject message and then an accept message for the same block over its own StackerDB slot — no collusion or majority control is required to trigger the underlying double-count, only enough weight (same amount normally needed to matter for the 30% blocking minority) to make it consequential.

### Likelihood Explanation
Triggering this only requires a single signer (compromised, or one under attacker control) to push two ordinary, individually-valid `SignerMessageV0::BlockResponse` messages — first `Rejected`, then `Accepted` — to its own StackerDB slot for the same `signer_signature_hash`. This is a normal message sequence the protocol already anticipates (the docs describe signers being allowed to reconsider prior rejections for several reject reasons), so the sequence is easy to produce and requires no cryptographic forgery, only ordinary message crafting from the signer's own key. The window in which the double count matters (i.e., contributes to actually crossing the 30% rejection threshold) depends on that signer's weight and the other genuine rejectors' weight, but the bug itself is unconditionally present.

### Recommendation
Make the double-counting guards symmetric and mutually exclusive: gate both the accept and reject weight additions on the same `responded_signers`-style check, and when a signer's vote changes (accept↔reject), either reject re-votes outright for a slot once it has responded, or explicitly move/clear the previously-counted weight from the opposite pool before adding it to the new one — mirroring the behavior already implemented and tested in `stacks-signer/src/signerdb.rs`'s `reject_then_accept` test.

### Proof of Concept
1. A miner proposes a block; `StackerDBListener::insert_block` creates a fresh `BlockStatus` with `total_weight_approved = 0`, `total_weight_rejected = 0`.
2. Malicious/compromised signer S (slot id `k`, weight `w`) pushes a `BlockResponse::Rejected` chunk for the block's `signer_signature_hash`. The handler executes `responded_signers.insert(k)` → `true`, so `total_weight_rejected += w`.
3. The same signer S then pushes a `BlockResponse::Accepted` chunk for the identical `signer_signature_hash` (using the same or a legitimately-crafted signature). The accept handler checks `!gathered_signatures.contains_key(&k)` → `true` (nothing was ever inserted there), so `total_weight_approved += w` as well.
4. `BlockStatus` for this block now shows both `total_weight_rejected` and `total_weight_approved` including `w` from the same signer — the signer's weight has been counted twice across the two decision pools, in contrast to the intended one-vote-per-signer accounting enforced elsewhere in the codebase (e.g., `stacks-signer/src/signerdb.rs`).

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

**File:** stacks-signer/CHANGELOG.md (L132-135)
```markdown
### Changed

- Do not count both a block acceptance and a block rejection for the same signer/block. Also ignore repeated responses (mainly for logging purposes).
- Database schema updated to version 16
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
