### Title
Unbounded block-proposal validation queue lets the current-tenure miner wedge a signer into a backlog of spam, delaying/denying legitimate signing - ([File: stacks-signer/src/signerdb.rs])

### Summary
The signer processes block-validation requests to the stacks-node one at a time, queuing everything else in the `block_validations_pending` table. Nothing bounds the number of distinct, not-provably-invalid proposals a single (legitimately winning) miner can enqueue before any of them reaches a signature. This mirrors the reported bug class: an actor who can cheaply enqueue many entries into an unbounded, strictly-processed-in-order queue can starve the queue's real work, here delaying a signer's ability to sign the actual canonical block in time.

### Finding Description
`handle_block_proposal` runs only cheap, local checks (`check_block_against_state`) before deciding to submit a proposal to the node for full validation or to queue it: [1](#0-0) 

If a validation is already outstanding (`submitted_block_proposal.is_some()`), the new proposal is written into the `block_validations_pending` table instead of being deduplicated or rate-limited, and the block itself is stored via `insert_block`: [2](#0-1) 

The pending table has no cap and no per-signer/per-tenure limit — it is a plain FIFO keyed by `signer_signature_hash`: [3](#0-2) 

Dequeue is strictly oldest-first, one at a time, driven only by `check_pending_block_validations`, which only pops the next validation after the previous submission resolves or times out: [4](#0-3) [5](#0-4) 

The comment in `handle_block_proposal` shows the team already recognized a storage-based DoS risk and mitigated it — but only for *provably* invalid blocks: [6](#0-5) 

The pre-queue gate that could reject duplicate/competing proposals at the same height, `check_latest_block_in_tenure` (via `get_tenure_last_block_info`/`get_last_signed_block`), only vetoes a proposal once some block in the tenure has actually been *signed*: [7](#0-6) 

Because the veto only fires after a signature exists, the currently-winning miner (a one-slot actor who legitimately controls `MinerMessages`/StackerDB gossip for the tenure) can broadcast an arbitrary number of syntactically distinct proposals (varying `timestamp`/tx set, hence distinct `signer_signature_hash`) at the same or nearby height *before* any of them is signed. Each one independently satisfies `check_block_against_state`'s "not provably invalid" bar (correct miner pubkey hash, correct consensus hash/tenure id, no problematic txs) and is queued into the unbounded `block_validations_pending` table and stored via `insert_block`.

### Impact Explanation
Every queued entry must be dequeued oldest-first and round-tripped through `/v3/block_proposal` validation before the *next* one — including the legitimate proposal for the real chain tip if it lands anywhere behind the spam. With the node-side per-request timeout (`block_proposal_validation_timeout_secs`) and the signer's own submission-timeout retry logic (`check_submitted_block_proposal`), a large enough backlog can consume the miner's entire `block_proposal_timeout` window before the genuine proposal is even submitted to the node, let alone validated and signed. This is a liveness wedge matching the "High" bucket: the signer can be kept busy chewing through spam and fail to sign a valid block in time, causing spurious miner-invalidation/tenure-extend churn or missed blocks — without needing a majority of signers, another signer's key, or the auth token.

### Likelihood Explanation
The only capability required is being the current sortition winner (a one-slot miner) able to broadcast `BlockProposal` messages over the normal miner→signer StackerDB channel — exactly the actor and channel the rules permit. Producing many distinct-hash, not-provably-invalid proposals at essentially the same height is trivial for the legitimate miner's own signing key (vary timestamp/nonce/tx selection), and no code path deduplicates, caps, or rate-limits entries into `block_validations_pending` before a signature exists in the tenure.

### Recommendation
- Cap the number of queued pending-validation entries (e.g., per tenure/consensus_hash, or per `reward_cycle`), evicting oldest entries the same way the pending pre-commit/signature/rejection response tables already do (max 3 entries per key).
- Deduplicate/limit distinct proposals per (consensus_hash, chain_length) admitted into the queue before any of them is signed, rather than gating only on already-signed blocks in `check_latest_block_in_tenure`.
- Consider prioritizing the highest-`chain_length`/freshest proposal for submission rather than strict FIFO, so a flood of stale/duplicate-height spam cannot delay the current tip's proposal.

### Proof of Concept
1. As the current tenure's winning miner, submit a normal `BlockProposal` A for height `h` and immediately, before any signer signs it, submit `k` further distinct proposals `B_1..B_k` at the same tenure/height, each with a different `timestamp` (hence unique `signer_signature_hash`) but otherwise valid header/signature.
2. Each of `B_1..B_k` passes `check_block_against_state` (miner pkh/consensus-hash checks succeed, no signed block exists yet in the tenure to trigger `check_latest_block_in_tenure`'s veto), so each is stored via `insert_block` and, once the single `submitted_block_proposal` slot is occupied, queued via `insert_pending_block_validation` (`stacks-signer/src/signerdb.rs`).
3. Observe (e.g., via `get_all_pending_block_validations` used in tests, `stacks-signer/src/signerdb.rs`) that the pending table grows to `k` entries with no eviction.
4. Because `check_pending_block_validations` dequeues oldest-first one at a time, the legitimate follow-up proposal for the real chain tip (submitted after A/B_1..B_k) is delayed behind the entire backlog, potentially past the miner's `block_proposal_timeout`, demonstrating the liveness wedge.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1670-1719)
```rust
        // Check if proposal can be rejected now if not valid against sortition view
        let block_rejection =
            self.check_block_against_state(stacks_client, sortition_state, &block_info);

        #[cfg(any(test, feature = "testing"))]
        let block_rejection =
            self.test_reject_block_proposal(block_proposal, &mut block_info, block_rejection);

        if let Some(block_rejection) = block_rejection {
            // We know proposal is invalid. Send rejection message, do not do further validation and do not store it.
            self.send_block_response(&block_info.block, block_rejection.into());
        } else {
            // Just in case check if the last block validation submission timed out.
            self.check_submitted_block_proposal();
            if self.submitted_block_proposal.is_none() {
                // We don't know if proposal is valid, submit to stacks-node for further checks and store it locally.
                info!(
                    "{self}: submitting block proposal for validation";
                    "signer_signature_hash" => %signer_signature_hash,
                    "block_id" => %block_proposal.block.block_id(),
                    "block_height" => block_proposal.block.header.chain_length,
                    "burn_height" => block_proposal.burn_height,
                );

                #[cfg(any(test, feature = "testing"))]
                self.test_stall_block_validation_submission();
                self.submit_block_for_validation(
                    stacks_client,
                    &block_proposal.block,
                    get_epoch_time_secs(),
                );
            } else {
                // Still store the block but log we can't submit it for validation. We may receive enough signatures/rejections
                // from other signers to push the proposed block into a global rejection/acceptance regardless of our participation.
                // However, we will not be able to participate beyond this until our block submission times out or we receive a response
                // from our node.
                warn!("{self}: cannot submit block proposal for validation as we are already waiting for a response for a prior submission. Inserting pending proposal.";
                    "signer_signature_hash" => signer_signature_hash.to_string(),
                );
                self.signer_db
                    .insert_pending_block_validation(&signer_signature_hash, get_epoch_time_secs())
                    .unwrap_or_else(|e| {
                        warn!("{self}: Failed to insert pending block validation: {e:?}")
                    });
            }

            // Do not store KNOWN invalid blocks as this could DOS the signer. We only store blocks that are valid or unknown.
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
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

**File:** stacks-signer/src/signerdb.rs (L623-629)
```rust
static CREATE_BLOCK_VALIDATION_PENDING_TABLE: &str = r#"
CREATE TABLE IF NOT EXISTS block_validations_pending (
    signer_signature_hash TEXT NOT NULL,
    -- the time at which the block was added to the pending table
    added_time INTEGER NOT NULL,
    PRIMARY KEY (signer_signature_hash)
) STRICT;"#;
```

**File:** stacks-signer/src/signerdb.rs (L2054-2070)
```rust
    /// Get a pending block validation, sorted by the time at which it was added to the pending table.
    /// If found, remove it from the pending table.
    pub fn get_and_remove_pending_block_validation(
        &self,
    ) -> Result<Option<(Sha512Trunc256Sum, u64)>, DBError> {
        let qry = "DELETE FROM block_validations_pending WHERE signer_signature_hash = (SELECT signer_signature_hash FROM block_validations_pending ORDER BY added_time ASC LIMIT 1) RETURNING signer_signature_hash, added_time";
        let args = params![];
        let mut stmt = self.db.prepare(qry)?;
        let result: Option<(String, i64)> = stmt
            .query_row(args, |row| Ok((row.get(0)?, row.get(1)?)))
            .optional()?;
        Ok(result.and_then(|(sighash, ts_i64)| {
            let signer_sighash = Sha512Trunc256Sum::from_hex(&sighash).ok()?;
            let ts = u64::try_from(ts_i64).ok()?;
            Some((signer_sighash, ts))
        }))
    }
```

**File:** stacks-signer/src/chainstate/mod.rs (L383-420)
```rust
    ) -> Result<bool, ClientError> {
        let last_block_info = SortitionData::get_tenure_last_block_info(
            tenure_id,
            signer_db,
            tenure_last_block_proposal_timeout,
        )?;

        if let Some(info) = last_block_info {
            // N.B. this block might not be the last globally accepted block across the network;
            // it's just the highest one in this tenure that we know about.  If this given block is
            // no higher than it, then it's definitely no higher than the last globally accepted
            // block across the network, so we can do an early rejection here.
            if block.header.chain_length <= info.block.header.chain_length {
                warn!(
                    "Miner's block proposal does not confirm as many blocks as we expect";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "proposed_chain_length" => block.header.chain_length,
                    "expected_at_least" => info.block.header.chain_length + 1,
                );
                if info.signed_group.is_none_or(|signed_time| {
                    signed_time + reorg_attempts_activity_timeout.as_secs() > get_epoch_time_secs()
                }) {
                    // Note if there is no signed_group time, this is a locally accepted block (i.e. tenure_last_block_proposal_timeout has not been exceeded).
                    // Treat any attempt to reorg a locally accepted block as valid miner activity.
                    // If the call returns a globally accepted block, check its globally accepted time against a quarter of the block_proposal_timeout
                    // to give the miner some extra buffer time to wait for its chain tip to advance
                    // The miner may just be slow, so count this invalid block proposal towards valid miner activity.
                    if let Err(e) = signer_db.update_last_activity_time(
                        &block.header.consensus_hash,
                        get_epoch_time_secs(),
                    ) {
                        warn!("Failed to update last activity time: {e}");
                    }
                }
                return Ok(false);
            }
        }
```
