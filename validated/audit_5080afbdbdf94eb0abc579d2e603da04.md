Based on my investigation, I found a concrete finding: the block-proposal validator hardcodes `DEFAULT_MAX_TENURE_BYTES` when rebuilding the proposed block for validation, rather than using the operator/miner's configured `max_tenure_bytes`, and this cap is only enforced through the *builder's own re-mining* accumulator, not as an independent post-hoc check against the proposal's actual size.

### Title
Signer/node accepts oversized tenures because block-proposal validation re-derives the tenure-size cap from a hardcoded default instead of the configured limit - (File: stackslib/src/net/api/postblock_proposal.rs)

### Summary
`NakamotoBlockProposal::validate`, the function invoked by the node's `/v3/block_proposal` endpoint that every signer relies on before signing, constructs its `NakamotoBlockBuilder` with `u64::from(DEFAULT_MAX_TENURE_BYTES)` [1](#0-0) , not with the node operator's actual `max_tenure_bytes` configuration value that is threaded through elsewhere (e.g. `stackslib/src/config/mod.rs` `make_block_builder_settings` and `MinerConfigFile::max_tenure_bytes`) [2](#0-1) . The only place tenure size is actually enforced is inside `NakamotoBlockBuilder::try_mine_tx_with_len`, which sums `bytes_so_far` against `self.max_tenure_bytes` and the parent's own recorded `total_tenure_size` — but this check is entirely skipped when `self.parent_header` is `None` [3](#0-2) .

### Finding Description
This is directly analogous to the Glance `image_size_cap` bug class: a size-cap configuration option exists, but one enforcement code path (here, the signer-facing block-validation path) fails to honor the actual configured value and instead re-derives an unrelated default, so the value validators see does not match the value the equality is supposed to protect ("signed size == validated size == miner's configured cap"). A signer trusts that `stacks-node`'s `/v3/block_proposal` response (`BlockValidateOk.size`) means "this block/tenure fits within the operator-configured cap." But because `validate()` substitutes `DEFAULT_MAX_TENURE_BYTES` for the real `max_tenure_bytes`, a validating node with a smaller configured cap will validate (and thus a signer will sign) a tenure that exceeds that operator's actual configured budget, silently breaking the equality between "what the config says should be capped" and "what was actually checked."

Additionally, even the constant that *is* used is bypassed entirely when `parent_header` is `None` in `try_mine_tx_with_len` [3](#0-2) , meaning the accumulated tenure-size check is conditional and not unconditionally enforced across all validation call sites.

### Impact Explanation
This does not reach the "Critical" bar under the stated rubric (it does not let a signer sign a non-canonical/invalid/conflicting block relative to consensus-critical fields, nor recount a rejection as an acceptance, nor produce a cross-context-valid signature) — chain_length continuity, tx replay, cost limits, and parent/tenure checks are all still enforced independently in `validate()` [4](#0-3) . The consequence is bounded to a resource/DoS-style deviation between configured and enforced tenure-size caps (disk/bandwidth growth beyond what an operator configured), which is the same class of harm as the original Glance CVE (disk consumption from an unenforced size cap), but here it is a discrepancy between configured value and default value rather than a fully unenforced cap.

### Likelihood Explanation
Any single miner (one slot) building a large tenure can trigger this without needing majority collusion: they only need to submit a tenure whose size falls between the operator's real `max_tenure_bytes` and `DEFAULT_MAX_TENURE_BYTES`. This requires no signer collusion, no auth token, and no majority — matching the "one-slot miner" threat model in scope.

### Recommendation
Thread the actual configured `max_tenure_bytes` (from `ConnectionOptions`/`MinerConfigFile`) into `NakamotoBlockProposal::validate`'s `NakamotoBlockBuilder::new` call instead of `DEFAULT_MAX_TENURE_BYTES`, and make the size-cap check in `try_mine_tx_with_len` unconditional (not skipped when `parent_header` is `None`).

### Proof of Concept
1. Node operator configures a smaller-than-default `max_tenure_bytes` (e.g. via `miner.max_tenure_bytes` in `stacks-node` config) [5](#0-4) .
2. A miner proposes a tenure whose accumulated size exceeds this configured value but stays under `DEFAULT_MAX_TENURE_BYTES`.
3. `NakamotoBlockProposal::validate` is invoked via `/v3/block_proposal`, builds the block using `DEFAULT_MAX_TENURE_BYTES` [1](#0-0) , and returns `BlockValidateOk` because the oversized tenure fits under the hardcoded default.
4. Signers, trusting this `Ok` response, sign the block per `stacks-signer/src/v0/signer.rs` block-validation-response handling [6](#0-5) , even though the tenure violates the operator's actual configured cap.

Note: I could not fully trace how (or whether) `ConnectionOptions.block_proposal_*` fields carry a distinct `max_tenure_bytes` field separate from `DEFAULT_MAX_TENURE_BYTES` at the `spawn_validation_thread` call site (only `timeout_secs`, `max_tx_execution_time_secs`, `max_tx_analysis_time_secs`, `max_tx_mem_bytes` are threaded there) [7](#0-6) , so I cannot rule out that this is an intentional design choice (validation may deliberately use a fixed ceiling rather than the local miner's own tuning parameter) rather than an oversight. This uncertainty should be verified against the intended design before treating it as a confirmed vulnerability.

### Citations

**File:** stackslib/src/net/api/postblock_proposal.rs (L348-372)
```rust
    fn spawn_validation_thread(
        self,
        sortdb: SortitionDB,
        mut chainstate: StacksChainState,
        receiver: Box<dyn ProposalCallbackReceiver>,
        connection_opts: &ConnectionOptions,
    ) -> Result<JoinHandle<()>, std::io::Error> {
        let timeout_secs = connection_opts.block_proposal_validation_timeout_secs;
        let max_tx_execution_time_secs = connection_opts.block_proposal_max_tx_execution_time_secs;
        let max_tx_analysis_time_secs = connection_opts.block_proposal_max_tx_analysis_time_secs;
        let max_tx_mem_bytes = connection_opts.block_proposal_max_tx_mem_bytes;
        let auth_token = connection_opts.auth_token.clone();
        thread::Builder::new()
            .name("block-proposal".into())
            .spawn(move || {
                let result = self
                    .validate(
                        &sortdb,
                        &mut chainstate,
                        timeout_secs,
                        max_tx_execution_time_secs,
                        max_tx_analysis_time_secs,
                        max_tx_mem_bytes,
                        auth_token,
                    )
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L671-685)
```rust
        if self.block.header.chain_length
            != parent_stacks_header.stacks_block_height.saturating_add(1)
        {
            warn!(
                "Rejected block proposal";
                "reason" => "Block height is non-contiguous with parent",
                "block_height" => self.block.header.chain_length,
                "parent_block_height" => parent_stacks_header.stacks_block_height,
            );
            return Err(BlockValidateRejectReason {
                reason_code: ValidateRejectCode::InvalidBlock,
                reason: "Block height is non-contiguous with parent".into(),
                failed_txid: None,
            });
        }
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L713-724)
```rust
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
```

**File:** stackslib/src/config/mod.rs (L1344-1349)
```rust
            max_execution_time: Some(Duration::from_secs(miner_config.max_execution_time_secs)),
            max_analysis_time: Some(Duration::from_secs(miner_config.max_analysis_time_secs)),
            max_tenure_bytes: miner_config.max_tenure_bytes,
            temporarily_excluded_txids: HashSet::new(),
            max_assembly_mem_bytes: miner_config.max_assembly_mem_bytes,
        }
```

**File:** stackslib/src/config/mod.rs (L4488-4488)
```rust
    pub max_tenure_bytes: Option<u64>,
```

**File:** stackslib/src/chainstate/nakamoto/miner.rs (L826-838)
```rust
        if let Some(parent_header) = &self.parent_header {
            let mut total_tenure_size = self.bytes_so_far + tx_len;

            // if we are in the same tenure of the parent, accumulate the parent total_tenure_size
            // note that total_tenure_size is reset whenever a new tenure extend happens
            if parent_header.consensus_hash == self.header.consensus_hash {
                total_tenure_size += parent_header.total_tenure_size;
            }

            if total_tenure_size >= self.max_tenure_bytes {
                return TransactionResult::skipped_due_to_error(tx, Error::TenureTooBigError);
            }
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1670-1685)
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
```
