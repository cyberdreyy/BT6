### Title
Node-side signer vote aggregator retains stale rejection weight after a signer switches to acceptance, corrupting the approve/reject tally - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
The miner-side `StackerDBListener` tallies `total_weight_approved` and `total_weight_rejected` for a block proposal using two independently-guarded code paths keyed on the same `slot_id`. When a signer's vote transitions from *reject* to *accept* for the same block (an explicitly valid, documented state transition on the signer side), the listener adds the signer's weight to `total_weight_approved` without ever subtracting it from `total_weight_rejected`. The signer's weight ends up counted in both buckets simultaneously, breaking the invariant that `total_weight_approved + total_weight_rejected` should never exceed the honestly-reachable total for a single vote per signer.

### Finding Description
`BlockStatus` tracks `responded_signers: HashSet<u32>` and `gathered_signatures: BTreeMap<u32, MessageSignature>` per block hash. [1](#0-0) 

On an **Accept** message, the weight-add is guarded by `gathered_signatures`, not by `responded_signers`: [2](#0-1) 

On a **Reject** message, the weight-add is guarded by `responded_signers.insert(slot_id)`: [3](#0-2) 

Consider a single, honest signer that first rejects a proposal and later re-evaluates and accepts it — a transition explicitly modeled as valid in the signer's own state machine (`LocallyRejected --> LocallyAccepted : re-evaluated`) and enforced by `BlockInfo::check_state`: [4](#0-3) 

Sequence for that signer's `slot_id`:
1. Reject arrives first: `responded_signers.insert(slot_id)` returns `true` → `total_weight_rejected += weight`.
2. Accept arrives later: `gathered_signatures.contains_key(&slot_id)` is `false` (only `responded_signers` has it) → `total_weight_approved += weight` is also applied, and `gathered_signatures`/`responded_signers` are updated.

The result: this signer's weight is now present in *both* `total_weight_approved` and `total_weight_rejected`, and `total_weight_rejected` is never decremented for the earlier vote. Contrast this with the safe direction (accept-then-reject), where the second reject is correctly ignored because `responded_signers.insert` returns `false`: [5](#0-4) 

The signer-side `SignerDb` explicitly guards against this class of inconsistency by clearing/blocking the opposite-vote row when a signature exists (`add_block_rejection_signer_addr` refuses to record a rejection once a signature exists, and adding a signature clears any prior rejection row, as shown by the `reject_then_accept`/`accept_then_reject` tests): [6](#0-5) [7](#0-6) 

No equivalent clearing exists in the node-side `StackerDBListener` tally — this is the exact "same account, both operations processed without distinguishing them, only the later value gets recorded without subtracting the former" pattern described in the reference report, mapped from token balances onto vote-weight bookkeeping.

### Impact Explanation
`SignerCoordinator::wait_for_signatures` evaluates the reject condition *before* the accept condition: [8](#0-7) 

Because a repentant signer's stale rejection weight is never retracted, it can combine with a genuinely rejecting minority to cross the `>30%` blocking threshold in `total_weight_rejected`, even though the same signer's corrected vote already pushed `total_weight_approved` past the real 70% threshold. Since the rejection branch is checked first and returns `Err(NakamotoNodeError::SignersRejected { .. })` immediately, the miner discards a proposal that in reality carries enough genuine, distinct signer weight to be accepted. This is a liveness wedge on the mining/coordination path: valid blocks that should be pushed can be spuriously treated as rejected, stalling tenure progress and forcing costly re-proposals, triggerable by ordinary message reordering/timing from a single honest signer rather than requiring any signer majority or byzantine behavior.

### Likelihood Explanation
No malicious majority or key compromise is required. Any single signer that legitimately re-evaluates a proposal (a first-class, documented transition in the signer's local state machine) and whose reject/accept messages are observed by the coordinator in that order — plausible under normal network delay/reordering over StackerDB gossip — triggers the inconsistency. The bug is purely in bookkeeping guarded by two different keys (`gathered_signatures` vs `responded_signers`) for the same `slot_id`, making it deterministic once the reject-then-accept ordering occurs.

### Recommendation
Guard both branches with the same, single source of truth per `slot_id` (e.g. always check/update via `responded_signers`, or explicitly remove the signer's weight from `total_weight_rejected` and any per-txid `failed_txids` weight when a later Accept is recorded, mirroring the explicit clear-on-flip logic already implemented in `stacks-signer/src/signerdb.rs::add_block_rejection_signer_addr`/`add_block_signature`). This restores the invariant that a signer's weight is attributed to exactly one of `total_weight_approved` / `total_weight_rejected` at any time.

### Proof of Concept
1. Coordinator inserts a fresh `BlockStatus` for a proposed block via `StackerDBListenerComms::insert_block`. [9](#0-8) 
2. Signer S (slot `k`, weight `w`) sends `BlockResponse::Rejected` for the block → `responded_signers = {k}`, `total_weight_rejected = w`.
3. Signer S later re-evaluates and sends `BlockResponse::Accepted` for the same block hash (a normal `LocallyRejected -> LocallyAccepted` transition) → since `gathered_signatures` doesn't contain `k`, `total_weight_approved += w`; now `total_weight_approved = w` and `total_weight_rejected = w` simultaneously, both attributing S's weight.
4. If other signers' independent rejections bring `total_weight_rejected + weight_threshold > total_weight` while `total_weight_approved >= weight_threshold` is also independently true, `wait_for_signatures` hits the reject branch first and returns `SignersRejected`, discarding a block that legitimately reached the acceptance threshold.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L486-519)
```rust
                    SignerMessageV0::BlockResponse(BlockResponse::Rejected(rejected_data)) => {
                        let (lock, cvar) = &*self.blocks;
                        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");

                        let Some(block) = blocks.get_mut(&rejected_data.signer_signature_hash)
                        else {
                            info!(
                                "StackerDBListener: Received rejection for block that we did not request. Ignoring.";
                                "signer_signature_hash" => %rejected_data.signer_signature_hash,
                                "slot_id" => slot_id,
                                "signer_set" => self.signer_set,
                            );
                            continue;
                        };

                        let rejected_pubkey = match rejected_data.recover_public_key() {
                            Ok(rejected_pubkey) => {
                                if rejected_pubkey != signer_pubkey {
                                    warn!("StackerDBListener: Recovered public key from rejected data does not match signer's public key. Ignoring.");
                                    continue;
                                }
                                rejected_pubkey
                            }
                            Err(e) => {
                                warn!("StackerDBListener: Failed to recover public key from rejected data: {e:?}. Ignoring.");
                                continue;
                            }
                        };

                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);

```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L691-704)
```rust
impl StackerDBListenerComms {
    /// Insert a block into the block status map with initial values.
    pub fn insert_block(&self, block: &NakamotoBlockHeader) {
        let (lock, _cvar) = &*self.blocks;
        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");
        let block_status = BlockStatus {
            responded_signers: HashSet::new(),
            gathered_signatures: BTreeMap::new(),
            total_weight_approved: 0,
            total_weight_rejected: 0,
            failed_txids: HashMap::new(),
        };
        blocks.insert(block.signer_signature_hash(), block_status);
    }
```

**File:** stacks-signer/src/signerdb.rs (L313-329)
```rust
    /// Check if the block state transition is valid
    fn check_state(&self, state: BlockState) -> bool {
        let prev_state = &self.state;
        if *prev_state == state {
            return true;
        }
        match state {
            BlockState::Unprocessed => false,
            BlockState::LocallyAccepted | BlockState::LocallyRejected => !matches!(
                prev_state,
                BlockState::GloballyRejected | BlockState::GloballyAccepted
            ),
            BlockState::GloballyAccepted => !matches!(prev_state, BlockState::GloballyRejected),
            BlockState::GloballyRejected => !matches!(prev_state, BlockState::GloballyAccepted),
            BlockState::PreCommitted => matches!(prev_state, BlockState::Unprocessed),
        }
    }
```

**File:** stacks-signer/src/signerdb.rs (L1922-1941)
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
