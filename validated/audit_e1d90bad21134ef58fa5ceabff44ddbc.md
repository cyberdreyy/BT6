### Title
Unbounded transaction-replay matching loop in `validate_replay` allows a malicious signer's replay set to stall block-proposal validation before any timeout applies - ([File: stackslib/src/net/api/postblock_proposal.rs])

### Summary
`NakamotoBlockProposal::validate_replay` iterates the transaction-replay set with an inner `loop { … }` that repeatedly `pop_front()`s candidate replay transactions and executes them via `try_mine_tx_with_len` when they don't match the block's next transaction, before falling through to a `continue` and popping again [1](#0-0) . This inner loop has no wall-clock deadline check anywhere in it. Crucially, `validate_replay` is invoked from `validate()` *before* `block_deadline` (the only per-block timeout instant in the function) is even constructed [2](#0-1) , so the entire replay-matching phase runs with zero timeout enforcement.

### Finding Description
The replay set (`self.replay_txs`) passed into a `/v3/block_proposal` validation request originates from the global tx replay set that signers compute from `StateMachineUpdate` messages' `replay_transactions` field, which is broadcast over StackerDB by any individual signer [3](#0-2) . That field is bounded only by an overall serialized-message-size cap (`STATE_MACHINE_UPDATE_MAX_SIZE`) enforced in `consensus_serialize`/`consensus_deserialize` [4](#0-3) , not by a count limit, so a malicious signer can craft a message with a very large number of small transactions inside the byte cap.

When a signer submits a block proposal for validation with `validate_with_replay_tx` enabled, it forwards the negotiated global replay set to the node's `/v3/block_proposal` endpoint [5](#0-4) . On the node side, `validate()` calls `validate_replay()` first, and only afterward computes `block_deadline` for the per-tx loop that follows [2](#0-1) . Inside `validate_replay`, the inner `loop` that walks the replay set performs real transaction execution (`try_mine_tx_with_len`, unlimited resource budget: `TransactionResourceBudgets::unlimited()`) for every non-matching replay-set entry before it is dropped and the next one is tried [6](#0-5) . Because this happens before any timeout instant exists in the function, no code path can cut this phase short — unlike the main per-tx loop, which explicitly checks `Instant::now() >= block_deadline` on every iteration [7](#0-6) .

Validation runs in a dedicated per-request thread (`spawn_validation_thread`), and the node guards against concurrent proposal submissions with `is_proposal_thread_running()` [8](#0-7) , meaning only one block proposal can be validated at a time per node. If the replay-matching phase runs far longer than `block_proposal_validation_timeout_secs`, every subsequent legitimate block proposal is rejected with `TOO_MANY_REQUESTS_STATUS` while the stuck thread runs, and the signer itself will eventually time out its own submission via `check_submitted_block_proposal` and reject the block with `ConnectivityIssues` [9](#0-8) , but this happens only after the configured timeout has already elapsed on the signer side, while the node-side validation thread keeps running uninterrupted, backlogging validation for the miner's real proposals — a liveness wedge analogous to OctoPrint's tornado handler blocking on an unterminated loop, though bounded (not infinite) since `replay_txs` is finite.

### Impact Explanation
This maps to the "High" bucket: a signer/node can be wedged such that it cannot validate (and therefore cannot sign) legitimate proposals in a timely manner, because the single-threaded proposal-validation slot is occupied by an unbounded replay-set walk that ignores `block_proposal_validation_timeout_secs`. A single malicious signer broadcasting an inflated `replay_transactions` list (no majority required) can degrade or wedge validation liveness across every signer/node that adopts that global replay set, causing repeated `ConnectivityIssues` rejections and missed block-signing windows.

### Likelihood Explanation
Reaching this requires only that the attacker be a member of the signer set able to broadcast a `StateMachineUpdate` (gossip-level capability), which the threat model explicitly allows without needing a majority, the auth token, or another signer's key. The replay-set size is capped only by the message byte-size limit, not entry count, so an attacker can pack many small transactions to maximize the number of `try_mine_tx_with_len` calls executed before the set is exhausted or a legitimately-matching entry appears.

### Recommendation
Move (or duplicate) the `block_deadline`/`Instant::now()` timeout construction to before `validate_replay` is called, and check it inside the inner replay-matching `loop` on every iteration (mirroring the check already present in the main per-tx loop at lines 755-771), so a crafted replay set cannot consume validation time beyond the configured `block_proposal_validation_timeout_secs`. Additionally, consider bounding the number of entries in `StateMachineUpdateContent::replay_transactions` independent of total byte size.

### Proof of Concept
1. As a signer in the active signer set, construct and broadcast a `StateMachineUpdate` (`V1`/`V2`) whose `replay_transactions` contains a large number of syntactically valid, cheap `StacksTransaction`s that will never match the transactions in real upcoming block proposals (e.g., transfers with fabricated recipients/nonces), staying under `STATE_MACHINE_UPDATE_MAX_SIZE`.
2. Once enough honest signers adopt this as the global tx replay set (via `global_state_evaluator`), they will include it as `replay_txs` on their subsequent `submit_block_for_validation` calls to their local nodes [5](#0-4) .
3. On the node, `NakamotoBlockProposal::validate()` calls `validate_replay()` before `block_deadline` exists [2](#0-1) ; the inner loop pops and executes every mismatched replay-set entry with no timeout check [10](#0-9) .
4. Measure wall-clock time of the `/v3/block_proposal` validation thread versus `block_proposal_validation_timeout_secs`; a sufficiently large/expensive replay set demonstrates the phase running well past the configured timeout while occupying the sole proposal-validation slot (`is_proposal_thread_running`), blocking subsequent proposals during that window.

Note: I could not fully verify the exact value of `STATE_MACHINE_UPDATE_MAX_SIZE` or the precise upper bound on achievable replay-set entry count within that byte budget in the time available; a Devin session with full repository access would be needed to compute the concrete worst-case stall duration and confirm the constant's value in `libsigner/src/v0/messages.rs`.

### Citations

**File:** stackslib/src/net/api/postblock_proposal.rs (L704-731)
```rust
        let replay_tx_exhausted = self.validate_replay(
            &parent_stacks_header,
            tenure_change,
            coinbase,
            tenure_cause,
            chainstate,
            &burn_dbconn,
        )?;

        let mut builder = NakamotoBlockBuilder::new(
            &parent_stacks_header,
            &self.block.header.consensus_hash,
            self.block.header.burn_spent,
            tenure_change,
            coinbase,
            self.block.header.pox_treatment.len(),
            None,
            None,
            Some(self.block.header.timestamp),
            u64::from(DEFAULT_MAX_TENURE_BYTES),
        )?;

        let mut miner_tenure_info =
            builder.load_tenure_info(chainstate, &burn_dbconn, tenure_cause)?;
        let burn_chain_height = miner_tenure_info.burn_tip_height;
        let mut tenure_tx = builder.tenure_begin(&burn_dbconn, &mut miner_tenure_info)?;

        let block_deadline = Instant::now() + Duration::from_secs(timeout_secs);
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L755-771)
```rust
        for (i, tx) in self.block.txs.iter().enumerate() {
            // Enforce the overall block validation budget between txs. A tx
            // running over its own per-tx limit is the tx's fault and is
            // handled below; running out of overall budget is the block's
            // fault and shouldn't flag any specific tx as problematic.
            if Instant::now() >= block_deadline {
                warn!(
                    "Rejected block proposal";
                    "reason" => "Block validation timed out",
                    "next_tx_index" => i,
                );
                return Err(BlockValidateRejectReason {
                    reason: format!("Block validation timed out before tx {i} could be processed"),
                    reason_code: ValidateRejectCode::InvalidBlock,
                    failed_txid: None,
                });
            }
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L945-1038)
```rust
            loop {
                if matches!(
                    tx.payload,
                    TransactionPayload::TenureChange(..) | TransactionPayload::Coinbase(..)
                ) {
                    // Allow this to happen, tenure extend checks happen elsewhere.
                    break;
                }
                fault_injection_reject_replay_txs()?;
                let Some(replay_tx) = replay_txs.pop_front() else {
                    // During transaction replay, we expect that the block only
                    // contains transactions from the replay set. Thus, if we're here,
                    // the block contains a transaction that is not in the replay set,
                    // and we should reject the block.
                    warn!("Rejected block proposal. Block contains transactions beyond the replay set.";
                        "txid" => %tx.txid(),
                        "tx_index" => i,
                    );
                    return Err(BlockValidateRejectReason {
                        reason_code: ValidateRejectCode::InvalidTransactionReplay,
                        reason: "Block contains transactions beyond the replay set".into(),
                        failed_txid: Some(tx.txid()),
                    });
                };
                if replay_tx.txid() == tx.txid() {
                    break;
                }

                // The included tx doesn't match the next tx in the
                // replay set. Check to see if the tx is skipped because
                // it was unmineable.
                let tx_result = replay_builder.try_mine_tx_with_len(
                    &mut replay_tenure_tx,
                    &replay_tx,
                    replay_tx.tx_len(),
                    &BlockLimitFunction::NO_LIMIT_HIT,
                    &TransactionResourceBudgets::unlimited(),
                    &mut total_receipts,
                );
                match tx_result {
                    TransactionResult::Skipped(TransactionSkipped { error, .. })
                    | TransactionResult::ProcessingError(TransactionError { error, .. })
                    | TransactionResult::Problematic(TransactionProblematic { error, .. }) => {
                        // The tx wasn't able to be mined. Check the underlying error, to
                        // see if we should reject the block or allow the tx to be
                        // dropped from the replay set.

                        match error {
                            ChainError::CostOverflowError(..)
                            | ChainError::BlockTooBigError
                            | ChainError::BlockCostLimitError
                            | ChainError::ClarityError(ClarityError::CostError(..)) => {
                                // block limit reached; add tx back to replay set.
                                // BUT we know that the block should have ended at this point, so
                                // return an error.
                                let txid = replay_tx.txid();
                                replay_txs.push_front(replay_tx);

                                warn!("Rejecting block proposal. Next replay tx exceeds cost limits, so should have been in the next block.";
                                    "error" => %error,
                                    "txid" => %txid,
                                );

                                return Err(BlockValidateRejectReason {
                                    reason_code: ValidateRejectCode::InvalidTransactionReplay,
                                    reason: "Next replay tx exceeds cost limits, so should have been in the next block.".into(),
                                    failed_txid: None,
                                });
                            }
                            _ => {
                                info!("During replay block validation, allowing problematic tx to be dropped";
                                    "txid" => %replay_tx.txid(),
                                    "error" => %error,
                                );
                                // it's ok, drop it
                                continue;
                            }
                        }
                    }
                    TransactionResult::Success(_) => {
                        // Tx should have been included
                        warn!("Rejected block proposal. Block doesn't contain replay transaction that should have been included.";
                            "block_txid" => %tx.txid(),
                            "block_tx_index" => i,
                            "replay_txid" => %replay_tx.txid(),
                        );
                        return Err(BlockValidateRejectReason {
                            reason_code: ValidateRejectCode::InvalidTransactionReplay,
                            reason: "Transaction is not in the replay set".into(),
                            failed_txid: Some(tx.txid()),
                        });
                    }
                };
            }
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L1215-1221)
```rust
        let res = node.with_node_state(|network, sortdb, chainstate, _mempool, rpc_args| {
            if network.is_proposal_thread_running() {
                return Err((
                    TOO_MANY_REQUESTS_STATUS,
                    NetError::SendError("Proposal currently being evaluated".into()),
                ));
            }
```

**File:** libsigner/src/v0/messages.rs (L581-603)
```rust
    /// Version 1
    V1 {
        /// The tip burn block (i.e., the latest bitcoin block) seen by this signer
        burn_block: ConsensusHash,
        /// The tip burn block height (i.e., the latest bitcoin block) seen by this signer
        burn_block_height: u64,
        /// The signer's view of who the current miner should be (and their tenure building info)
        current_miner: StateMachineUpdateMinerState,
        /// The replay transactions
        replay_transactions: Vec<StacksTransaction>,
    },
    /// Version 2 is exactly the same as Version 1, but is used to indicate this signer is
    /// compatible with global state machine processing
    V2 {
        /// The tip burn block (i.e., the latest bitcoin block) seen by this signer
        burn_block: ConsensusHash,
        /// The tip burn block height (i.e., the latest bitcoin block) seen by this signer
        burn_block_height: u64,
        /// The signer's view of who the current miner should be (and their tenure building info)
        current_miner: StateMachineUpdateMinerState,
        /// The replay transactions
        replay_transactions: Vec<StacksTransaction>,
    },
```

**File:** libsigner/src/v0/messages.rs (L970-1012)
```rust
impl StacksMessageCodec for StateMachineUpdate {
    fn consensus_serialize<W: Write>(&self, fd: &mut W) -> Result<(), CodecError> {
        self.active_signer_protocol_version
            .consensus_serialize(fd)?;
        self.local_supported_signer_protocol_version
            .consensus_serialize(fd)?;
        let mut buffer = Vec::new();
        self.content.serialize(&mut buffer)?;
        let buff_len = u32::try_from(buffer.len())
            .map_err(|_e| CodecError::SerializeError("Message length exceeded u32".into()))?;
        if buff_len > STATE_MACHINE_UPDATE_MAX_SIZE {
            return Err(CodecError::SerializeError(format!(
                "Message length exceeded max: {STATE_MACHINE_UPDATE_MAX_SIZE}"
            )));
        }
        buff_len.consensus_serialize(fd)?;
        fd.write_all(&buffer).map_err(CodecError::WriteError)
    }

    fn consensus_deserialize<R: Read>(fd: &mut R) -> Result<Self, CodecError> {
        let active_signer_protocol_version: u64 = read_next(fd)?;
        let local_supported_signer_protocol_version: u64 = read_next(fd)?;
        let content_len: u32 = read_next(fd)?;
        if content_len > STATE_MACHINE_UPDATE_MAX_SIZE {
            return Err(CodecError::DeserializeError(format!(
                "Message length exceeded max: {STATE_MACHINE_UPDATE_MAX_SIZE}"
            )));
        }
        let buffer_len = usize::try_from(content_len)
            .expect("FATAL: cannot process signer messages when usize < u32");
        let mut buffer = vec![0u8; buffer_len];
        fd.read_exact(&mut buffer).map_err(CodecError::ReadError)?;
        let negotiated =
            active_signer_protocol_version.min(local_supported_signer_protocol_version);
        let content = StateMachineUpdateContent::deserialize(&mut buffer.as_slice(), negotiated)?;

        // We use the inbound constructor here as we need to allow for older versions
        Self::new_inbound(
            active_signer_protocol_version,
            local_supported_signer_protocol_version,
            content,
        )
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2114-2168)
```rust
    /// Check the current tracked submitted block proposal to see if it has timed out.
    /// Broadcasts a rejection and marks the block locally rejected if it has.
    fn check_submitted_block_proposal(&mut self) {
        let Some((proposal_signer_sighash, block_submission)) =
            self.submitted_block_proposal.take()
        else {
            // Nothing to check.
            return;
        };
        if block_submission.elapsed() < self.block_proposal_validation_timeout {
            // Not expired yet. Put it back!
            self.submitted_block_proposal = Some((proposal_signer_sighash, block_submission));
            return;
        }
        // For mutability reasons, we need to take the block_info out of the map and add it back after processing
        let mut block_info = match self.signer_db.block_lookup(&proposal_signer_sighash) {
            Ok(Some(block_info)) => {
                if block_info.has_reached_consensus() {
                    // The block has already reached consensus.
                    return;
                }
                block_info
            }
            Ok(None) => {
                // This is weird. If this is reached, its probably an error in code logic or the db was flushed.
                // Why are we tracking a block submission for a block we have never seen / stored before.
                error!("{self}: tracking an unknown block validation submission.";
                    "signer_signature_hash" => %proposal_signer_sighash,
                );
                return;
            }
            Err(e) => {
                error!("{self}: Failed to lookup block in signer db: {e:?}",);
                return;
            }
        };
        // We cannot determine the validity of the block, but we have not reached consensus on it yet.
        // Reject it so we aren't holding up the network because of our inaction.
        warn!(
            "{self}: Failed to receive block validation response within {} ms. Rejecting block.", self.block_proposal_validation_timeout.as_millis();
            "signer_signature_hash" => %proposal_signer_sighash,
        );
        let rejection = self.create_block_rejection(
            RejectReason::ConnectivityIssues(
                "failed to receive block validation response in time".to_string(),
            ),
            &block_info.block,
        );
        block_info.reject_reason = Some(rejection.response_data.reject_reason.clone());
        if let Err(e) = block_info.mark_locally_rejected() {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally rejected: {e:?}");
            }
        };
        self.send_block_response(&block_info.block, rejection.into());
```

**File:** stacks-signer/src/v0/signer.rs (L2613-2623)
```rust
        match stacks_client.submit_block_for_validation(
            block.clone(),
            if self.validate_with_replay_tx {
                self.global_state_evaluator
                    .get_global_tx_replay_set()
                    .unwrap_or_default()
                    .clone_as_optional()
            } else {
                None
            },
        ) {
```
