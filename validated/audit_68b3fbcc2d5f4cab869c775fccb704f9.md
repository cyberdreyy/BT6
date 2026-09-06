### Title
`SortitionData::check_latest_block_in_tenure` fail-opens on *any* `ClientError` from `get_tenure_tip`, letting a signer sign a stale/conflicting block at the pre-commit re-check ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`check_latest_block_in_tenure` — the chainstate check that guards both block-proposal acceptance and the final signature at the pre-commit threshold — treats *every* error returned by `client.get_tenure_tip(tenure_id)` as "assume the proposal is higher than the tenure tip," unconditionally returning `Ok(true)` (check passes). This is analogous to the reported bug class: a validation/verification step is disabled wholesale on any failure rather than being narrowly scoped to a specific, provably safe condition.

### Finding Description
`check_latest_block_in_tenure` is the shared re-check used at three points in the signer's flow (proposal arrival, validate-ok, and — critically — at the moment the pre-commit threshold is reached and a signature is about to be produced), per `docs/signer-flows.md` section 7 and the call sites in `stacks-signer/src/v0/signer.rs` (`check_block_against_signer_db_state`, lines 1803–1880) and `chainstate/v1.rs`/`v2.rs` (`validate_tenure_change_payload`, `confirms_latest_block_in_same_tenure`).

