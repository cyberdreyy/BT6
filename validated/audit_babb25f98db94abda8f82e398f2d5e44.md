Based on the investigation, there is a genuine structural analog to the Velvet-Capital bug class in this codebase: a validation gate ("pause"-equivalent) that is enforced at one point in the pipeline but is **not re-enforced** at the later point where the actual state-changing action (the signer's cryptographic signature) is produced.

### Title
Signer can place a signature on a block whose miner/consensus-hash validity was only checked once at proposal time, never re-verified before the pre-commit-threshold signature is produced - (File: `stacks-signer/src/v0/signer.rs`, `stacks-signer/src/chainstate/v2.rs`)

### Summary
`GlobalStateView::check_proposal` (v2 chainstate) validates a proposed block's `consensus_hash`/`current_miner_pkh`/pox-bitvec against the signer's *current* view of the active miner **only once**, at proposal arrival. The re-validation performed later in the pipeline — `check_block_against_signer_db_state`, which runs both at block-validate-ok (`handle_block_validate_response`) and again right before a signature is actually emitted at the pre-commit threshold (`handle_block_pre_commit`) — deliberately does **not** repeat that check. It only re-runs the tenure-tip/parent confirmation logic (`check_latest_block_in_tenure` / `check_tenure_change_confirms_parent`). [1](#0-0) 

### Finding Description
The signer's block-acceptance pipeline has two distinct gates:

1. **Proposal-time gate** (`check_block_against_state` → `check_block_against_global_state` → `GlobalStateView::check_proposal`): checks `ConsensusHashMismatch`, `InvalidMiner`, `PubkeyHashMismatch`, `InvalidBitvec`, and (for tenure-change blocks) `validate_tenure_change_payload`/`DuplicateBlockFound`, all evaluated against `self.signer_state.current_miner` — a value that changes over time as new burn blocks/sortitions arrive. [2](#0-1) 

2. **Signing-time gate** (`check_block_against_signer_db_state`, called from `handle_block_validate_response` and again from `handle_block_pre_commit` right before the pre-commit-weight threshold triggers the actual signature): only re-checks tenure-tip confirmation, not miner validity/consensus-hash/pubkey-hash/bitvec. [3](#0-2) [4](#0-3) 

Between the moment a proposal passes gate (1) and the moment the pre-commit weight crosses 70% and the signature is emitted, minutes can elapse ("the bulk of a stalled block's latency") while pre-commits from other signers accumulate. [5](#0-4) 
During that window the signer's own `global_state_evaluator`/`local_state_machine.current_miner` view can change — e.g. the current miner can be marked `InvalidatedBeforeFirstBlock`/`InvalidatedAfterFirstBlock` on timeout, or a new sortition can supersede the tenure — yet none of that is re-asked at signing time. The documentation itself calls this out explicitly as intentional scope-narrowing for the re-check, listing exactly the checks that are "proposal path only" and never re-run at validate-ok or at signing. [6](#0-5) 

This is structurally the same class of bug as the Velvet-Capital report: a strong gate exists at one entry point (mint/burn ↔ block-proposal arrival), but the corresponding state-mutating action reachable via a different code path (ERC20 transfer ↔ pre-commit-threshold signature) is missing the same gate, "breaking the purpose" of the original check.

### Impact Explanation
If the check is genuinely missing coverage for a live scenario (miner later invalidated, or consensus-hash/miner-pkh no longer matching the signer's up-to-date view), a signer would emit a valid ECDSA signature over a block it itself would now consider `InvalidMiner`/`ConsensusHashMismatch`/`PubkeyHashMismatch` if it re-ran `check_proposal`. That is a "signer signing an invalid/non-canonical block" per the Critical impact bucket, since the signature was produced against the signer's own now-stale acceptance decision rather than its current chain view.

### Likelihood Explanation
Low-to-moderate. The specific `DuplicateBlockFound` gap in this same list is explicitly documented as covered by an independent mitigation (section 5's own-tenure/cross-tenure signed-conflict guard in `handle_block_pre_commit`), and `check_static_valid_block`/problematic-tx checks are redundant because they test immutable block properties. However, the miner-pkh/consensus-hash/InvalidMiner checks depend on the *signer's own time-varying miner state*, and no equivalent mitigation for that specific case is described or evidenced in `check_block_against_signer_db_state`. Reaching the window requires only ordinary asynchronous timing (validation delay + pre-commit gossip delay) plus a legitimate later state change (e.g., miner-inactivity timeout, a new sortition) — no majority-signer collusion or key compromise is needed, matching the "one slot miner plus gossip"-reachable scope of this exercise. Whether this is truly exploitable in practice, versus already implicitly precluded by invariants not visible in the snippets read (e.g., that `current_miner` transitions can't happen mid-tenure without also invalidating in-flight `BlockInfo` records), could not be fully confirmed from the available code slices.

### Recommendation
Re-run (or cheaply re-derive) the miner-validity/consensus-hash/pubkey-hash checks — or at minimum re-check `current_miner`'s validity/tenure-id — inside `check_block_against_signer_db_state`, immediately before `handle_block_pre_commit` and `handle_block_validate_response` allow a signature to be produced, mirroring the same "re-check before signing" discipline that was deliberately added for the tenure-conflict case in section 5 of the signer flow.

### Proof of Concept
1. Miner M proposes tenure-start block B; signer's `current_miner` is `ActiveMiner{pkh: M, tenure_id: T}` at that moment, so `GlobalStateView::check_proposal` accepts B and it is submitted for node validation. [7](#0-6) 
2. Node returns `Ok`; signer calls `handle_block_validate_ok` → `check_block_against_signer_db_state`, which checks only tenure-tip confirmation and marks B `PreCommitted`, broadcasting a pre-commit. [8](#0-7) 
3. Before B's pre-commit weight reaches 70%, a burn event arrives that causes the signer to mark M's tenure `InvalidatedBeforeFirstBlock`/`InvalidatedAfterFirstBlock` (as handled at proposal time in `SortitionsView::check_proposal`), updating `current_miner` away from M — but B is already stored and awaiting pre-commit votes, not subject to re-proposal. [9](#0-8) 
4. Peer pre-commits push B over the 70% threshold; `handle_block_pre_commit` runs `check_block_against_signer_db_state` again — which does not check `current_miner`/`InvalidMiner`/`PubkeyHashMismatch` — passes the tenure-tip check, and the signer signs B via `mark_locally_accepted`. [10](#0-9) [11](#0-10) 

This yields a signer signature over a block that, if `check_proposal` were re-run at that instant, would now be rejected as `InvalidMiner`/`ConsensusHashMismatch`/`PubkeyHashMismatch` — the exact "gate enforced once, bypassed on the later state-changing path" pattern from the source report.

### Citations

**File:** docs/signer-flows.md (L420-437)
```markdown
A failed check becomes a different rejection depending on who asked.
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

**File:** stacks-signer/src/chainstate/v2.rs (L111-184)
```rust
impl GlobalStateView {
    /// Apply checks from the signer state machine on the block proposal.
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
        if &block.header.consensus_hash != tenure_id {
            info!("Miner block proposal consensus hash does not match the current miner's tenure id. Considering invalid.";
                "block_height" => block.header.chain_length,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
                "block_consensus_hash" => %block.header.consensus_hash,
                "active_miner_tenure_id" => %tenure_id,
                "active_miner_parent_tenure_id" => %parent_tenure_id,
            );
            return Err(RejectReason::ConsensusHashMismatch {
                actual: block.header.consensus_hash.clone(),
                expected: tenure_id.clone(),
            });
        }
        let Some(miner_pk) = block.header.recover_miner_pk() else {
            warn!("Failed to recover miner pubkey";
                  "signer_signature_hash" => %block.header.signer_signature_hash(),
                  "consensus_hash" => %block.header.consensus_hash);
            return Err(RejectReason::IrrecoverablePubkeyHash);
        };
        let miner_pkh = Hash160::from_data(&miner_pk.to_bytes_compressed());
        if current_miner_pkh != &miner_pkh {
            warn!(
                "Miner block proposal pubkey does not match the winning pubkey hash for its sortition. Considering invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
                "proposed_block_pubkey" => &miner_pk.to_hex(),
                "proposed_block_pubkey_hash" => %miner_pkh,
                "active_miner_pubkey_hash" => %current_miner_pkh,
            );
            return Err(RejectReason::PubkeyHashMismatch);
        }
        let bitvec_all_1s = block.header.pox_treatment.iter().all(|entry| entry);
        if !bitvec_all_1s {
            warn!(
                "Miner block proposal has bitvec field which punishes in disagreement with signer. Considering invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
                "active_miner_consensus_hash" => ?tenure_id,
                "active_miner_parent_consensus_hash" => ?parent_tenure_id,
            );
            return Err(RejectReason::InvalidBitvec);
        }

        if let Some(tenure_change) = block.get_tenure_change_tx_payload() {
            Self::validate_tenure_change_payload(
                tenure_change,
                block,
                parent_tenure_id,
                signer_db,
                client,
                &self.config,
            )?;
```

**File:** stacks-signer/src/v0/signer.rs (L1283-1290)
```rust
        // do we have enough pre-commits to reach consensus?
        // i.e. is the threshold reached?
        //
        // Tally this up front, before the early returns below, so that every pre-commit we
        // receive can be logged with the running weight. Crossing this threshold is what
        // triggers our block response, so without it the wait for the threshold, which can
        // be minutes and is the bulk of a stalled block's latency, leaves no trace at all.
        let committers = self
```

**File:** stacks-signer/src/v0/signer.rs (L1333-1366)
```rust
        if min_weight > commit_weight {
            debug!(
                "{self}: Not enough pre-committed to block {block_hash} (have {commit_weight}, need at least {min_weight}/{total_weight})"
            );
            return;
        }

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

**File:** stacks-signer/src/v0/signer.rs (L1466-1478)
```rust
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
        if let Err(e) = block_info.mark_locally_accepted(false) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}",);
            }
        }
        self.signer_db
            .insert_block(&block_info)
            .unwrap_or_else(|e| self.handle_insert_block_error(e));
        let accepted = self.create_block_acceptance(&block_info.block);
        // have to save the signature _after_ the block info
        self.handle_block_signature(stacks_client, sortition_state, &accepted);
        self.send_block_response(&block_info.block, accepted.into());
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

**File:** stacks-signer/src/v0/signer.rs (L1941-1975)
```rust
        if !block_info.check_static_valid_block() {
            debug!("{self}: Block is syntatically invalid; will not store");
            return;
        }

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

**File:** stacks-signer/src/chainstate/v1.rs (L144-163)
```rust
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
