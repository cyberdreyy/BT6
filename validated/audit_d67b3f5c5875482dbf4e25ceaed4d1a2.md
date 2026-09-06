### Title
Pre-commit-threshold signing path never re-checks miner validity, letting a signer sign for a miner it has already marked invalid - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`check_block_against_signer_db_state` is the only re-validation the v0 signer performs immediately before it commits an irreversible signature, both when block validation returns OK and when the pre-commit weight threshold is crossed. Unlike `SortitionsView::check_proposal`/`GlobalStateView::check_proposal` (run once, at proposal arrival), this "last look" function never re-derives or re-checks `SortitionMinerStatus`/`current_miner` validity — it structurally cannot, because it is never given the `sortition_state`. This mirrors the reported `cooldown()` bug class: one action (`check_proposal`) enforces a critical guard (miner validity) that a sibling action performed later in the same lifecycle (the pre-commit → signature step) omits.

### Finding Description
`SortitionsView::check_proposal` (v1) explicitly ties signing eligibility to `SortitionMinerStatus`: [1](#0-0) 
and marks a miner `InvalidatedBeforeFirstBlock` on timeout or bad reorg, rejecting with `InvalidMiner`/`ReorgNotAllowed`: [2](#0-1) 
The v2 equivalent likewise gates on `MinerState::ActiveMiner` from `self.signer_state.current_miner`: [3](#0-2) 

However, `check_proposal` is documented and implemented as a proposal-arrival-only check; the checks it performs on miner pubkey/consensus hash/status are explicitly noted as "not re-run at validate-ok or at signing": [4](#0-3) 

The function that *is* re-run at both validate-ok and at the pre-commit-threshold signing step is `check_block_against_signer_db_state`. Its signature takes only `stacks_client` and the `proposed_block` — no `sortition_state`/miner-status input at all — and it only re-verifies tenure-confirms-parent / latest-block-in-tenure facts, nothing about miner validity: [5](#0-4) 

This same function is invoked immediately before the signer commits its signature at the pre-commit threshold in `handle_block_pre_commit`: [6](#0-5) 
and after it passes, the code proceeds straight to conflict checks and then `mark_locally_accepted` / `handle_block_signature` (the actual signature) with no miner-status re-evaluation in between: [7](#0-6) 

Meanwhile, a signer's view of miner validity is mutable and can flip to invalid *after* the proposal was accepted and pre-committed, both via `check_proposal`'s own timeout logic on any later invocation, and explicitly via `capitulate_viewpoint`/`capitulate_miner_view`, which sets `sortition_state.cur_sortition.miner_status = SortitionMinerStatus::InvalidatedBeforeFirstBlock` on a miner-view mismatch: [8](#0-7) 

Because pre-commits are counted and stored independently of this later invalidation (`add_block_pre_commit`/`get_block_pre_committers` in `handle_block_pre_commit`), a block that already collected ≥70% pre-commit weight from signers *before* the miner was invalidated can still cross the threshold and be signed by a signer *after* that same signer has locally recorded the miner as invalid — because the only re-check gate before signing, `check_block_against_signer_db_state`, is blind to `miner_status`/`current_miner`.

### Impact Explanation
This breaks the equality the protocol otherwise enforces everywhere else: "a signer's signature must reflect its current, not stale, view of miner/tenure validity." `check_proposal` is designed to prevent ever handing a signature to a miner the signer has flagged invalid (timed out, or reorging improperly); the pre-commit → signature path is the one place that spends the actual, irreversible signature, and it is exactly the path missing this guard. The result is a signer producing a signature for a block from a miner it has independently and locally determined to be invalid — a direct instance of "a signer signing an invalid/non-canonical/conflicting block," matching the Critical impact bucket in scope. Because signers' timeouts (`block_proposal_timeout`) are the same configured duration across the set, this race is not a one-off fluke limited to a single signer; it is systematically triggerable by ordinary network timing (slow pre-commit gossip, or a single stalling/one-slot miner near the timeout boundary) without requiring a majority of colluding signers or another signer's key.

### Likelihood Explanation
No malicious majority or extra credentials are required — only enough passage of time (or a slow-gossiping/stalling miner) for `block_proposal_timeout` to elapse locally between when the pre-commit weight starts accumulating and when it finally crosses the 70% threshold. Given a one-slot miner that mines slowly, delays its own follow-up activity, or is affected by an in-flight reorg while a first proposal's pre-commits are still trickling in via gossip, this window is realistically reachable in normal operation, not merely a theoretical corner case.

### Recommendation
Thread `sortition_state`/the current miner state into `check_block_against_signer_db_state` (or otherwise re-invoke the equivalent of `check_proposal`'s miner-validity checks) so it is re-evaluated at both call sites — the validate-ok recheck and, critically, the pre-commit-threshold recheck in `handle_block_pre_commit` — immediately before `mark_locally_accepted`/`handle_block_signature` is reached, mirroring the mitigation pattern from the referenced report ("re-check the guard that other paths already enforce before performing the irreversible action").

### Proof of Concept
1. Miner M proposes block B for the current sortition/tenure; ≥5 of 7 (≥70% weight) signers validate it OK and broadcast pre-commits (`handle_block_validate_ok` → `mark_pre_committed` → `send_block_pre_commit`), per: [9](#0-8) 
2. Due to ordinary gossip delay, one signer S has received only 60% pre-commit weight so far and is still waiting (`min_weight > commit_weight` branch), per: [10](#0-9) 
3. Meanwhile `block_proposal_timeout` elapses for M's sortition on signer S (e.g., M is slow to follow up, or S is briefly desynced). The next `check_proposal` call (for any subsequent proposal or the miner-inactivity housekeeping) marks `S`'s `cur_sortition.miner_status = InvalidatedBeforeFirstBlock`, per: [1](#0-0) 
4. The remaining pre-commits for B (already in flight from honest signers who committed before the timeout) finally arrive at S, crossing the 70% threshold in `handle_block_pre_commit`. S calls `check_block_against_signer_db_state`, which passes (it never looks at `miner_status`), then proceeds to sign and broadcast acceptance for B via `mark_locally_accepted`/`handle_block_signature`, per: [11](#0-10) 
5. S has thus signed a block from a miner it has itself flagged `InvalidatedBeforeFirstBlock` — the exact condition `check_proposal` exists to prevent at proposal time, but which is silently bypassed at the actual signing step.

### Citations

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

**File:** stacks-signer/src/chainstate/v1.rs (L187-219)
```rust
                if !is_valid_parent_tenure {
                    warn!(
                        "Current sortition does not build off of canonical tip tenure, marking as invalid";
                        "current_sortition_parent" => ?self.cur_sortition.data.parent_tenure_id,
                        "tip_consensus_hash" => ?tip.block.header.consensus_hash,
                    );
                    self.cur_sortition.miner_status =
                        SortitionMinerStatus::InvalidatedBeforeFirstBlock;

                    // If the current proposal is also for this current
                    // sortition, then we can return early here.
                    if self.cur_sortition.data.consensus_hash == block.header.consensus_hash {
                        return Err(RejectReason::ReorgNotAllowed);
                    }
                }
            }
        }

        if let Some(last_sortition) = self.last_sortition.as_mut() {
            if last_sortition.miner_status == SortitionMinerStatus::Valid
                && SortitionState::is_timed_out(
                    &last_sortition.data.consensus_hash,
                    signer_db,
                    self.config.block_proposal_timeout,
                )?
            {
                info!(
                    "Last miner timed out, marking as invalid.";
                    "block_height" => block.header.chain_length,
                    "last_sortition_consensus_hash" => ?last_sortition.data.consensus_hash,
                );
                last_sortition.miner_status = SortitionMinerStatus::InvalidatedBeforeFirstBlock;
            }
```

**File:** stacks-signer/src/chainstate/v2.rs (L118-163)
```rust
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
```

**File:** docs/signer-flows.md (L425-433)
```markdown
Two things belong to the proposal path only and are **not** re-run at validate-ok
or at signing:

- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
- the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the
  pox bitvec, and tenure-extend rules before delegating here.
```

**File:** stacks-signer/src/v0/signer.rs (L1333-1338)
```rust
        if min_weight > commit_weight {
            debug!(
                "{self}: Not enough pre-committed to block {block_hash} (have {commit_weight}, need at least {min_weight}/{total_weight})"
            );
            return;
        }
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

**File:** stacks-signer/src/v0/signer.rs (L1458-1479)
```rust
        if !conflicts.is_empty() {
            info!(
                "{self}: Reached the pre-commit threshold for a block that conflicts with previously signed or accepted blocks, but none of those conflicts still blocks it. Signing the replacement.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "num_conflicts" => conflicts.len(),
            );
        }
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

**File:** stacks-signer/src/v0/signer_state.rs (L964-978)
```rust
            match new_miner {
                StateMachineUpdateMinerState::ActiveMiner {
                    current_miner_pkh, ..
                } => {
                    if let Some(sortition_state) = sortition_state {
                        // if there is a mismatch between the new_miner ad the current sortition view, mark the current miner as invalid
                        if current_miner_pkh != sortition_state.cur_sortition.data.miner_pkh {
                            sortition_state.cur_sortition.miner_status =
                                SortitionMinerStatus::InvalidatedBeforeFirstBlock
                        }
                    }
                }
                StateMachineUpdateMinerState::NoValidMiner => (),
            }
        }
```