At the tail of the function: [1](#0-0) 

Any `Err` returned by `client.get_tenure_tip(tenure_id)` — not merely a connection failure — causes the function to `return Ok(true)` unconditionally, i.e. the "does this block still confirm the tip we expect" safety check is treated as passed. `ClientError` is a broad enum covering many failure modes beyond unreachability: malformed/unexpected response format, deserialization/decoding errors, HTTP status failures, retry-timeout, etc.: [2](#0-1) 

The comment justifying this ("safe because the stacks-node proposal endpoint is the backstop") is only true at proposal-arrival time, where the block is later resubmitted to `/v3/block_proposal` for full validation. It is **not** true at the two later call sites:

1. **Validate-ok recheck** (`handle_block_validate_ok` → `check_block_against_signer_db_state`, `stacks-signer/src/v0/signer.rs:1946-1947`): this recheck exists specifically because the node already validated the block and the signer db state may have drifted since; if `get_tenure_tip` errors here, the "backstop" (fresh node validation) does not run again — the check simply passes.
2. **Pre-commit-threshold signing recheck** (`handle_block_pre_commit`, `stacks-signer/src/v0/signer.rs:1345-1346`): this is described in `docs/signer-flows.md` (section 5) as the last re-check before the "one irreversible act" — placing a signature — specifically to catch the case where, between validation and reaching 70% pre-commit weight, the signer has since signed a conflicting/higher block in the same tenure elsewhere. If the RPC call to `get_tenure_tip` fails for *any* reason at this exact moment (a decode error, an HTTP 5xx from the node, a transient timeout, a malformed body), the function fails open and returns `true`, and the signer proceeds to place its signature (`SIGN: mark_locally_accepted, handle_block_signature, broadcast acceptance`) without confirming that the tenure tip it's building against is actually still valid.

A one-slot miner (plus gossip) can influence timing of proposals/pre-commits and thus the exact moment at which this recheck executes; combined with any transient node/RPC issue (which is common and not attacker-controlled certainty, but a real, reachable failure mode this code path explicitly and knowingly tolerates), this converts what is documented as a safety-critical recheck into a check that silently no-ops on error instead of refusing to sign.

### Impact Explanation
This breaks the "signed vs validated" / "approved-parent vs canonical" equality that the pre-commit recheck exists to enforce: a signer can place its signature on a block whose parent-tenure confirmation could not actually be verified, at exactly the point (post-threshold, pre-signature) where the design explicitly says the world must be re-checked before the irreversible signature leaves the box. In the worst case this contributes toward a signer signing a block it should have rejected as no longer confirming the expected tenure tip (a step toward a conflicting/non-canonical signature), which the report rules classify under "Critical" (signer signing an invalid/non-canonical/conflicting block). At minimum it is a systemic weakening of a liveness/safety guard into best-effort logging rather than an enforced rejection.

### Likelihood Explanation
The vulnerable branch is reached on *any* error from a single RPC call and no other guard exists as a backstop at this call site (the very reason the recheck exists is that no fresher validation occurs afterward). It does not require majority collusion or key compromise — only a transient RPC/node condition coinciding with the pre-commit-threshold moment, which is a normal-operations failure mode the code explicitly anticipates (it has a warn-log path for it) rather than an exotic one. This lowers likelihood only in that it needs an actual RPC failure at that instant rather than being fully attacker-triggerable at will, so it is assessed as Medium-High likelihood rather than certain-on-demand.

### Recommendation
At minimum, distinguish between the proposal-arrival call site (where the node-side `/v3/block_proposal` validation is a genuine backstop) and the validate-ok/pre-commit-signing call sites (where it is not). For the latter two, an error from `get_tenure_tip` should not fail open to `Ok(true)`; it should propagate as an error (as `ClientError`/`SignerChainstateError` already supports) so the caller treats it as `ConnectivityIssues` and withholds/rejects rather than signs, consistent with how errors are already surfaced elsewhere in `check_block_against_signer_db_state`.

### Proof of Concept
Conceptual PoC (cannot be executed here, but traceable in code):
1. A block reaches the ≥70% pre-commit weight threshold, entering `handle_block_pre_commit` (`stacks-signer/src/v0/signer.rs:1250`).
2. `check_block_against_signer_db_state` invokes `SortitionData::check_latest_block_in_tenure` (line 1843).
3. Force/observe `client.get_tenure_tip(tenure_id)` to return any `ClientError` (e.g. a malformed JSON body, an HTTP error status, or a timed-out request) at this instant — a normal degraded-network/node condition.
4. `check_latest_block_in_tenure` (chainstate/mod.rs:450-461) logs a warning and returns `Ok(true)` instead of propagating the error.
5. `check_block_against_signer_db_state` returns `None` (check "passed"), and `handle_block_pre_commit` proceeds to `mark_locally_accepted` / `handle_block_signature`, producing the signer's signature without having actually confirmed the tenure tip. [3](#0-2) [4](#0-3)

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L366-461)
```rust
    /// Check whether or not `block` is higher than the highest block in `tenure_id`.
    ///  returns `Ok(true)` if `block` is higher, `Ok(false)` if not.
    ///
    /// If we can't look up `tenure_id`, assume `block` is higher.
    /// This assumption is safe because this proposal ultimately must be passed
    /// to the `stacks-node` for proposal processing: so, if we pass the block
    /// height check here, we are relying on the `stacks-node` proposal endpoint
    /// to do the validation on the chainstate data that it has.
    ///
    /// This updates the activity timer for the miner of `block`.
    pub fn check_latest_block_in_tenure(
        tenure_id: &ConsensusHash,
        block: &NakamotoBlock,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        tenure_last_block_proposal_timeout: Duration,
        reorg_attempts_activity_timeout: Duration,
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

        // A block we have only pre-committed to must NOT veto this proposal, but, similar to above
        // this should still count as activity for the miner.
        let last_accepted_block = signer_db
            .get_last_accepted_block(tenure_id)
            .map_err(|e| ClientError::InvalidResponse(e.to_string()))?;
        if let Some(info) = last_accepted_block {
            let is_fresh_pre_commit = info.state == BlockState::PreCommitted
                && info.approved_time.is_some_and(|approved_time| {
                    approved_time.saturating_add(tenure_last_block_proposal_timeout.as_secs())
                        > get_epoch_time_secs()
                });
            if is_fresh_pre_commit && block.header.chain_length <= info.block.header.chain_length {
                info!(
                    "Miner's block proposal conflicts with a block we have only pre-committed to. Counting it as miner activity, but not rejecting the proposal.";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "proposed_chain_length" => block.header.chain_length,
                    "pre_committed_signer_signature_hash" => %info.block.header.signer_signature_hash(),
                    "pre_committed_chain_length" => info.block.header.chain_length,
                );
                if let Err(e) = signer_db
                    .update_last_activity_time(&block.header.consensus_hash, get_epoch_time_secs())
                {
                    warn!("Failed to update last activity time: {e}");
                }
            }
        }

        let tip = match client.get_tenure_tip(tenure_id) {
            Ok(tip) => tip.anchored_header,
            Err(e) => {
                warn!(
                    "Failed to fetch the tenure tip for the parent tenure: {e:?}. Assuming proposal is higher than the parent tenure for now.";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "parent_tenure" => %tenure_id,
                );
                return Ok(true);
            }
        };
```

**File:** stacks-signer/src/client/mod.rs (L40-90)
```rust
#[derive(thiserror::Error, Debug)]
/// Client error type
pub enum ClientError {
    /// Error for when a response's format does not match the expected structure
    #[error("Unexpected response format: {0}")]
    UnexpectedResponseFormat(String),
    /// An error occurred serializing the message
    #[error("Unable to serialize stacker-db message: {0}")]
    StackerDBSerializationError(#[from] CodecError),
    /// Failed to sign stacker-db chunk
    #[error("Failed to sign stacker-db chunk: {0}")]
    FailToSign(#[from] StackerDBError),
    /// Failed on a DBError
    #[error("SignerDB database error: {0}")]
    SignerDBError(#[from] blockstack_lib::util_lib::db::Error),
    /// Stacker-db instance rejected the chunk
    #[error("Stacker-db rejected the chunk. Reason: {0}")]
    PutChunkRejected(String),
    /// Failed to call a read only function
    #[error("Failed to call read only function. {0}")]
    ReadOnlyFailure(String),
    /// Reqwest specific error occurred
    #[error("{0}")]
    ReqwestError(#[from] reqwest::Error),
    /// Failed to build and sign a new Stacks transaction.
    #[error("Failed to generate transaction from a transaction signer: {0}")]
    TransactionGenerationFailure(String),
    /// Stacks node client request failed
    #[error("Stacks node client request failed: {0}")]
    RequestFailure(reqwest::StatusCode),
    /// Failed to serialize a Clarity value
    #[error("Failed to serialize Clarity value: {0}")]
    ClaritySerializationError(#[from] SerializationError),
    /// Failed to parse a Clarity value
    #[error("Received a malformed clarity value: {0}")]
    MalformedClarityValue(String),
    /// Backoff retry timeout
    #[error("Backoff retry timeout occurred. Stacks node may be down.")]
    RetryTimeout,
    /// Not connected
    #[error("Not connected")]
    NotConnected,
    /// Clarity type error
    #[error("Clarity error: {0}")]
    ClarityError(#[from] ClarityTypeError),
    /// Malformed reward set
    #[error("Malformed contract data: {0}")]
    MalformedContractData(String),
    /// Stacks node does not support a feature we need
    #[error("Stacks node does not support a required feature: {0}")]
    UnsupportedStacksFeature(String),
```

**File:** stacks-signer/src/v0/signer.rs (L1799-1880)
```rust
    /// WARNING: This is an incomplete check. Do NOT call this function PRIOR to check_proposal or block_proposal validation succeeds.
    ///
    /// Re-verify a block's chain length against the last signed block within signerdb.
    /// This is required in case a block has been approved since the initial checks of the block validation endpoint.
    fn check_block_against_signer_db_state(
        &mut self,
        stacks_client: &StacksClient,
        proposed_block: &NakamotoBlock,
    ) -> Option<BlockRejection> {
        let signer_signature_hash = proposed_block.header.signer_signature_hash();
        // If this is a tenure change block, ensure that it confirms the correct number of blocks from the parent tenure.
        if let Some(tenure_change) = proposed_block.get_tenure_change_tx_payload() {
            // Ensure that the tenure change block confirms the expected parent block
            match SortitionData::check_tenure_change_confirms_parent(
                tenure_change,
                proposed_block,
                &mut self.signer_db,
                stacks_client,
                self.proposal_config.tenure_last_block_proposal_timeout,
                self.proposal_config.reorg_attempts_activity_timeout,
            ) {
                Ok(true) => return None,
                Ok(false) => {
                    return Some(self.create_block_rejection(
                        RejectReason::SortitionViewMismatch,
                        proposed_block,
                    ))
                }
                Err(e) => {
                    warn!("{self}: Error checking block proposal: {e}";
                        "signer_signature_hash" => %signer_signature_hash,
                        "block_id" => %proposed_block.block_id()
                    );
                    return Some(self.create_block_rejection(
                        RejectReason::ConnectivityIssues(
                            "error checking block proposal".to_string(),
                        ),
                        proposed_block,
                    ));
                }
            }
        }

        // Ensure that the block is the last block in the chain of its current tenure.
        match SortitionData::check_latest_block_in_tenure(
            &proposed_block.header.consensus_hash,
            proposed_block,
            &mut self.signer_db,
            stacks_client,
            self.proposal_config.tenure_last_block_proposal_timeout,
            self.proposal_config.reorg_attempts_activity_timeout,
        ) {
            Ok(is_latest) => {
                if !is_latest {
                    warn!(
                        "Miner's block proposal does not confirm as many blocks as we expect";
                        "proposed_block_consensus_hash" => %proposed_block.header.consensus_hash,
                        "proposed_block_signer_signature_hash" => %signer_signature_hash,
                        "proposed_chain_length" => proposed_block.header.chain_length,
                    );
                    Some(self.create_block_rejection(
                        RejectReason::SortitionViewMismatch,
                        proposed_block,
                    ))
                } else {
                    None
                }
            }
            Err(e) => {
                warn!("{self}: Failed to check block against signer db: {e}";
                    "signer_signature_hash" => %signer_signature_hash,
                    "block_id" => %proposed_block.block_id()
                );
                Some(self.create_block_rejection(
                    RejectReason::ConnectivityIssues(
                        "failed to check block against signer db".to_string(),
                    ),
                    proposed_block,
                ))
            }
        }
    }
```
