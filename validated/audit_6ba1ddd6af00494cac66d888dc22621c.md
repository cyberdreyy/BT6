### Title
Signer signs a block from a miner it has already invalidated because the pre-commit "one last look" re-check omits the miner-validity check that gated the original proposal - (File: `stacks-signer/src/v0/signer.rs` / `stacks-signer/src/chainstate/v1.rs`)

### Summary
A block proposal is only checked against the signer's current-miner view (`SortitionsView::check_proposal` in v1, `GlobalStateView::check_proposal` in v2 — miner pubkey hash, consensus hash, timeout/`SortitionMinerStatus`) once, at the moment the proposal arrives. The threshold gap between pre-commit and signature (the same gap the analog report's "terms may change after submission" bug targets) is bridged only by `check_block_against_signer_db_state`, which re-verifies tenure/parent-confirmation facts but never re-runs the miner-validity/timeout check. If the signer's own state machine marks the proposing miner as timed out or invalid while the block is only `PreCommitted` (unsigned), that invalidation is silently ignored when the pre-commit threshold is later reached, and the signer signs anyway.

### Finding Description
The v0 signer's block-signing pipeline is a two-round protocol: `handle_block_proposal` → `check_proposal` (proposal-time only) → `handle_block_validate_ok` → `handle_block_pre_commit` (signature on threshold). The "one last look" before signing is `check_block_against_signer_db_state`, called from both `handle_block_validate_ok` ( [1](#0-0) ) and `handle_block_pre_commit` ( [2](#0-1) ). This function only calls `SortitionData::check_tenure_change_confirms_parent` / `check_latest_block_in_tenure` against `signer_db` and the node — it never takes the `sortition_state`/`GlobalStateView` and never re-invokes `check_proposal` ( [3](#0-2) ).

