### Title
Miner-side signer weight tally double-counts a signer who flips from Reject to Accept on the same block - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
The node-side `StackerDBListener` tallies each block's `total_weight_approved` and `total_weight_rejected` using two *different* dedup guards — `gathered_signatures` for acceptances and `responded_signers` for rejections. A signer that first rejects and later reconsiders and accepts the same block (a normal, documented state transition: `LocallyRejected → LocallyAccepted : re-evaluated`) has its weight added to `total_weight_rejected` on the first message and *also* added to `total_weight_approved` on the second message, because the acceptance path never checks `responded_signers` and the earlier rejection is never subtracted. This mirrors the reported bug class — a sum-of-parts (approved + rejected weight for one block) that should never exceed the total signer weight but structurally can — leaking one signer's weight into both buckets.

### Finding Description
In `handle_new_stackerdb_chunks` (stackerdb_listener.rs):

- On `BlockResponse::Accepted`, the guard is `if !block.gathered_signatures.contains_key(&slot_id)` before adding `signer_entry.weight` to `block.total_weight_approved`, and then `block.gathered_signatures.insert(slot_id, signature)` plus `block.responded_signers.insert(slot_id)`. [1](#0-0) 

- On `BlockResponse::Rejected`, the guard is `if block.responded_signers.insert(slot_id)` before adding `signer_entry.weight` to `block.total_weight_rejected`. [2](#0-1) 

Walk through the sequence for one signer `S` on one block `B`:

1. `S` sends `Rejected(B)` first. `responded_signers.insert(slot_id)` returns `true` (first time) → `total_weight_rejected += weight(S)`. `slot_id` is now recorded in `responded_signers` but **not** in `gathered_signatures`.
2. `S` later reconsiders (a normal path per the signer's own state machine, `LocallyRejected → LocallyAccepted`) and sends `Accepted(B)` for the same `signer_signature_hash`.
3. The acceptance handler checks `gathered_signatures.contains_key(&slot_id)` — which is still empty for `S` — so it proceeds to add `weight(S)` to `total_weight_approved` as well.
4. Nothing in this code path ever decrements `total_weight_rejected` for `S`, nor is there any cross-check between the two maps before crediting a bucket.

Net result: `total_weight_approved + total_weight_rejected` for block `B` can exceed `self.total_weight` (the reward-cycle's total signer weight) — a single signer's weight is counted in both the "approved" and "rejected" tallies simultaneously, permanently, for the lifetime of that block's in-memory status. This is exactly the "protocolFee + tradeFee + royalty > inputAmount" bug class from the external report translated to weight accounting: two mutually-exclusive-in-principle categories are summed without checking that their combined total stays within the whole.

Contrast with the stacks-signer-side dedup, which explicitly guards against this: `add_block_rejection_signer_addr`/`add_block_signature` in the SignerDB enforce that an address can only be in one of the two states at a time (see the `accept_then_reject` / `reject_then_accept` tests, where switching from one to the other clears the previous entry). [3](#0-2) 
The `stackerdb_listener.rs` miner-side tally has no equivalent mutual-exclusion invariant.

### Impact Explanation
This is reachable by a single honest signer performing a documented, ordinary state transition (rejecting once, then re-evaluating to acceptance), not requiring a majority or malicious signer. Its effect is on the miner/node's aggregation of the 70%/30% weight thresholds used by `signer_coordinator.rs`:

```
if block_status.total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight {
    // treat block as globally rejected (SignersRejected)
} else if block_status.total_weight_approved >= self.weight_threshold {
    // treat block as accepted
}
``` [4](#0-3) 

Because a flipped signer's weight is stuck in `total_weight_rejected` forever (it is never removed) while also being added to `total_weight_approved`, the rejected-weight bucket accumulates stale/phantom weight from signers who have since approved. This inflates `total_weight_rejected` beyond what any currently-standing rejection reflects, making the `> self.total_weight - weight_threshold` "blocking minority" condition trip more easily than it should. A block that legitimately has enough real, current approvals to cross the 70% threshold can instead be declared globally rejected by the miner (`NakamotoNodeError::SignersRejected`) because of inflated, stale rejection weight from signers who no longer hold that position — a liveness wedge: the miner is driven to give up on and re-mine a block that a genuine supermajority is willing to sign, and (per the docs) miner activity/txid exclusion side effects (`temporarily_excluded_txids`/`permanently_excluded_txids`) are derived from this same inflated tally, needlessly punishing/excluding transactions.

This falls under "High" impact per the given scope: a wedge that prevents progress on an otherwise-signable block, driven purely by ordinary vote-flipping rather than adversarial majority action.

### Likelihood Explanation
The report's own documentation states that a signer may legitimately move `LocallyRejected → LocallyAccepted` upon re-evaluation, and that resending an acceptance after a stale rejection is an expected recovery path (see the signer-flows documentation and the `stale_proposal_of_accepted_block_resends_acceptance` test, which explicitly covers a signer's response changing after resubmission). [5](#0-4) 
Any tenure with a slow-arriving proposal, transient chainstate mismatch, or timing races that cause one or more signers to reject-then-accept the *same* block hash will trigger this double count. No malicious signer or majority collusion is required — this can occur under ordinary network/timeout conditions with a single signer's honest vote change.

### Recommendation
Track each signer's response to a given block in a single canonical state (mirroring what `SignerDb` already does), rather than two independently-updated weight counters guarded by two different sets:
- When processing `Accepted`, if the same `slot_id` was previously credited to `total_weight_rejected` (tracked via `responded_signers`/a similar per-signer verdict map), subtract that signer's weight from `total_weight_rejected` before adding it to `total_weight_approved` (and vice versa for `Rejected` following a prior `Accepted`).
- Alternatively, maintain one `HashMap<slot_id, Verdict>` (Accepted/Rejected) per block and recompute `total_weight_approved`/`total_weight_rejected` from that map on each update, guaranteeing `total_weight_approved + total_weight_rejected <= total_weight` always holds.

### Proof of Concept
1. Set up a reward cycle with `N` signers, weight threshold `T` (70% of total weight), and one block proposal `B`.
2. Have signer `S` (weight `w`) broadcast `BlockResponse::Rejected(B)` first — miner's `stackerdb_listener` records `total_weight_rejected += w` via `responded_signers.insert(slot_id)`.
3. Have `S` reconsider (simulating the documented `LocallyRejected → LocallyAccepted` transition, e.g., because the earlier rejection reason no longer applies) and broadcast `BlockResponse::Accepted(B)` for the same `signer_signature_hash`.
4. Observe the miner's `stackerdb_listener` code path: since `gathered_signatures` does not yet contain `S`'s `slot_id`, `total_weight_approved += w` is also applied — `S`'s weight is now counted in both `total_weight_approved` and `total_weight_rejected` for block `B`.
5. Arrange for other signers' weights such that, without the double count, `total_weight_rejected` alone would not clear the 30% blocking-minority bar, but with `S`'s stale rejection weight still included, `total_weight_rejected + weight_threshold > total_weight` becomes true in `signer_coordinator.rs`'s wait loop even though a genuine ≥70% of signers (including `S`) now approve — the miner incorrectly returns `NakamotoNodeError::SignersRejected` for a block that should have been accepted.

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

**File:** stacks-node/src/tests/signer/v0/proposal_replication_void.rs (L305-341)
```rust
#[tag(bitcoind)]
#[test]
#[ignore]
/// Verify that a signer which has already decided on a block does not flip its
/// decision when the same proposal is re-sent after
/// `block_proposal_max_age_secs`.
///
/// `ProposalTooOld` is only appropriate when the signer has nothing to report.
/// If the signer already accepted the block, overwriting that acceptance with a
/// rejection would leave the miner and the other signers with divergent views
/// of this signer's vote, depending on which of the two responses each of them
/// observed (the miner keeps the acceptance, since approvals are sticky, while
/// a signer that only saw the rejection would count it toward the rejection
/// threshold). Resending the prior acceptance is also what actually unsticks
/// the miner: it is re-proposing precisely because it never heard the
/// acceptance.
///
/// Test Setup:
/// Five signers with block_proposal_max_age_secs = 30, one miner with a 15s
/// rejection timeout.
///
/// Test Execution:
/// 1. Suppress the signers' acceptance broadcasts (note that this testing hook
///    suppresses acceptances only -- a rejection would still be broadcast), so
///    the signers validate and locally accept block N while the miner hears
///    nothing and stays in its resend loop.
/// 2. Hold that state for > 30s so block N's proposal goes stale, then let the
///    acceptances flow again.
/// 3. The miner re-sends the stale proposal, and every signer resends its
///    acceptance instead of rejecting it as too old.
///
/// Test Assertion:
/// - All signers respond to the stale proposal with an acceptance.
/// - No signer ever rejects block N (in particular, not with ProposalTooOld).
/// - The original block N -- same hash, same old header timestamp -- is the
///   block that advances the tip.
fn stale_proposal_of_accepted_block_resends_acceptance() {
```
