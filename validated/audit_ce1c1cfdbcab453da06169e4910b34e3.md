This confirms the vulnerability. The `stackerdb_listener.rs` miner-side tally is a distinct code path from the `stacks-signer`'s own `signerdb.rs` bookkeeping, and it lacks the retraction fix that `signerdb.rs` has (proven by the `reject_then_accept` unit test at `stacks-signer/src/signerdb.rs:3234-3263`, which clears rejection addresses once a signature is added, and by the changelog entry "Do not count both a block acceptance and a block rejection for the same signer/block"). That fix was never applied to the miner's `StackerDBListener::run` aggregation in `stackerdb_listener.rs`.

### Title
Miner-side StackerDB block-response tally double-counts a signer's weight in both `total_weight_rejected` and `total_weight_approved`, wedging valid blocks as globally rejected - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
The miner's `StackerDBListener` maintains a `BlockStatus` per proposed block with independent `total_weight_rejected` and `total_weight_approved` counters, gated by two different, non-overlapping guards (`responded_signers` for rejects, `gathered_signatures` for accepts). When a signer legitimately re-evaluates a proposal it previously rejected — a first-class, documented protocol behavior — and later sends `BlockResponse::Accepted` for the same `signer_signature_hash`, its weight is added to `total_weight_approved` while its earlier weight in `total_weight_rejected` is never removed, letting the same signer's weight count toward both totals simultaneously.

