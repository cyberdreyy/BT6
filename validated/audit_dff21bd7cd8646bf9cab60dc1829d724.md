This is now clear enough to conclude. The claimed wedge is directly falsified by two independent guards:

1. **Node-side validation always resolves parentage authoritatively.** `NakamotoBlockProposal::validate` calls `check_block_has_valid_parent`, which explicitly rejects with `ValidateRejectCode::UnknownParent` / `InvalidParentBlock` when the parent header cannot be found or does not correspond to the highest block in its tenure via the SortitionDB, not via gossip-dependent state. [1](#0-0) [2](#0-1) 

2. **The signer has an explicit timeout/fallback before submitting to the node.** `submit_block_for_validation` in `stacks-signer/src/v0/signer.rs` checks `maybe_processed_parent`; if the parent hasn't yet been observed as processed, it parks the proposal via `insert_pending_block_validation` and retries — but only until `proposal_wait_for_parent_time` elapses, after which it submits the proposal to the node anyway, letting the node's authoritative check (above) resolve or reject it. [3](#0-2) 

This is exactly the "asynchronous-validation" pattern already covered by existing test infrastructure (`stacks-node/src/tests/signer/v0/reprocess_block_proposals.rs`, which specifically tests the "Bitcoin block not yet processed" race and confirms the signer reconsiders and the node tip still advances), and by `stacks-node/src/tests/signer/v0/mod.rs` asserting a dangling/unresolvable parent produces `ValidateRejectCode::UnknownParent` rather than indefinite pending. [4](#0-3) [5](#0-4) 

So there is no perpetual "pending/awaiting parent" state: a dangling parent is bounded by `proposal_wait_for_parent_time` (submit-anyway fallback) and, on the node side, is definitively rejected (not stalled) via `UnknownParent`/`InvalidParentBlock` derived from `SortitionDB`/chainstate lookups — precisely the "re-derive canonicity from the sortition DB" fallback the question claims doesn't exist. A subsequent valid proposal at height H+1 from the legitimate miner is evaluated independently through `handle_block_proposal` → `check_proposal` and is not blocked by an unrelated prior proposal's pending/rejected state; `submitted_block_proposal` is a single in-flight slot with its own timeout (`check_submitted_block_proposal`), and additional proposals queue via `insert_pending_block_validation`/`check_pending_block_validations` rather than deadlocking. [6](#0-5) 

#No vulnerability found for this question.

### Citations

**File:** stackslib/src/net/api/postblock_proposal.rs (L480-524)
```rust
    pub(crate) fn check_block_has_valid_parent(
        chainstate: &StacksChainState,
        sortdb: &SortitionDB,
        block: &NakamotoBlock,
    ) -> Result<(), BlockValidateRejectReason> {
        let is_tenure_start =
            block
                .is_wellformed_tenure_start_block()
                .map_err(|_| BlockValidateRejectReason {
                    reason_code: ValidateRejectCode::InvalidBlock,
                    reason: "Block is not well-formed".into(),
                    failed_txid: None,
                })?;

        if !is_tenure_start {
            // this is a well-formed block that is not the start of a tenure, so it must build
            // atop an existing block in its tenure.
            Self::check_block_builds_on_highest_block_in_tenure(
                chainstate,
                sortdb,
                &block.header.consensus_hash,
                &block.header.parent_block_id,
            )?;
        } else {
            // this is a tenure-start block, so it must build atop a parent which has the
            // highest height in the *previous* tenure.
            let parent_header = NakamotoChainState::get_block_header(
                chainstate.db(),
                &block.header.parent_block_id,
            )?
            .ok_or_else(|| BlockValidateRejectReason {
                reason_code: ValidateRejectCode::UnknownParent,
                reason: "No parent block".into(),
                failed_txid: None,
            })?;

            Self::check_block_builds_on_highest_block_in_tenure(
                chainstate,
                sortdb,
                &parent_header.consensus_hash,
                &block.header.parent_block_id,
            )?;
        }
        Ok(())
    }
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L577-585)
```rust
        let parent_stacks_header = NakamotoChainState::get_block_header(
            chainstate.db(),
            &self.block.header.parent_block_id,
        )?
        .ok_or_else(|| BlockValidateRejectReason {
            reason_code: ValidateRejectCode::UnknownParent,
            reason: "Unknown parent block".into(),
            failed_txid: None,
        })?;
```

**File:** stacks-signer/src/v0/signer.rs (L2082-2112)
```rust
    /// Check if we can submit a block validation, and do so if we have pending block proposals
    fn check_pending_block_validations(&mut self, stacks_client: &StacksClient) {
        // if we're already waiting on a submitted block proposal, we cannot submit yet.
        if self.submitted_block_proposal.is_some() {
            return;
        }

        let (signer_sig_hash, insert_ts) =
            match self.signer_db.get_and_remove_pending_block_validation() {
                Ok(Some(x)) => x,
                Ok(None) => {
                    return;
                }
                Err(e) => {
                    warn!("{self}: Failed to get pending block validation: {e:?}");
                    return;
                }
            };

        info!("{self}: Found a pending block validation: {signer_sig_hash:?}");
        match self.signer_db.block_lookup(&signer_sig_hash) {
            Ok(Some(block_info)) => {
                self.submit_block_for_validation(stacks_client, &block_info.block, insert_ts);
            }
            Ok(None) => {
                // This should never happen
                error!("{self}: Pending block validation not found in DB: {signer_sig_hash:?}");
            }
            Err(e) => error!("{self}: Failed to get block info: {e:?}"),
        }
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2585-2612)
```rust
    /// Submit a block for validation, and mark it as pending if the node
    /// is busy with a previous request.
    fn submit_block_for_validation(
        &mut self,
        stacks_client: &StacksClient,
        block: &NakamotoBlock,
        added_epoch_time: u64,
    ) {
        let signer_signature_hash = block.header.signer_signature_hash();
        if !self.maybe_processed_parent(stacks_client, block) {
            let time_elapsed = get_epoch_time_secs().saturating_sub(added_epoch_time);
            if Duration::from_secs(time_elapsed)
                < self.proposal_config.proposal_wait_for_parent_time
            {
                info!("{self}: Have not processed parent of block proposal yet, inserting pending block validation and will try again later";
                        "signer_signature_hash" => %signer_signature_hash,
                        "parent_block_id" => %block.header.parent_block_id,
                );
                self.signer_db
                    .insert_pending_block_validation(&signer_signature_hash, added_epoch_time)
                    .unwrap_or_else(|e| {
                        warn!("{self}: Failed to insert pending block validation: {e:?}")
                    });
                return;
            } else {
                debug!("{self}: Cannot confirm that we have processed parent, but we've waited proposal_wait_for_parent_time, will submit proposal");
            }
        }
```

**File:** stacks-node/src/tests/signer/v0/reprocess_block_proposals.rs (L30-52)
```rust
///
/// This test verifies a race condition where a signer receives a block proposal building on a Bitcoin block
/// that has not yet been fully processed by the Stacks node. The signer should reconsider the block
/// proposal after the Bitcoin block is processed, allowing it to validate against the correct state
///
/// Test Setup:
/// - Distribute signers across two miners (3 on miner 1, 2 on miner 2)
///
/// Test Execution:
/// 1. Propose a block to all signers
/// 2. Pause bitcoin block processing on the node connect to the two signers (miner 2) to simulate the condition where the block proposal is received before the Bitcoin block is fully processed
/// 3. 3 signers on miner 1 issue pre-commits
/// 4. 2 signers on miner 2 issue a rejection due to the missing Bitcoin block
/// 5. Resume Bitcoin block processing
/// 6. Confirm the two miners on miner 2 reconsider the block proposal and issue pre-commits
/// 7. Confirm the block is accepted the node advances its tip.
///
/// Test Assertion:
/// The two signers issue rejections to the proposal
/// The two signers then reconsider the proposal after Bitcoin block processing is resumed issue pre-commits
/// The node tip advances after the block is reconsidered and accepted
fn signers_reprocess_bitcoin_block_not_found_proposals() {
    if env::var("BITCOIND_TEST") != Ok("1".into()) {
```

**File:** stacks-node/src/tests/signer/v0/mod.rs (L2712-2717)
```rust
    let reject = signer_test
        .wait_for_validate_reject_response(short_timeout.as_secs(), &block_signer_signature_hash_2);
    assert!(matches!(
        reject.reason_code,
        ValidateRejectCode::UnknownParent
    ));
```
