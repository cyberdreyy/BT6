### Title
Signer signs a block whose tenure canonicity is verified only once at `validate()`-time and never re-checked before signing - ([File: stackslib/src/net/api/postblock_proposal.rs])

### Summary
`NakamotoBlockProposal::check_block_has_valid_tenure` checks tenure canonicity against a `SortitionHandleConn` opened once at the start of `validate()`, and this is the *only* burnchain-canonicity check performed for the proposal's own tenure in the entire signer pipeline. Neither `check_block_against_signer_db_state` (re-run at validate-ok and at pre-commit threshold) nor the pre-commit conflict guard re-verify that the block's own tenure is still on the canonical Bitcoin fork before a signature is produced.

### Finding Description
`check_block_has_valid_tenure` is called once, early in `validate()`, against a `db_handle` derived from `sort_tip` computed via `NakamotoChainState::get_block_burn_view` at the moment the HTTP request begins processing: [1](#0-0) 

That same `sortdb`/`db_handle` snapshot is then reused for the rest of `validate()`, including static validation and transaction replay, which can run for up to the configured execution/timeout budgets before returning `BlockValidateOk`.

On the signer side, `handle_block_validate_ok` only re-runs `check_block_against_signer_db_state`, which checks chain-length/tenure-confirmation via `check_tenure_change_confirms_parent`/`check_latest_block_in_tenure`, not burnchain canonicity: [2](#0-1) 

Per `docs/signer-flows.md` (section 7), this check "answers 'does this block confirm the tip we expect?'" and is unrelated to canonicity: [3](#0-2) 

At the pre-commit threshold (section 5 of the same doc), the only canonicity check performed (`get_sortition_by_burn_hash` inside `conflict_still_blocks`/`reorg_permit_stands`) applies to *already-signed conflicting blocks at the same height*, not to the proposal's own tenure: [4](#0-3) 

If no other block happens to conflict at that height, this guard never fires, and the block's own tenure canonicity is simply never re-asked between the moment `validate()` opened its sortition snapshot and the moment `mark_locally_accepted`/signature broadcast occurs. This matches the equality claimed in the prompt: CANONICITY is checked once at validation-start time, not at signing time.

### Impact Explanation
This breaks the canonicity safety property: a signer can produce a valid ECDSA signature over a block whose tenure's sortition is no longer on the canonical Bitcoin fork by the time the signature is emitted, because the only canonicity gate (`check_block_has_valid_tenure`) is checked against a point-in-time snapshot inside `validate()` and never re-verified in `check_block_against_signer_db_state` or the pre-commit conflict guard, which only checks conflicting siblings, not the proposal's own tenure. This is a Critical-class finding per the stated impact bar (signature over a non-canonical tenure).

### Likelihood Explanation
The precondition is a burnchain reorg occurring during the window between `validate()`'s canonicity check and the point of signing (bounded by `block_proposal_validation_timeout_secs` plus queueing/threshold-accumulation time). This does not require attacker control over Bitcoin — natural reorgs, or a miner timing their proposal submission around a contested tip, both suffice; the attacker only needs one miner slot to produce the proposal and normal gossip to have it propagate, matching the stated unprivileged threat model. It is repeatable any time a reorg window overlaps the validation/threshold-accumulation period and no conflicting sibling block happens to already be recorded and signed.

### Recommendation
Re-verify `check_block_has_valid_tenure` (or an equivalent canonicity check against the *current* sortition tip) inside `check_block_against_signer_db_state`, both at validate-ok time and immediately before a signature is emitted at the pre-commit threshold, independent of whether a conflicting sibling block exists.

### Proof of Concept
Rust test plan (stacks-signer, `v0::signer` unit tests):
1. Mock `StacksClient`/sortdb state so that at t0 the proposal's `consensus_hash` resolves to a canonical sortition (`has_consensus_hash` == true).
2. Drive `Signer::handle_block_validate_ok` with a `BlockValidateOk` for this proposal, asserting `check_block_against_signer_db_state` returns `None` and the block moves to `PreCommitted`.
3. Before pre-commit threshold is reached, mutate the mocked client/sortdb to make that same `consensus_hash` non-canonical (simulate reorg), with no conflicting sibling block recorded in `signerdb`.
4. Deliver enough `BlockPreCommit` messages to cross the 70% threshold and call `handle_block_pre_commit`.
5. Assert (currently failing) that the signer refuses to sign / calls `mark_locally_rejected` instead of `mark_locally_accepted`, because the tenure is no longer canonical — showing that today's code proceeds to `SIGN` regardless, since `check_block_against_signer_db_state` never re-queries canonicity and `get_signed_conflicts`/`reorg_permit_stands` only look at conflicting siblings.

### Citations

**File:** stackslib/src/net/api/postblock_proposal.rs (L587-602)
```rust
        let burn_view_consensus_hash =
            NakamotoChainState::get_block_burn_view(sortdb, &self.block, &parent_stacks_header)?;
        let sort_tip =
            SortitionDB::get_block_snapshot_consensus(sortdb.conn(), &burn_view_consensus_hash)?
                .ok_or_else(|| BlockValidateRejectReason {
                    reason_code: ValidateRejectCode::NoSuchTenure,
                    reason: "Failed to find sortition for block tenure".to_string(),
                    failed_txid: None,
                })?;

        let burn_dbconn: SortitionHandleConn = sortdb.index_handle(&sort_tip.sortition_id);
        let db_handle = sortdb.index_handle(&sort_tip.sortition_id);

        // (For the signer)
        // Verify that the block's tenure is on the canonical sortition history
        Self::check_block_has_valid_tenure(&db_handle, &self.block.header.consensus_hash)?;
```

**File:** stacks-signer/src/v0/signer.rs (L1946-1975)
```rust
        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
        {
            // The signer db state has changed. We no longer view this block as valid. Override the validation response.
            if let Err(e) = block_info.mark_locally_rejected() {
                if !block_info.has_reached_consensus() {
                    warn!("{self}: Failed to mark block as locally rejected: {e:?}");
                }
            };
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.handle_block_rejection(&block_rejection, sortition_state);
            self.send_block_response(&block_info.block, block_rejection.into());
        } else {
            if let Err(e) = block_info.mark_pre_committed() {
                // The block may have reached enough signatures before we validated the block so should fail to mark pre-committed
                // but still call to make sure the timestamps and validity are updated correctly.
                if !block_info.has_reached_consensus()
                    && block_info.state != BlockState::LocallyAccepted
                {
                    warn!("{self}: Failed to mark block as approved: {e:?}",);
                    return;
                }
            }

            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.send_block_pre_commit(signer_signature_hash.clone());
```

**File:** docs/signer-flows.md (L248-264)
```markdown
    TH -- yes --> RECHECK{"chainstate checks still pass?<br/>check_block_against_signer_db_state<br/>→ section 7"}
    RECHECK -- no --> REJ["mark_locally_rejected,<br/>handle_block_rejection,<br/>broadcast rejection"]:::bad
    RECHECK -- yes --> CONF["signed conflicts at height ≥ h,<br/>in ANY tenure<br/>get_signed_conflicts"]
    CONF --> PERM{"covered by a reorg permit whose<br/>permitting sortition is still canonical?<br/>reorg_permit_stands"}
    PERM -- yes --> EXCL(["excluded — our signature must not<br/>block a replacement we sanctioned"]):::good
    PERM -- no --> FRESH{"any of them still fresh?<br/>last_endorsed > cutoff"}
    FRESH -- yes --> SORT{"conflict_still_blocks, question 1:<br/>is its tenure's sortition still on the<br/>canonical burn chain?<br/>get_sortition_by_burn_hash"}
    SORT -- "404, with the node's burnchain tip<br/>at or past the burn block — a fork<br/>orphaned the tenure" --> OWN
    SORT -- "canonical, or we never<br/>saved its burn block" --> LIVE{"question 2: does the node's chain<br/>still reach the block itself?<br/>get_tenure_tip(its tenure)"}
    SORT -- "could not ask, or 404 with the<br/>node's tip still below the burn block" --> HOLD1
    LIVE -- "yes — real chain state" --> HOLD1["refuse to sign for now<br/>(may sign once conflict is stale)"]:::hold
    LIVE -- "no, and it was<br/>globally accepted" --> OWN
    LIVE -- "no, only locally accepted<br/>— but above this height" --> OWN
    LIVE -- "no, only locally accepted<br/>and a sibling at this height" --> HOLD1
    LIVE -- "could not ask" --> HOLD1
    FRESH -- "no — all stale" --> OWN{"a conflict in this block's<br/>OWN tenure?"}
    OWN -- yes --> TIP{"own tenure confirmed<br/>at ≥ this height?<br/>get_tenure_tip(own tenure)"}
```

**File:** docs/signer-flows.md (L391-398)
```markdown
`check_latest_block_in_tenure` answers "does this block confirm the tip we
expect?" and it runs in three places: at proposal arrival (inside
`check_proposal`), at validate-ok, and at the moment of signing. _Which_ tenure
it is asked about depends on the block: a tenure-change block is checked against
its **parent** tenure, every other block against its **own**. Never both. The
pivotal helper is `get_tenure_last_block_info`, which considers only blocks that
carry a signature (`get_last_signed_block`): a pre-commit never vetoes anything,
it only counts as miner activity.
```