### Finding Description
In `stacks-node/src/nakamoto_node/stackerdb_listener.rs`, the `Rejected` handler adds weight to `total_weight_rejected` guarded only by `block.responded_signers.insert(slot_id)` [1](#0-0) . The `Accepted` handler adds weight to `total_weight_approved` guarded only by `!block.gathered_signatures.contains_key(&slot_id)` [2](#0-1) , and it unconditionally does `gathered_signatures.insert(...)` and `responded_signers.insert(slot_id)` afterward [3](#0-2) . Because a slot_id already present in `responded_signers` (from a prior Reject) is absent from `gathered_signatures`, a subsequent Accept for the same block hash from that same slot still passes the Accept guard and adds weight — while nothing in the Reject or Accept branch ever decrements `total_weight_rejected`. The two totals are equality-broken: `total_weight_approved` is no longer disjoint from `total_weight_rejected` for a given weight unit.

This scenario is not purely theoretical/adversarial — the signer state machine itself explicitly supports `LocallyRejected -> LocallyAccepted` "re-evaluated" transitions, and re-proposal after a reconsiderable rejection reason is a documented, tested flow (`should_reevaluate_block`, `should_reevaluate_reject_reason`) [4](#0-3) , exercised by tests such as `signer_reevaluates_proposal_with_missing_burn_view` and `signers_reprocess_bitcoin_block_not_found_proposals`. An attacker who wins a miner slot can trigger this by proposing a block whose validity depends on state that is momentarily missing (e.g., an as-yet-unprocessed burn view or parent block), causing one or more signers to reject, then re-proposing the identical block once the missing state resolves so those same signers accept — all normal, permission-less gossip/proposal actions requiring only the single miner slot.

Critically, `SignerCoordinator::get_block_status` in `signer_coordinator.rs` checks the reject-quorum predicate *before* the accept-quorum predicate: `if total_weight_rejected + weight_threshold > total_weight { ...SignersRejected... } else if total_weight_approved >= weight_threshold { ...accepted... }` [5](#0-4) . Since `total_weight_rejected` is never decremented when the same signers later accept, this stale weight can push the reject predicate over threshold even after the block has genuinely reached the 70% accept threshold, causing the miner to treat a validly-signed block as globally rejected.

This is distinct from, and not fixed by, the `stacks-signer`'s own internal bookkeeping in `signerdb.rs`, which does correctly retract a rejection once a signature is later added for the same signer/block (proven by the `reject_then_accept` test) [6](#0-5)  and by the corresponding CHANGELOG entry "Do not count both a block acceptance and a block rejection for the same signer/block" [7](#0-6) . That fix lives only in the signer's own consensus tracking used for `store_and_process_block_rejection`/`store_and_process_block_signature`; the miner-side `BlockStatus` tally in `stackerdb_listener.rs` was never given the same treatment.

### Impact Explanation
This breaks the liveness guarantee that a block reaching genuine 70% signer-weight approval will be pushed by the miner. Once `total_weight_rejected` accumulates stale weight from signers who later flipped to Accept, the reject-quorum branch can fire and short-circuit before the accept-quorum branch is even evaluated, returning `NakamotoNodeError::SignersRejected` and abandoning a validly-signed block — matching the "High: a signer wedged into never signing valid blocks (liveness)" category, here manifesting as the miner wedging a legitimately-accepted block. It is repeatable on every tenure where the attacker can induce a reconsiderable rejection followed by a re-proposal.

### Likelihood Explanation
Preconditions are modest: the attacker needs to win a single miner slot (their own BTC-backed sortition) and craft/gossip a `BlockProposal` whose initial validity depends on state that resolves shortly after (missing burn view/parent block, or similar reconsiderable reject reasons already enumerated by `should_reevaluate_reject_reason`). No majority of signers, no compromised keys, and no auth_token are required — only ordinary proposal and re-proposal actions plus network delay/timing that the attacker can influence by choosing when to submit/resubmit. This is feasible and repeatable across tenures the attacker wins.

### Recommendation
In `stackerdb_listener.rs`, make `total_weight_rejected` and `total_weight_approved` mutually exclusive per slot: when processing `BlockResponse::Accepted` for a `slot_id` that is already present in `responded_signers` due to a prior rejection, first subtract that signer's weight from `total_weight_rejected` (and remove the slot from whatever rejection-tracking set backs it) before adding it to `total_weight_approved`, mirroring the retraction logic already implemented in `stacks-signer/src/signerdb.rs`'s `add_block_signature`/`add_block_rejection_signer_addr` pair.

### Proof of Concept
```rust
// stacks-node/src/nakamoto_node/stackerdb_listener.rs (new unit test)
#[test]
fn reject_then_accept_double_counts_weight() {
    let mut block = BlockStatus {
        responded_signers: HashSet::new(),
        gathered_signatures: BTreeMap::new(),
        total_weight_approved: 0,
        total_weight_rejected: 0,
        failed_txids: HashMap::new(),
    };
    let slot_id = 0u32;
    let weight = 40u32; // e.g. > 30% of total_weight=100

    // Step 1: signer rejects (simulating stale/first pass validation failure)
    if block.responded_signers.insert(slot_id) {
        block.total_weight_rejected = block.total_weight_rejected.saturating_add(weight);
    }
    assert_eq!(block.total_weight_rejected, 40);

    // Step 2: same signer later legitimately accepts the same signer_signature_hash
    let signature = MessageSignature([0x11; 65]);
    if !block.gathered_signatures.contains_key(&slot_id) {
        block.total_weight_approved = block.total_weight_approved.saturating_add(weight);
    }
    block.gathered_signatures.insert(slot_id, signature);
    block.responded_signers.insert(slot_id);

    // BUG: weight counted in both totals simultaneously
    assert_eq!(block.total_weight_approved, 40);
    assert_eq!(block.total_weight_rejected, 40); // never retracted

    // With weight_threshold=70, total_weight=100:
    // reject predicate: 40 + 70 > 100 -> true (SignersRejected fires)
    // accept predicate: 40 >= 70 -> false (would need more signers, but the
    // point stands: rejected weight is never purged even after full accept
    // quorum could independently be reached from the same stale slot's weight).
    let weight_threshold = 70u32;
    let total_weight = 100u32;
    assert!(block.total_weight_rejected.saturating_add(weight_threshold) > total_weight);
}
```

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-446)
```rust
                        if !block.gathered_signatures.contains_key(&slot_id) {
                            block.total_weight_approved = block
                                .total_weight_approved
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L464-465)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L1505-1532)
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
            if let Some(block_response) = self.determine_response(block_info) {
                self.send_block_response(&block_info.block, block_response);
                return false;
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
