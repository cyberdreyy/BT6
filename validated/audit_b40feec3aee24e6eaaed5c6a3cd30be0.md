## Finding [1](#0-0) 

The bug-class from the Basket.sol report — a quantity used in one accounting bucket is not excluded/retracted once it is also accounted for elsewhere, silently over-counting — has a direct analog in the miner-side StackerDB signature aggregator.

### Title
Miner's signer-response aggregator double-counts a signer's weight into both `total_weight_rejected` and `total_weight_approved` when the signer switches its vote - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener` maintains a per-block `BlockStatus` with two independent weight counters, `total_weight_approved` and `total_weight_rejected`, plus a single `responded_signers` set meant to prevent double counting. The `Rejected` handling path correctly gates weight addition behind `responded_signers.insert(slot_id)`, but the `Accepted` handling path instead gates weight addition behind `!gathered_signatures.contains_key(&slot_id)`. This means a signer who first rejects (their weight is added to `total_weight_rejected` and their `slot_id` recorded in `responded_signers`) and later sends an `Accepted` response for the same block is counted a second time: `gathered_signatures` does not yet contain their slot, so their weight is *also* added to `total_weight_approved`, without ever being subtracted from `total_weight_rejected`.

### Finding Description [2](#0-1) 
For an `Accepted` response, the code only checks `gathered_signatures` to decide whether to add weight:
```
if !block.gathered_signatures.contains_key(&slot_id) {
    block.total_weight_approved = block.total_weight_approved.saturating_add(signer_entry.weight);
    ...
}
block.gathered_signatures.insert(slot_id, signature);
block.responded_signers.insert(slot_id);
``` [3](#0-2) 
For a `Rejected` response, the shared `responded_signers` set is the sole dedup gate:
```
if block.responded_signers.insert(slot_id) {
    block.total_weight_rejected = block.total_weight_rejected.saturating_add(signer_entry.weight);
    ...
}
```
Because `responded_signers` is shared between the two branches but only the reject branch treats it as authoritative, a signer that rejects then accepts (a supported, intentional behavior elsewhere in the codebase — the signer's own `signerdb.rs` explicitly allows and cleanly reconciles reject→accept transitions, see `reject_then_accept`/`accept_then_reject` tests) is left permanently double-booked in this miner-side aggregator: its weight remains in `total_weight_rejected` forever (nothing ever decrements it) while also being added to `total_weight_approved`. [4](#0-3) 

The consumer of this state, `SignerCoordinator::wait_for_signer_signatures` (used by the block miner), checks the rejection-quorum branch before the approval branch: [5](#0-4) 
```
if block_status.total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight {
    ... return Err(NakamotoNodeError::SignersRejected { ... });
} else if block_status.total_weight_approved >= self.weight_threshold {
    ... return Ok(...); // block accepted
}
```
Because the reject-weight can never be retracted once a signer flips to accept, a block that legitimately reaches the 70% approval threshold can simultaneously carry stale rejection weight from a signer(s) who *changed their mind*, and if that stale rejection weight (still counted from the earlier vote) alone crosses the >30% blocking-minority line, the miner takes the `SignersRejected` branch and permanently excludes the block/transactions — even though a valid, sufficient set of *current* acceptances exists. This is a liveness wedge triggerable by a single signer flip‑flopping its own response (no majority or other signer's key required), reachable purely through the normal `BlockResponse` gossip path that `StackerDBListener` already processes.

### Impact Explanation
This falls under the "High" impact bucket: a signer's stale/overridden rejection weight can wedge the miner into treating a validly-approved block (or transaction) as globally rejected, denying block/transaction inclusion progress even though sufficient current signer weight approved it — a liveness failure in the miner's block-acceptance decision logic, not merely a cosmetic log discrepancy.

### Likelihood Explanation
Reject→accept vote transitions are an explicitly supported, documented behavior of the signer protocol (`stacks-signer/CHANGELOG.md`: "For some rejection reasons, a signer will reconsider a block proposal that it previously rejected"), so this is not a contrived edge case — it can occur during normal validation-timing races (e.g., `check_submitted_block_proposal` timing out and rejecting, then later reconsidering) or be intentionally forced by a single signer sending its own two messages, requiring no other signer's cooperation, key, or majority.

### Recommendation
In the `Accepted` handling branch of `StackerDBListener`, gate the `total_weight_approved` addition on the shared `responded_signers` set consistently with the `Rejected` branch, and if the signer previously rejected, retract their weight from `total_weight_rejected` (and any populated `failed_txids` entries) before crediting `total_weight_approved`, so a signer's weight is only ever counted once, in its current/latest bucket.

### Proof of Concept
1. Miner proposes block B; `StackerDBListener::insert_block` initializes `BlockStatus` with all counters at 0.
2. Signer S (weight `w`) sends `BlockResponse::Rejected` for B → `responded_signers = {S}`, `total_weight_rejected = w`.
3. Signer S later reconsiders and sends `BlockResponse::Accepted` for B (a scenario the signer-side logic explicitly supports) → since `gathered_signatures` doesn't yet contain S's slot, `total_weight_approved = w` is added too; `total_weight_rejected` remains `w` (never decremented).
4. If additional signers push `total_weight_approved` to ≥ `weight_threshold` while `total_weight_rejected` (still inflated by S's stale weight) independently exceeds `total_weight - weight_threshold`, `SignerCoordinator::wait_for_signer_signatures` evaluates the rejection branch first and returns `NakamotoNodeError::SignersRejected`, discarding a block that in fact carries a valid, sufficient, up-to-date acceptance quorum.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-519)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);

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
