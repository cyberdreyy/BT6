### Title
Cross-block rejection overwrite via single-column primary key on `block_rejection_signer_addrs` — (File: `stacks-signer/src/signerdb.rs`)

### Summary
The `block_rejection_signer_addrs` table is keyed only by `signer_addr`, not by `(signer_signature_hash, signer_addr)`. A signer can only ever have one rejection row in the entire database, so rejecting a second, different block silently overwrites (moves) the record of having rejected the first block. This is the same "add-without-checking-existing-membership" defect class as the reported `initializeExistingPoolsByMarket`/`closePool` bug, except here the failure runs in the opposite direction: instead of a duplicate entry that can't be cleanly removed, a single-slot key means one entry silently disappears from the *wrong* collection (the first block's tally) when a second, unrelated insert happens, corrupting the rejection-weight tally that the signer relies on for consensus bookkeeping.

### Finding Description
The table is declared with a primary key on `signer_addr` alone: [1](#0-0) 

This table backs `add_block_rejection_signer_addr` / `get_block_rejection_signer_addrs`, which are used by `store_and_process_block_rejection` in the v0 signer to tally rejection weight per block before deciding whether to call `mark_globally_rejected`: [2](#0-1) 

The existing regression test `duplicate_block_rejections` proves that an insert for the same `signer_addr` overwrites the prior row's data (there, the reason code) rather than accumulating a new row: [3](#0-2) 

That test only exercises the case where the *same* block hash is reused. Because the primary key is `signer_addr` alone (not the composite `(signer_signature_hash, signer_addr)` used correctly elsewhere, e.g. `block_signatures`/`blocks`), the identical upsert path is taken whenever the *same signer* rejects *any two different blocks* — which happens routinely in this codebase's own documented flows: a re-proposed block after a signature timeout, a sibling proposal at the same height from a forked miner, or a replacement block after a pre-committed proposal is superseded (all described in `docs/signer-flows.md` sections 3 and 5, anchored to `handle_block_pre_commit`/`should_reevaluate_block`). In every such case, the second `add_block_rejection_signer_addr` call for the same `signer_addr` but a *different* `signer_signature_hash` replaces the row, so `get_block_rejection_signer_addrs(&first_block_hash)` subsequently no longer returns that signer's address.

### Impact Explanation
This breaks the "aggregated-weight vs verified-rejects" equality that `store_and_process_block_rejection` depends on to decide `mark_globally_rejected`. When a signer's earlier rejection of block A is silently erased because they later rejected a different block B, block A's `total_reject_weight` (computed by `compute_signature_signing_weight` over `get_block_rejection_signer_addrs`) under-reports the true weight that rejected it. If A is genuinely at or just past the blocking-minority threshold, this local under-count can prevent the node from ever calling `mark_globally_rejected` on it, wedging that signer's local state machine on a block that should have been finalized as rejected — a silent, permanent loss of previously-recorded rejection evidence with no compensating mechanism (there is no analogous check-before-insert as recommended for the Clearpool bug).

### Likelihood Explanation
No majority collusion or malicious signer key is required — a single honest signer, following the documented, code-supported behavior of rejecting a stale/re-proposed/sibling block after having rejected an earlier one within the same reward cycle, is enough to trigger the overwrite. Competing/duplicate proposals at the same height are an expected occurrence in this codebase (see the pre-commit/replacement flow tested in `pre_committed_block_does_not_veto_replacement`), making the trigger condition realistic rather than contrived.

### Recommendation
Change the primary key of `block_rejection_signer_addrs` to the composite `(signer_signature_hash, signer_addr)`, matching the pattern already used correctly on `blocks` and `block_signatures`, so that a signer's rejection of one block can never overwrite the record of their rejection of a different block. A migration should be added to preserve/rebuild the table under the new key, mirroring the existing `MIGRATE_BLOCKS_TABLE_2_BLOCKS_TABLE_3` pattern.

### Proof of Concept
1. Signer S validates and rejects block A (`signer_signature_hash = hash_A`) via `store_and_process_block_rejection`, which calls `add_block_rejection_signer_addr(hash_A, S, reason)`, inserting/overwriting the row keyed by `signer_addr = S`.
2. `get_block_rejection_signer_addrs(hash_A)` returns `[(S, reason)]`; A's tallied `total_reject_weight` includes S's weight, and is one signer short of the >30% blocking threshold.
3. The miner re-proposes a competing/successor block B at the same height (a normal event per `should_reevaluate_block`/pre-commit-replacement flow). S validates and rejects B, calling `add_block_rejection_signer_addr(hash_B, S, reason)` — because the table's primary key is `signer_addr` alone, this **replaces** S's existing row, now pointing at `hash_B`.
4. `get_block_rejection_signer_addrs(hash_A)` now returns `[]` for S — S's earlier, still-valid rejection of A has vanished from A's tally.
5. If another signer's rejection of A arrives and re-triggers the tally in `store_and_process_block_rejection`, A's `total_reject_weight` is computed without S's weight, potentially keeping A just under the blocking-minority threshold and preventing this signer from ever calling `mark_globally_rejected(&A)`, even though the true aggregate rejection weight (across all signers, including S's now-lost vote) exceeded it. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** stacks-signer/src/signerdb.rs (L514-524)
```rust
static CREATE_BLOCK_REJECTION_SIGNER_ADDRS_TABLE: &str = r#"
CREATE TABLE IF NOT EXISTS block_rejection_signer_addrs (
    -- The block sighash commits to all of the stacks and burnchain state as of its parent,
    -- as well as the tenure itself so there's no need to include the reward cycle.  Just
    -- the sighash is sufficient to uniquely identify the block across all burnchain, PoX,
    -- and stacks forks.
    signer_signature_hash TEXT NOT NULL,
    -- the signer address that rejected the block
    signer_addr TEXT NOT NULL,
    PRIMARY KEY (signer_addr)
) STRICT;"#;
```

**File:** stacks-signer/src/signerdb.rs (L3192-3232)
```rust
    #[test]
    fn duplicate_block_rejections() {
        let db_path = tmp_db_path();
        let db = SignerDb::new(db_path).expect("Failed to create signer db");

        let block_id = Sha512Trunc256Sum::from_data("foo".as_bytes());
        let address = StacksAddress::burn_address(false);

        assert_eq!(
            db.get_block_rejection_signer_addrs(&block_id).unwrap(),
            vec![]
        );

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

        assert!(db
            .add_block_rejection_signer_addr(&block_id, &address, RejectReasonPrefix::InvalidMiner)
            .unwrap());
        assert_eq!(
            db.get_block_rejection_signer_addrs(&block_id).unwrap(),
            vec![(address.clone(), RejectReasonPrefix::InvalidMiner)]
        );

        assert!(!db
            .add_block_rejection_signer_addr(&block_id, &address, RejectReasonPrefix::InvalidMiner)
            .unwrap());
        assert_eq!(
            db.get_block_rejection_signer_addrs(&block_id).unwrap(),
            vec![(address, RejectReasonPrefix::InvalidMiner)]
        );
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2274-2313)
```rust
    ) {
        let block_hash = &block_info.signer_signature_hash();
        // We should still store signatures even on consensus reached blocks for auditing purposes.
        // signature is valid! store it
        match self.signer_db.add_block_rejection_signer_addr(
            block_hash,
            signer_address,
            reject_reason,
        ) {
            Err(e) => {
                warn!("{self}: Failed to save block rejection signature: {e:?}",);
            }
            Ok(false) => return, // We already have this signature, do not process it again.
            Ok(true) => (),
        }

        if block_info.has_reached_consensus() {
            // Checking the rejection signatures is pointless. We have already reached consensus on this block.
            return;
        }

        // do we have enough signatures to mark a block a globally rejected?
        // i.e. is (set-size) - (threshold) + 1 reached.
        let rejection_addrs = match self.signer_db.get_block_rejection_signer_addrs(block_hash) {
            Ok(addrs) => addrs,
            Err(e) => {
                warn!("{self}: Failed to load block rejection addresses: {e:?}.",);
                return;
            }
        };
        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
        let total_reject_weight =
            self.compute_signature_signing_weight(rejection_addrs.iter().map(|(addr, _)| addr));
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
        if total_reject_weight.saturating_add(min_weight) <= total_weight {
```
