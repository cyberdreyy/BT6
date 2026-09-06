## Finding

Between the moment a signer approves a block proposal (marks pre-commit) and the moment it actually signs after crossing the 70% pre-commit threshold, the miner/tenure validity checks performed at proposal time are never re-verified — only a narrow subset of tenure-confirmation checks are re-run. A miner can exploit the timing gap between validation and pre-commit-threshold-crossing to get a signer to sign a block for a miner that the signer's own state machine now considers timed-out/invalid, producing a signature on a stale/should-be-abandoned tenure that conflicts with what other (faster-timing) signers have already moved on to support.

### Title
Stale-miner-validity race between block-proposal check and pre-commit signing threshold — ([File: stacks-signer/src/v0/signer.rs])

### Summary
`check_proposal` (in `stacks-signer/src/chainstate/v1.rs` / `v2.rs`) is the only place that validates a proposal against the signer's live view of "who the current/valid miner is" — including `SortitionState::is_timed_out` (v1) / `MinerState::ActiveMiner` freshness (v2), pubkey-hash matching, and bitvec correctness. This check runs once, at `handle_block_proposal` time. [1](#0-0) [2](#0-1) 

Later, at `handle_block_validate_ok` (after node validation returns) and at `handle_block_pre_commit` (when the pre-commit threshold is crossed and the signature is actually produced), the only re-verification performed is `check_block_against_signer_db_state`, which calls just `check_tenure_change_confirms_parent`/`check_latest_block_in_tenure` — it does not re-invoke `check_proposal`, so it never re-checks miner timeout status, pubkey-hash validity, or bitvec correctness. [3](#0-2) [4](#0-3) 

This is explicitly documented as a known proposal-only gap: `docs/signer-flows.md` states that the duplicate check and the v2 wrapper's miner-pubkey/consensus-hash/bitvec/tenure-extend checks "are **not** re-run at validate-ok or at signing," relying instead on a narrower "own-tenure conflict guard" that only protects against *signed* conflicts, not against a miner that has simply timed out with no competing signature yet. [5](#0-4) 

### Finding Description
The equality that must hold is: *"the miner/tenure this signer is about to sign for is still the miner/tenure this signer currently considers valid."* This equality is established once, at proposal time (`check_proposal`), via `SortitionState::is_timed_out` (v1) which flips `miner_status` to `SortitionMinerStatus::InvalidatedBeforeFirstBlock` once `block_proposal_timeout` elapses with no signed block in the tenure: [6](#0-5) 

That timeout check is time-dependent (`std::time::SystemTime::now()`), so its result can flip from "valid" to "timed out" purely due to elapsed wall-clock time — with zero signer action required. Once a proposal has passed `check_proposal` and entered the validation → pre-commit pipeline, nothing downstream re-asks that same question. `handle_block_pre_commit` re-runs chainstate checks only through `check_block_against_signer_db_state`, and explicitly frames this re-check as being about tenure-confirmation state ("the chain and signer db state may have changed... between validation and reaching the pre-commit threshold we may have signed a block that this one would reorg"), not about miner liveness/validity: [4](#0-3) 

A single miner controls the timing of its own proposal broadcast, and validation + pre-commit gossip round-trips (network latency, node validation time, other signers' response timing) are entirely outside the miner's control but influence how much wall-clock time elapses between `check_proposal` succeeding and pre-commit threshold being crossed for any given signer. A miner (plus normal gossip propagation delay) can engineer or simply benefit from a proposal that is validated and approved for pre-commit just before `block_proposal_timeout` and reaches pre-commit-threshold crossing (and thus signature) just after — while other signers, evaluating the same wall-clock condition slightly earlier or with slightly different local activity timestamps, have already flipped to `InvalidatedBeforeFirstBlock` and fallen back to a different (prior-tenure) miner state.

### Impact Explanation
This breaks the "signed vs validated" equality: a signer can produce a valid cryptographic signature over a block from a miner it should currently consider invalid/timed-out, at the same time other signers in the set have already locally invalidated that miner and moved their state machine to support a different (fallback) tenure. This can result in signatures being split across two mutually-conflicting chain continuations (the timed-out miner's tenure vs. the fallback tenure), which is a Critical-class outcome per the given impact criteria: "a signer signing an invalid, non-canonical, or conflicting block." It can also produce a High-class liveness wedge if enough signers land on each side of the race without reaching 70% on either.

### Likelihood Explanation
This requires only the timing of a single miner's proposal (a one-slot actor) relative to gossip/validation delays that are already part of normal operation — no majority collusion, no other signer's key, and no local/auth_token access is needed. The precondition (proposal timing landing near `block_proposal_timeout`/`tenure_last_block_proposal_timeout`) is realistic under normal network jitter and is explicitly acknowledged as an unguarded gap in `docs/signer-flows.md`.

### Recommendation
Re-run the miner-validity portion of `check_proposal` (or at minimum re-check `SortitionState::is_timed_out` / `MinerState::ActiveMiner` freshness) inside `check_block_against_signer_db_state`, so that both the validate-ok recheck and the pre-commit-threshold recheck confirm the miner is still considered valid/non-timed-out at the moment of signing, not just at the moment of initial proposal evaluation.

### Proof of Concept
1. Miner M wins a sortition and its tenure's last activity timestamp is close to `block_proposal_timeout`.
2. M broadcasts `BlockProposal` P. Signer S receives it while `is_timed_out(M) == false`, so `check_proposal` passes and S submits P for node validation (`handle_block_proposal`). [7](#0-6) 
3. Validation takes long enough (node load, network latency) that by the time S's peers cross the 70% pre-commit weight for P, wall-clock time has advanced past `block_proposal_timeout` for M's tenure — meaning a fresh `check_proposal` call would now return `RejectReason::InvalidMiner`.
4. `handle_block_validate_ok` and `handle_block_pre_commit` both only call `check_block_against_signer_db_state`, which does not consult `is_timed_out`/`miner_status`, so S proceeds to `mark_locally_accepted` and signs P. [8](#0-7) 
5. Meanwhile other signers, whose local `last_activity_time` for M's tenure crossed the timeout slightly earlier, already flipped to `InvalidatedBeforeFirstBlock` and began endorsing a fallback tenure — producing a signed conflict between S's signature on P and the fallback tenure's signatures.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L52-94)
```rust
impl SortitionState {
    /// Check if the given sortition identified by its ConsensusHash has timed out based on current signed blocks
    /// and the time at which the burn block for it was first recorded in the provided signerdb
    pub fn is_timed_out(
        sortition: &ConsensusHash,
        db: &SignerDb,
        block_proposal_timeout: Duration,
    ) -> Result<bool, SignerChainstateError> {
        // If we've already signed a block in this tenure, the miner can't have timed out: we have
        // committed a signature to this tenure and must not help abandon it.
        //
        // Importantly, a block we have only pre-committed to does not count! A pre-commit carries
        // no signature, and if it never reaches the pre-commit threshold the tenure can stall
        // indefinitely. Treating it as signed here would suppress the inactivity timeout for
        // exactly the signers that pre-committed, so they could never fall back to the prior
        // miner and the tenure could never recover.
        let has_block = db.has_signed_block_in_tenure(sortition)?;
        if has_block {
            return Ok(false);
        }
        let Some(received_ts) = db.get_burn_block_receive_time_ch(sortition)? else {
            return Ok(false);
        };
        let received_time = UNIX_EPOCH + Duration::from_secs(received_ts);
        let last_activity = db
            .get_last_activity_time(sortition)?
            .map(|time| UNIX_EPOCH + Duration::from_secs(time))
            .unwrap_or(received_time);

        let Ok(elapsed) = std::time::SystemTime::now().duration_since(last_activity) else {
            return Ok(false);
        };

        if elapsed > block_proposal_timeout {
            info!(
                "Tenure miner was inactive too long and timed out";
                "tenure_ch" => %sortition,
                "elapsed_inactive" => elapsed.as_secs(),
                "config_block_proposal_timeout" => block_proposal_timeout.as_secs()
            );
        }
        Ok(elapsed > block_proposal_timeout)
    }
```

**File:** stacks-signer/src/chainstate/v1.rs (L136-163)
```rust
    pub fn check_proposal(
        &mut self,
        client: &StacksClient,
        signer_db: &mut SignerDb,
        block: &NakamotoBlock,
        reset_view_if_wrong_consensus_hash: bool,
        replay_set: ReplayTransactionSet,
    ) -> Result<(), RejectReason> {
        if self.cur_sortition.miner_status == SortitionMinerStatus::Valid
            && SortitionState::is_timed_out(
                &self.cur_sortition.data.consensus_hash,
                signer_db,
                self.config.block_proposal_timeout,
            )?
        {
            info!(
                "Current miner timed out, marking as invalid.";
                "block_height" => block.header.chain_length,
                "block_proposal_timeout" => ?self.config.block_proposal_timeout,
                "current_sortition_consensus_hash" => ?self.cur_sortition.data.consensus_hash,
            );
            self.cur_sortition.miner_status = SortitionMinerStatus::InvalidatedBeforeFirstBlock;

            // If the current proposal is also for this current
            // sortition, then we can return early here.
            if self.cur_sortition.data.consensus_hash == block.header.consensus_hash {
                return Err(RejectReason::InvalidMiner);
            }
```

**File:** stacks-signer/src/chainstate/v2.rs (L113-132)
```rust
    pub fn check_proposal(
        &self,
        client: &StacksClient,
        signer_db: &mut SignerDb,
        block: &NakamotoBlock,
    ) -> Result<(), RejectReason> {
        let MinerState::ActiveMiner {
            current_miner_pkh,
            tenure_id,
            parent_tenure_id,
            ..
        } = &self.signer_state.current_miner
        else {
            info!(
                "No valid current miner. Considering invalid.";
                "block_height" => block.header.chain_length,
                "signer_signature_hash" => %block.header.signer_signature_hash()
            );
            return Err(RejectReason::InvalidMiner);
        };
```

**File:** stacks-signer/src/v0/signer.rs (L1340-1366)
```rust
        // The chain and signer db state may have changed materially since this block passed the
        // proposal-time checks (e.g. between validation and reaching the pre-commit threshold we
        // may have signed a block that this one would reorg). Re-run the chainstate checks
        // before putting a signature over the block, and respond with a rejection if they no
        // longer pass, just as the block validation response handler does.
        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
        {
            warn!(
                "{self}: Reached the pre-commit threshold for a block, but it no longer passes the chainstate checks. Rejecting.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "reject_code" => %block_rejection.reason_code,
                "reject_reason" => &block_rejection.reason,
            );
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
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1799-1841)
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

```

**File:** docs/signer-flows.md (L421-437)
```markdown
`check_block_against_signer_db_state` returns `SortitionViewMismatch`, or
`ConnectivityIssues` when the lookup itself errored rather than answering; the v2
`check_proposal` path returns `InvalidParentBlock`.

Two things belong to the proposal path only and are **not** re-run at validate-ok
or at signing:

- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
- the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the
  pox bitvec, and tenure-extend rules before delegating here.

Because the duplicate check never runs again, a block that crosses the pre-commit
threshold long after it was proposed relies on section 5's own-tenure conflict
guard to cover the same ground.
```
