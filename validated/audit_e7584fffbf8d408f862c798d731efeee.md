Confirmed: the `Accepted` branch never checks `block.responded_signers` (only used by the `Rejected` branch) before crediting `total_weight_approved`, so a signer that already rejected can later be double-counted without any reversal of `total_weight_rejected`.

### Title
Rejection weight is never reversed when a signer later accepts, causing `total_weight_rejected`/`total_weight_approved` to be simultaneously inflated for the same signer - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`stacks-signer`'s own `signerdb.rs` explicitly allows a signer to first reject and then later sign/accept the same block (a rejection is a "revocable opinion" — see `add_block_rejection_signer_addr`, which clears a stored rejection once a signature exists, and the `reject_then_accept` test at [1](#0-0) ). But the miner-side vote tally in `StackerDBListener::poll` never mirrors that reversal: `total_weight_rejected` is a monotonically-increasing counter that is only ever added to, exactly like the reported `bank.totalLend` that is incremented on lend but never decremented on the equivalent "undo" path (liquidation withdrawal).

### Finding Description
In `stackerdb_listener.rs`, per-block vote state is tracked in `BlockStatus` with two independent weight accumulators, `total_weight_approved` and `total_weight_rejected` [2](#0-1) .

When a `Rejected` message is processed, the code inserts the slot into `responded_signers` and, only if that insert is new, adds the signer's weight to `total_weight_rejected`: [3](#0-2) 

When an `Accepted` message is later processed for the *same* signer/slot, the guard used is `!block.gathered_signatures.contains_key(&slot_id)` — a completely separate map that was never touched by the rejection path: [4](#0-3) 

Because `gathered_signatures` does not contain the slot_id (it was only inserted into `responded_signers` by the reject handler), this check passes and `total_weight_approved` is incremented for a signer whose weight is *already* sitting inside `total_weight_rejected`. Nothing in the `Accepted` branch consults `responded_signers` or reverses the earlier `total_weight_rejected` contribution.

The two counters are each individually monotonic (`saturating_add`, never subtracted), so once a signer's weight has been added to `total_weight_rejected` it stays there forever for this block, even after that same signer switches to accepting. This breaks the intended equality that `total_weight_approved` and `total_weight_rejected` represent disjoint, mutually-exclusive partitions of `self.total_weight` per signer for a given block — the same class of accounting break as `bank.totalLend` never being decremented on the corresponding reverse action.

### Impact Explanation
This directly affects the safety/liveness-critical decision logic in `signer_coordinator.rs`, which drives the miner's accept/reject decision for the block: [5](#0-4) 

Two consequences follow from the un-reversed rejection weight:
1. `total_weight_rejected` can remain permanently inflated by a signer's stale rejection weight even though that signer has since signed acceptance. This can push `total_weight_rejected + weight_threshold > total_weight` and cause the miner to treat an actually-approved-enough block as rejected (`SignersRejected`), stalling block production — a liveness wedge (a signer/quorum being counted such that valid signing progress is never recognized by the miner even though the signer set intends to approve).
2. Simultaneously `total_weight_approved` also grows from the same signer's later acceptance, so a single signer's weight is double-counted across both totals, corrupting the arithmetic invariant the threshold check relies on (`total_weight_approved`/`total_weight_rejected` no longer form a valid partition of `total_weight`).

This matches the "aggregated-weight vs verified-accepts" equality-break class: the aggregated weight (`total_weight_rejected`) no longer reflects the verified, current position of each signer.

### Likelihood Explanation
This requires no majority or key compromise — a single signer flipping its own vote from reject to accept (an explicitly-supported, single-signer-controlled sequence per the `signerdb.rs` `reject_then_accept` behavior/tests) is sufficient to trigger the stale counter. Reject-then-accept sequences are a normal, documented part of the signer protocol (rejections are revocable), so this is readily reachable in normal/adversarial network conditions (message reordering, retries, or a signer legitimately changing its mind after receiving more information), not a contrived edge case.

### Recommendation
Track rejection/acceptance per-slot state (e.g., a `HashMap<u32, Vote>` instead of two independent monotonic counters plus a separate `responded_signers`/`gathered_signatures` set), and when a slot's vote changes from Rejected to Accepted (or vice versa), subtract the old contribution before adding the new one before comparing to `self.weight_threshold` / `self.total_weight`. Recompute `total_weight_approved`/`total_weight_rejected` from the current per-slot vote map rather than incrementally, or explicitly check `responded_signers`/prior vote kind in the `Accepted` branch and decrement `total_weight_rejected` if the slot had previously been recorded as a rejection.

### Proof of Concept
1. Miner proposes block B; signer S (weight `w`) sends `BlockResponse::Rejected` first. `stackerdb_listener.rs` inserts `slot(S)` into `block.responded_signers` and adds `w` to `block.total_weight_rejected` (lines 515-518).
2. S later reconsiders and sends `BlockResponse::Accepted` for the same block B (a scenario the signer-side `signerdb.rs`/`reject_then_accept` logic explicitly allows and tests for).
3. In the `Accepted` handler, `block.gathered_signatures.contains_key(&slot(S))` is `false` (this map was never touched by the reject path), so the guard at line 443 passes and `w` is added to `block.total_weight_approved` as well (lines 443-465).
4. Result: `block.total_weight_rejected` still contains `w` from step 1 (never decremented), and `block.total_weight_approved` now also contains `w` from step 3. The two totals are no longer a correct partition of `self.total_weight`; `total_weight_rejected` remains inflated by `w` indefinitely for this block, which can (with other signers) push `total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight` and cause `signer_coordinator.rs` to return `SignersRejected` for a block the signer set actually intends to approve.

### Citations

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