The miner-validity checks that gated the *original* acceptance — matching pubkey hash, consensus hash to the active/current sortition, and `SortitionMinerStatus`/`MinerState::ActiveMiner` validity — live exclusively in `check_proposal` ( [4](#0-3)  and [5](#0-4) ) and `SortitionState::is_timed_out` ( [6](#0-5) ). Crucially, `is_timed_out` explicitly does **not** count a merely-`PreCommitted` block as "signed," precisely so the tenure can still time out: `has_signed_block_in_tenure` gates the check, and a pre-commit carries no signature ( [7](#0-6) ). This means: while a block sits `PreCommitted` waiting for the 70% pre-commit threshold — a window the project's own docs say "can be minutes" ( [8](#0-7) ) — the signer's own housekeeping (`check_miner_inactivity`, run every `process_event` pass, per `docs/signer-flows.md` line 84) can legitimately mark that same miner as timed out / invalidated and fall back to a prior tenure's miner view. Yet when pre-commit weight later crosses threshold, `handle_block_pre_commit` signs the block anyway, because the recheck it performs (`check_block_against_signer_db_state`) never asks "is this still the miner/tenure I currently consider valid?" — it only asks tenure-confirmation questions.

This is the same class of bug as the external report: an initial validation ("terms"/miner eligibility) is performed once, the actor's true state changes before the irrevocable action (signature), and the code does not re-validate the changed condition before committing the irrevocable act — it only re-checks a narrower subset of facts (tenure confirmation), not the full condition set that gated approval in the first place.

### Impact Explanation
This maps to the Critical bucket: "a signer signing an invalid, non-canonical, or conflicting block." A signer that has already fallen back to a prior tenure's miner (because it deemed the current miner timed out/invalid) can still emit a valid signature for a block from the miner it has just repudiated, once enough other signers' pre-commits accumulate. That signature is fully cryptographically valid and counts toward the group's 70% signing threshold (`store_and_process_block_signature`, [9](#0-8) ), so it can help push through a block the signer's own state machine has independently judged illegitimate — undermining the miner-validity guarantee the whole `check_proposal` gate exists to enforce, and potentially contributing to two incompatible tenures both accumulating valid signer weight.

### Likelihood Explanation
Requires only a single miner/proposer plus normal gossip timing (no majority of signers, no key compromise): a miner proposes a block, enough signers to reach 70% pre-commit weight take longer than `block_proposal_timeout`/`is_timed_out` to respond (network delay, load, or an adversarial miner deliberately delaying broadcast to a subset while pre-commits trickle in), causing this signer's local state machine to time out the miner independently, after which the delayed pre-commits from other signers finally cross the threshold. This is directly reachable by a one-slot miner plus normal message propagation delay/gossip, matching the required threat model.

### Recommendation
Before signing at the pre-commit threshold (and ideally also before marking `PreCommitted` at validate-ok), re-run the full miner-validity check — not just the tenure-confirmation subset — against the current `SortitionsView`/`GlobalStateView`. Concretely, `check_block_against_signer_db_state` (or a new wrapper called from `handle_block_pre_commit`) should re-invoke `check_proposal`'s miner-pubkey/consensus-hash/`SortitionMinerStatus`/`ActiveMiner` validity logic (or equivalently query `self.local_state_machine`'s current miner view) and refuse to sign if the block's miner no longer matches, mirroring the "expected terms must match current terms" fix pattern from the external report.

### Proof of Concept
1. Miner M proposes block B for tenure T; signer S validates it (`check_proposal` passes: M is `ActiveMiner`/`SortitionMinerStatus::Valid`), submits it to its node, gets `BlockValidateOk`, and moves B to `PreCommitted` via `handle_block_validate_ok` ( [10](#0-9) ), broadcasting a pre-commit.
2. Network delay/partition means only a minority of signers' pre-commits reach S promptly; `min_weight > commit_weight` keeps S waiting ( [11](#0-10) ).
3. Meanwhile, S's periodic housekeeping (`check_miner_inactivity`) observes M has been inactive past `block_proposal_timeout` and — because B is only `PreCommitted`, not signed, so `has_signed_block_in_tenure` is false — marks M's sortition as timed out/invalid and falls back to the prior tenure's miner view (per `SortitionState::is_timed_out` logic).
4. The delayed pre-commits from other signers now arrive and push `commit_weight` over `min_weight`. `handle_block_pre_commit` calls `check_block_against_signer_db_state`, which passes (it only checks tenure confirmation, not miner validity), and S proceeds to `mark_locally_accepted` and broadcasts its signature for B — a block from a miner S's own state machine has already ruled invalid.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1333-1338)
```rust
        if min_weight > commit_weight {
            debug!(
                "{self}: Not enough pre-committed to block {block_hash} (have {commit_weight}, need at least {min_weight}/{total_weight})"
            );
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1345-1366)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L1803-1880)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L1946-1984)
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
            // have to save the signature _after_ the block info
            let address = self.stacks_address.clone();
            self.handle_block_pre_commit(
                stacks_client,
                sortition_state,
                &address,
                signer_signature_hash,
            );
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2494-2514)
```rust
        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
        let total_signature_weight = self.compute_signature_signing_weight(addrs_to_sigs.keys());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });

        if min_weight > total_signature_weight {
            info!("{self}: Received block acceptance, but have not yet reached the acceptance threshold.";
                "signer_signature_hash" => %block_hash,
                "signature_weight" => signature_weight,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "block_height" => block_info.block.header.chain_length,
                "total_weight_approved" => total_signature_weight,
                "total_weight" => total_weight,
                "percent_approved" => (total_signature_weight as f64 / total_weight as f64 * 100.0),
            );
            return;
        }
```

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

**File:** stacks-signer/src/chainstate/v1.rs (L134-163)
```rust
impl SortitionsView {
    /// Apply checks from the SortitionsView on the block proposal.
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

**File:** stacks-signer/src/chainstate/v2.rs (L55-66)
```rust
        // If we've already signed a block in this tenure, the miner can't have timed out: we have
        // committed a signature to this tenure and must not help abandon it.
        //
        // Importantly, a block we have only pre-committed to does not count! A pre-commit carries
        // no signature, and if it never reaches the pre-commit threshold the tenure can stall
        // indefinitely. Treating it as signed here would suppress the inactivity timeout for
        // exactly the signers that pre-committed, so they could never fall back to the prior
        // miner and the tenure could never recover.
        let has_block = signer_db.has_signed_block_in_tenure(sortition)?;
        if has_block {
            return Ok(false);
        }
```

**File:** stacks-signer/src/chainstate/v2.rs (L119-163)
```rust
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
```

**File:** docs/signer-flows.md (L229-235)
```markdown
## 5. Pre-commit threshold → signature

The only place the signer produces a block signature by counting votes.
Pre-commits from peers (and our own) accumulate; at ≥70% weight the signer
decides whether to follow through. Between validation and threshold, we may have
signed a _different_ block at the same height, possibly in another tenure, so
the world must be re-checked before the signature leaves the box.
```
