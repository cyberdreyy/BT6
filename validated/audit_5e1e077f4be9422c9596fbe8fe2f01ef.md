### Title
Node-side signer weight aggregation double-counts a signer that first rejects then accepts the same block, letting a rejected weight also count toward the approval threshold - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
The Seaport `_executionInvariantChecks()` bug is a class of "the accounting map used to gate a state transition doesn't match the map that actually recorded prior state," letting an actor's action be excluded from (or duplicated in) an aggregate it should be included in exactly once. The direct analog in this repo is in the miner/coordinator's per-block vote tally in `StackerDBListener::poll_events` (`stacks-node/src/nakamoto_node/stackerdb_listener.rs`), where `BlockStatus` uses two different sets/maps to decide "have I already counted this signer's weight for this response type," instead of one canonical per-signer response record.

### Finding Description
`BlockStatus` (`stacks-node/src/nakamoto_node/stackerdb_listener.rs:70-82`) tracks `gathered_signatures` (slot_id → signature, populated only on `Accepted`) and `responded_signers` (a `HashSet<u32>`, used as the "already counted" guard for `Rejected`), plus `total_weight_approved`/`total_weight_rejected`.

- On `BlockResponse::Accepted`, the "already counted" check is `!block.gathered_signatures.contains_key(&slot_id)` [1](#0-0) , and afterward both `gathered_signatures` and `responded_signers` get the slot inserted [2](#0-1) .
- On `BlockResponse::Rejected`, the "already counted" check is instead `block.responded_signers.insert(slot_id)` [3](#0-2) , which never touches `gathered_signatures`.

Because the two branches gate on different collections, a signer that sends a `Rejected` message first (weight added to `total_weight_rejected`, `slot_id` added to `responded_signers` only) and later sends an `Accepted` message for the *same* `signer_signature_hash` (e.g. after legitimately reconsidering, per the "reject reason allows us to reconsider" reconsideration path in `should_reevaluate_reject_reason`/`determine_response` on the signer side [4](#0-3) ) will pass the `Accepted` guard, because `gathered_signatures` never held that slot_id. Its weight is then *also* added to `total_weight_approved`.

This breaks the aggregated-weight vs. verified-response equality the coordinator relies on: `total_weight_approved + total_weight_rejected` can exceed `total_weight` for a single reward cycle, and one signer's weight is simultaneously live in both buckets. Note the signer's own local `SignerDb` deliberately enforces mutual exclusivity between a stored acceptance and rejection for a given block (see `reject_then_accept`/`accept_then_reject` tests in `signerdb.rs` [5](#0-4) ) — the coordinator-side tally in `stackerdb_listener.rs` does not mirror that invariant, so the two subsystems disagree on what "one signer, one vote" means.

### Impact Explanation
This lets the effective `total_weight_approved` reach `weight_threshold` (`compute_voting_weight_threshold`) using fewer *distinct* approving signers than the protocol assumes, because one signer's weight is reused across both the rejection and approval accounting after a legitimate flip (or a signer replaying/re-sending its earlier Accepted response while also having sent a Rejected one due to message reordering over StackerDB). This is a miscounted response inflating the approval side of the equality the coordinator uses to decide `Ok(gathered_signatures)` at `stacks-node/src/nakamoto_node/stackerdb_listener.rs:541-545`, i.e. a form of "rejection recounted toward acceptance," which can let the node accept/broadcast a block with less real signer weight behind it than the 70% threshold requires. This does not require a signer key compromise or a majority of signers — a single signer's ordinary message pair (reject-then-reconsider-accept) triggers it.

### Likelihood Explanation
The reconsideration path exists intentionally in the signer (`should_reevaluate_reject_reason`, referenced in `docs/signer-flows.md`), so a signer legitimately transitioning from `Rejected` to `Accepted` for the same block/proposal is an in-protocol event, not an attacker-crafted edge case — it just requires normal network/timing conditions (e.g., a stale rejection reason that becomes reconsiderable, or duplicate delivery over StackerDB). Because it needs only one signer's ordinary behavior and no majority collusion, likelihood is moderate to high whenever a signer's local state legitimately transitions between response states for a tracked block.

### Recommendation
Track a single canonical "last response counted for this signer for this block" (with its resulting weight bucket) instead of two independently-gated collections (`gathered_signatures` vs `responded_signers`). When a signer's response changes from `Rejected` to `Accepted` (or vice versa) for the same block hash, atomically move the weight between `total_weight_rejected` and `total_weight_approved` rather than only guarding new insertion into one of the two collections.

### Proof of Concept
1. Node is coordinating a block proposal with `signer_signature_hash = H` and reward-cycle weight threshold `T`.
2. Signer S (weight `w`), for some legitimate reason, first responds `Rejected(H)`. Coordinator: `responded_signers.insert(S.slot_id)` succeeds → `total_weight_rejected += w`.
3. Shortly after, S's local state machine reconsiders (a reconsiderable reject reason) and re-evaluates the same block, ultimately calling `determine_response` → sends `Accepted(H)`.
4. Coordinator's `Accepted` handler checks `!gathered_signatures.contains_key(S.slot_id)` — true, since only `Rejected` touched `responded_signers`, not `gathered_signatures` — so it proceeds: `total_weight_approved += w`.
5. Now both `total_weight_rejected` and `total_weight_approved` include `w` for the same signer S; `total_weight_approved + total_weight_rejected` can exceed `self.total_weight`, and the approval side can cross `weight_threshold` while effectively double-counting one signer's weight instead of counting each signer once.

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

**File:** stacks-signer/src/v0/signer.rs (L1560-1571)
```rust
        } else {
            info!(
                "{self}: received a block proposal for this block before, but our rejection reason allows us to reconsider";
                "reject_reason" => ?block_info.reject_reason,
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_proposal.block.block_id(),
                "block_height" => block_proposal.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "consensus_hash" => %block_proposal.block.header.consensus_hash
            );
        }
        true
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
