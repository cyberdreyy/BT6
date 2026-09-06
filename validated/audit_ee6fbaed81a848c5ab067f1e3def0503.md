### Title
Miner-side signature/rejection tally never reconciles a signer's flip from Reject to Accept, letting stale rejection weight wedge otherwise-accepted blocks - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
The miner-side `StackerDBListener`, which feeds `SignerCoordinator` the aggregate weight used to decide whether a proposed block is accepted or rejected, keeps two independent running counters (`total_weight_approved`, `total_weight_rejected`) per block. When a signer that had previously rejected a block later legitimately re-evaluates and accepts it (a supported, expected flow), the code adds that signer's weight to `total_weight_approved` but never removes it from `total_weight_rejected`. This is the same class of bug as the reported oracle issue: a stale, "all-inclusive" accounting value (here, rejection weight that should have been superseded) keeps being used instead of the value that reflects only the signer's current, final vote.

### Finding Description
`stacks-signer`'s own DB layer treats acceptance and rejection as mutually exclusive per signer/block: `SignerDb::add_block_rejection_signer_addr` refuses to record a rejection once a signature exists, and `SignerDb::add_block_signature` implicitly supersedes a prior rejection — the `reject_then_accept` unit test proves that after a signer accepts, `get_block_rejection_signer_addrs` returns empty for that signer. [1](#0-0) [2](#0-1) 

The miner-side `StackerDBListener`, however, keeps a separate in-memory tally that does not enforce this invariant. In the `Accepted` branch, weight is only gated on whether `gathered_signatures` already contains the slot — it never checks or clears `responded_signers`/`total_weight_rejected` for that slot: [3](#0-2) 

In the `Rejected` branch, weight is only added once (gated by `responded_signers.insert(slot_id)`), and it is never removed later: [4](#0-3) 

So the sequence "signer rejects, then later accepts" (a normal reconsideration flow — the CHANGELOG explicitly documents that "For some rejection reasons, a signer will reconsider a block proposal it previously rejected", and there is a dedicated test `signer_can_accept_rejected_block`) leaves that signer's weight counted in **both** `total_weight_rejected` and `total_weight_approved` simultaneously. [5](#0-4) [6](#0-5) 

`SignerCoordinator::send_and_recv_signer_message` (or the equivalent wait loop) checks the rejection condition first: [7](#0-6) 

Because `total_weight_rejected` retains stale weight from signers who have since accepted, the sum `total_weight_rejected + weight_threshold` can exceed `total_weight` purely from double-counted weight, even while `total_weight_approved` independently also reaches `weight_threshold` from genuine, current acceptances. Since the rejection branch is checked before the acceptance branch, the miner can incorrectly declare the block **globally rejected** (`NakamotoNodeError::SignersRejected`) and even permanently ban transactions via `permanently_excluded_txids`/`temporarily_excluded_txids`, despite a real, current supermajority of signers having accepted the block.

### Impact Explanation
This breaks the intended equality between "aggregated weight" and "verified, current accepts/rejects" at the miner/coordinator layer: a signer's weight should count toward exactly one bucket (its most recent vote), but it can count toward both. The practical effect is a liveness wedge — a legitimately-approvable block gets treated as rejected, and the associated transactions can be excluded from future proposals, based on stale accounting rather than the signers' actual current state. This does not require a majority of malicious/colluding signers; it can be triggered purely through the normal, documented reconsideration flow that already exists in the codebase (multiple signers initially rejecting for a transient reason such as parent-not-yet-processed, then reconsidering and accepting once the reason clears).

### Likelihood Explanation
The trigger condition (signers initially rejecting then later accepting the same proposal) is not a hypothetical edge case — it is an explicitly supported and tested behavior (`signer_can_accept_rejected_block`, and the "reconsider a block proposal it previously rejected" changelog entry). Any set of signers whose combined weight is sufficient to have initially pushed `total_weight_rejected` close to the blocking-minority threshold, and who later switch to accepting, can trip this bug without any signer acting maliciously or the tester needing majority control of keys — this can occur under ordinary network/timing conditions (e.g., a proposal seen as invalid transiently, later revalidated).

### Recommendation
In `stackerdb_listener.rs`, mirror the mutual-exclusivity invariant already implemented in `SignerDb`: when a valid `Accepted` `BlockResponse` is processed for a slot that is already present in `responded_signers` as a rejector, subtract that signer's weight from `total_weight_rejected` (and drop any per-txid `failed_txids` contribution attributed to that signer) before/while adding it to `total_weight_approved`. Symmetrically, decide whether a late rejection from a previously-accepted slot should be accepted at all (currently `SignerDb` refuses this on the signer side); if it is accepted here, the acceptance weight should be removed as well. The cleanest fix is to track, per slot, a single "current vote" (Some(Accepted)/Some(Rejected)) rather than two independent monotonically-increasing counters, and recompute `total_weight_approved`/`total_weight_rejected` from that single source of truth.

### Proof of Concept
1. Start a `SignerCoordinator`/`StackerDBListener` session for a proposed block with signers S1..Sn, where S1's weight is close to (but under) the 30%-blocking-minority threshold.
2. S1 sends a `BlockResponse::Rejected` for the block (e.g., due to `ProposalTooOld` or any reconsiderable reason) — `total_weight_rejected += weight(S1)`, and `responded_signers` now contains S1's `slot_id`.
3. The condition triggering the reject reason clears (e.g., parent block finishes processing), and S1 legitimately reconsiders and sends a valid `BlockResponse::Accepted` for the same block — per the existing reconsideration logic in `stacks-signer/src/v0/signer.rs`.
4. In `stackerdb_listener.rs`'s `Accepted` handler, since `gathered_signatures` does not yet contain S1's slot, `total_weight_approved += weight(S1)` executes — but `total_weight_rejected` is never decremented.
5. Have enough other signers accept normally so that `total_weight_approved >= weight_threshold` (real supermajority achieved) while, simultaneously, the stale + newly accumulated `total_weight_rejected` from other signers who behaved the same way as S1 pushes `total_weight_rejected + weight_threshold > total_weight`.
6. `SignerCoordinator`'s loop hits the rejection branch first and returns `NakamotoNodeError::SignersRejected`, incorrectly treating a currently-approvable block as globally rejected, and potentially excluding transactions from the block via `permanently_excluded_txids`/`temporarily_excluded_txids` based on the stale rejection data.

### Citations

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

**File:** stacks-signer/CHANGELOG.md (L178-180)
```markdown
### Changed

- For some rejection reasons, a signer will reconsider a block proposal that it previously rejected ([#5880](https://github.com/stacks-network/stacks-core/pull/5880))
```

**File:** stacks-node/src/tests/signer/v0/mod.rs (L7153-7160)
```rust
#[test]
#[ignore]
/// This test verifies that a a signer will accept a rejected block if it is
/// re-proposed and determined to be legitimate. This can happen if the block
/// is initially rejected due to a test flag or because the stacks-node had
/// not yet processed the block's parent.
fn signer_can_accept_rejected_block() {
    if env::var("BITCOIND_TEST") != Ok("1".into()) {
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
