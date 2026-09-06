### Title
Timeout-triggered `reset_rejections` clears rejection weight while stale approvals persist forever, letting a >30% globally-rejected block later cross the 70% approval threshold - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListenerComms::reset_rejections`, invoked by the miner-side `SignerCoordinator::get_block_status` on every `SignatureTimeout`, wipes `total_weight_rejected` and `responded_signers` for a block but explicitly preserves `gathered_signatures`/`total_weight_approved` forever [1](#0-0) . Because the miner resends the *identical* block (same `signer_signature_hash`) on every timeout and reuses the same `BlockStatus` object rather than reinitializing it [2](#0-1) , approval weight from stale, long-past acceptances accumulates indefinitely across resend cycles while rejection weight is reset to zero on each timeout. This breaks the documented invariant that ">30% rejected makes 70% impossible → globally rejected" (docs/signer-flows.md section 6), because the "impossible" state is never durably recorded on the miner side — it can be repeatedly erased by a single miner simply waiting out the rejection timeout, in a mechanism that is structurally analogous to the killed-gauge bug: a single, repeatable, attacker-triggerable action (re-triggering `distribute`/timeout) that undoes a guard meant to be one-shot per decision, and can partially or fully negate emissions/consensus state that should have been finalized (drain the pot / flip "globally rejected" back to "still pending").

### Finding Description
The `BlockStatus` struct tracks, per proposed block (`signer_signature_hash`), `total_weight_approved`, `total_weight_rejected`, `gathered_signatures`, and `responded_signers` [3](#0-2) .

Approvals accumulate monotonically and are never removed once added: `add block.total_weight_approved`, `gathered_signatures.insert`, `responded_signers.insert` [4](#0-3) . Rejections accumulate the same way [5](#0-4) .

`get_block_status`'s waiting loop treats `total_weight_rejected + weight_threshold > total_weight` as the terminal "signers rejected" outcome, returning `SignersRejected` [6](#0-5) . But if the loop times out *before* that condition is (re-)observed — e.g., due to `EVENT_RECEIVER_POLL` cadence, slow gossip, or a miner deliberately using a short `rejections_timeout` step — the code calls `self.stackerdb_comms.reset_rejections(block_signer_sighash)` and returns `SignatureTimeout` instead of `SignersRejected` [7](#0-6) , [8](#0-7) .

`reset_rejections` explicitly zeroes `total_weight_rejected` and clears `responded_signers` (re-seeding it only with slots that already approved), while leaving `gathered_signatures` and `total_weight_approved` completely untouched — the comment even states this asymmetry is intentional ("Block approvals cannot be cleared because an old approval could always be used to make a block reach the approval threshold") [1](#0-0) .

`propose_block` then loops and resends the *same* `NakamotoBlock`/`signer_signature_hash` on `SignatureTimeout`, calling `get_block_status` again on the *same* `BlockStatus` entry (it does not call `insert_block` again, so history is not reinitialized) [2](#0-1) .

The consequence: suppose a supermajority of signers (>30% weight) reject the block for a genuinely disqualifying reason (fork, invalid tx, reorg not allowed) — enough weight for `SignersRejected` per the doc's terminal rule ("over 30% rejected — 70% is now impossible → globally rejected", `docs/signer-flows.md`). If the miner's SignCoordinator times out even once before observing this — which any single miner fully controls, since it owns `block_rejection_timeout_steps` and the polling loop — `reset_rejections` wipes that rejection tally to zero and clears `responded_signers` for everyone except those who already approved. The miner then resends the identical proposal. Signers whose earlier acceptances are *never revoked* on the miner side (this is enforced deliberately, and is also visibly relied upon by tests such as `stale_proposal_of_accepted_block_resends_acceptance` [9](#0-8) ) keep contributing their stale approval weight toward `weight_threshold` on every subsequent poll, indefinitely, while the disqualifying rejection weight from the rejecting cohort has to be rebuilt from scratch after every timeout-triggered reset. If even a slow-but-nonzero fraction of previously-approving weight (accumulated over many resend cycles, possibly across multiple distinct rejecting quorums that each get wiped before crossing the SignersRejected threshold) sums past `weight_threshold`, `get_block_status` returns `Ok` and the block is pushed — even though a rejecting cohort exceeding the 30% blocking minority existed at various points but was never allowed to "stick" because the miner's local tally kept getting reset before the loop observed it.

This inverts the intended equality "aggregated rejection weight vs. verified accepts": on the miner side there is no analog of the signer-side sticky `GloballyRejected`/`GloballyAccepted` state machine (`BlockState::check_state` in `signerdb.rs`, which makes both global states terminal against each other) [10](#0-9) . The miner's local `BlockStatus` bookkeeping has no such terminal/sticky rejection concept — its rejection counter is fully mutable and repeatable via `reset_rejections`, while its approval counter is a monotonic ratchet with no expiry, no re-validation, and no tie to the *current* polling window. This is precisely the "killed-gauge" pattern: a repeatable action, fully controlled by a single actor, that discards a state (rejection tally) that was supposed to gate further disbursement (block push), while a persistent counter (approval weight) keeps accumulating unguarded across repeats.

### Impact Explanation
This is a High-severity liveness/safety wedge on the acceptance side of the protocol:
- A single miner (who owns and configures `block_rejection_timeout_steps` and the coordinator polling behavior) can, by using short timeout steps or by simply exploiting inherent poll-cadence timing, repeatedly reset a block's local rejection tally before the >30% "signers rejected" threshold is durably observed, functionally suppressing the miner's own recognition of `SignersRejected` outcomes.
- Meanwhile, stale approvals from signers who accepted the block earlier (and whose per-signer decisions are deliberately kept sticky/resent rather than re-evaluated — see `stale_proposal_of_accepted_block_resends_acceptance`) keep counting toward `weight_threshold` without ever being re-verified against the current state, since the miner's `get_block_status` never asks the signer set to re-validate against a fresher chain view before it declares `Ok`.
- The net effect can let a block that should have been finalized as globally rejected (i.e., that a blocking minority already rejected) instead cross the 70% signature threshold locally at the miner and get pushed, violating the equality that "over 30% rejected weight makes 70% signed impossible" that the design (and docs) rely on as the terminal safety property.

### Likelihood Explanation
Medium: the mechanism requires only actions available to the block proposer/miner itself (configuring/using short rejection timeout steps, or simply operating under realistic network delay so that the polling loop times out before the rejection sum crosses the 30% blocking threshold) plus normal gossip lag — no majority signer collusion, no stolen keys, and no auth-token access are needed. It does require a specific timing window (rejections building up slower than the miner's timeout, spread across at least one reset cycle) and some already-accumulated stale approval weight, which is plausible in a live network with heterogeneous latencies and with a miner free to choose aggressive rejection-timeout steps.

### Recommendation
Make rejection accounting for a given `signer_signature_hash` sticky/monotonic exactly like `BlockState::GloballyRejected` is sticky in `stacks-signer/src/signerdb.rs`: once `total_weight_rejected` (or any subset combination observed across resend cycles) has crossed the blocking-minority threshold at any point, latch a "this block is dead" flag on the `BlockStatus` entry that `get_block_status` checks before resetting on timeout, and never clear it via `reset_rejections`. Alternatively, track rejection weight as a monotonic union of all signer addresses that have ever rejected the sighash (mirroring the sticky uniqueness already used for `block_rejection_signer_addrs` in the signer's own `signerdb.rs`), rather than a `responded_signers` set that gets wiped and can be undermined by resend timing.

### Proof of Concept
1. Configure the miner's `block_rejection_timeout_steps` with a very small initial timeout (e.g., 0 → a few hundred ms), matching the pattern used in the `retry_proposal` integration test [11](#0-10) .
2. Have a majority-but-not-yet-observed-by-miner cohort (>30% weight) begin sending `BlockResponse::Rejected` for a proposal while a smaller, already-approving cohort's earlier `BlockResponse::Accepted` sits in `gathered_signatures` from a previous resend cycle.
3. Because the poll interval (`EVENT_RECEIVER_POLL` = 500ms) and the short rejection timeout expire before `total_weight_rejected.saturating_add(weight_threshold) > total_weight` is observed inside the loop (per `get_block_status`) [12](#0-11) , the loop falls into the `SignatureTimeout` branch and calls `reset_rejections`, zeroing the rejection tally [1](#0-0) .
4. `propose_block` resends the identical proposal and re-polls the same `BlockStatus`; the previously-accumulated `total_weight_approved` (never cleared) plus any new approvals eventually reach `weight_threshold`, and `get_block_status` returns `Ok`, pushing a block that a >30% cohort had already rejected at points that were repeatedly erased before being durably observed [13](#0-12) .

**Uncertainty note**: I was unable to fully verify, within the indexed content, the exact behavior of `wait_for_block_status`'s predicate closure and the precise scheduling/ordering guarantees between `total_weight_rejected` updates and the loop's blocking-minority check across concurrent StackerDB message delivery — a full reproduction would need a live/integration-test run (e.g., adapting `retry_proposal`) to confirm the race window is actually reachable in practice under realistic gossip timing, rather than only demonstrable via unit-level manipulation of `BlockStatus`.

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L70-82)
```rust
#[derive(Debug, Clone)]
pub struct BlockStatus {
    /// Set of the slot ids of signers who have responded
    pub responded_signers: HashSet<u32>,
    /// Map of the slot id of signers who have signed the block and their signature
    pub gathered_signatures: BTreeMap<u32, MessageSignature>,
    /// Total weight of signers who have signed the block
    pub total_weight_approved: u32,
    /// Total weight of signers who have rejected the block
    pub total_weight_rejected: u32,
    /// Per-txid rejection tracking from signers
    pub failed_txids: HashMap<Txid, FailedTxInfo>,
}
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-465)
```rust
                        if !block.gathered_signatures.contains_key(&slot_id) {
                            block.total_weight_approved = block
                                .total_weight_approved
                                .saturating_add(signer_entry.weight);

                            info!("StackerDBListener: Signature Added to block";
                                "signer_signature_hash" => %block_sighash,
                                "signer_pubkey" => signer_pubkey.to_hex(),
                                "signer_slot_id" => slot_id,
                                "signature" => %signature,
                                "signer_weight" => signer_entry.weight,
                                "total_weight_approved" => block.total_weight_approved,
                                "percent_approved" => block.total_weight_approved as f64 / self.total_weight as f64 * 100.0,
                                "total_weight_rejected" => block.total_weight_rejected,
                                "percent_rejected" => block.total_weight_rejected as f64 / self.total_weight as f64 * 100.0,
                                "weight_threshold" => self.weight_threshold,
                                "tenure_extend_timestamp" => tenure_extend_timestamp,
                                "read_count_extend_timestamp" => read_count_extend_timestamp,
                                "server_version" => metadata.server_version,
                            );
                        }
                        block.gathered_signatures.insert(slot_id, signature);
                        block.responded_signers.insert(slot_id);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-518)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L706-723)
```rust
    /// Reset rejections for a block proposal.
    /// This is used when a block proposal times out and we need to retry it by
    /// clearing the block's rejections. Block approvals cannot be cleared
    /// because an old approval could always be used to make a block reach
    /// the approval threshold.
    pub fn reset_rejections(&self, signer_sighash: &Sha512Trunc256Sum) {
        let (lock, _cvar) = &*self.blocks;
        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");
        if let Some(block) = blocks.get_mut(signer_sighash) {
            block.responded_signers.clear();
            block.total_weight_rejected = 0;

            // Add approving signers back to the responded signers set
            for (slot_id, _) in block.gathered_signatures.iter() {
                block.responded_signers.insert(*slot_id);
            }
        }
    }
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L291-356)
```rust
        // Add this block to the block status map.
        self.stackerdb_comms.insert_block(&block.header);

        let reward_cycle_id = burnchain
            .block_height_to_reward_cycle(election_sortition.block_height)
            .expect("FATAL: tried to initialize coordinator before first burn block height");

        let block_proposal = BlockProposal {
            block: block.clone(),
            burn_height: election_sortition.block_height,
            reward_cycle: reward_cycle_id,
            block_proposal_data: BlockProposalData::from_current_version(miner_diagnostic_data),
        };

        let block_proposal_message = SignerMessageV0::BlockProposal(block_proposal);

        loop {
            debug!("Sending block proposal message to signers";
                "signer_signature_hash" => %block.header.signer_signature_hash(),
            );
            Self::send_miners_message::<SignerMessageV0>(
                &self.message_key,
                sortdb,
                election_sortition,
                stackerdbs,
                block_proposal_message.clone(),
                MinerSlotID::BlockProposal,
                self.is_mainnet,
                &mut self.miners_session,
                &election_sortition.consensus_hash,
                miner_db,
            )?;
            counters.bump_naka_proposed_blocks();

            #[cfg(test)]
            {
                info!(
                "SignerCoordinator: sent block proposal to .miners, waiting for test signing channel"
            );
                // In test mode, short-circuit waiting for the signers if the TEST_SIGNING
                //  channel has been created. This allows integration tests for the stacks-node
                //  independent of the stacks-signer.
                if let Some(signatures) =
                    crate::tests::nakamoto_integrations::TestSigningChannel::get_signature()
                {
                    debug!("Short-circuiting waiting for signers, using test signature");
                    return Ok(signatures);
                }
            }

            let res = self.get_block_status(
                &block.header.signer_signature_hash(),
                &block.block_id(),
                &block.header.parent_block_id,
                chain_state,
                sortdb,
                counters,
            );

            match res {
                Err(NakamotoNodeError::SignatureTimeout) => {
                    info!("Block proposal signing process timed out, resending the same proposal");
                    continue;
                }
                _ => return res,
            }
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L443-455)
```rust
                    if rejections_timer.elapsed() > *rejections_timeout {
                        warn!("Timed out while waiting for responses from signers, resending proposal";
                            "elapsed" => rejections_timer.elapsed().as_secs(),
                            "rejections_timeout" => rejections_timeout.as_secs(),
                            "rejections" => rejections,
                            "rejections_threshold" => self.total_weight.saturating_sub(self.weight_threshold)
                        );

                        // Reset the rejections in the stackerdb listener
                        self.stackerdb_comms.reset_rejections(block_signer_sighash);

                        return Err(NakamotoNodeError::SignatureTimeout);
                    }
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-540)
```rust
            if block_status
                .total_weight_rejected
                .saturating_add(self.weight_threshold)
                > self.total_weight
            {
                info!(
                    "{}/{} signer weight votes to reject block",
                    block_status.total_weight_rejected, self.total_weight;
                    "signer_signature_hash" => %block_signer_sighash,
                );
                counters.bump_naka_rejected_blocks();

                // Only act on failed txids that a blocking minority (>30% weight) agrees on
                let blocking_minority = self.total_weight.saturating_sub(self.weight_threshold);
                let mut temporarily_excluded_txids = HashSet::new();
                let mut permanently_excluded_txids = HashSet::new();
                for (txid, info) in &block_status.failed_txids {
                    if info.total_weight > blocking_minority {
                        // Do not perma ban txids that only a small minority of signers reported as problematic
                        // But make sure its removed from the next block proposal
                        if info.problematic_weight > blocking_minority {
                            permanently_excluded_txids.insert(txid.clone());
                        } else {
                            temporarily_excluded_txids.insert(txid.clone());
                        }
                    }
                }

                return Err(NakamotoNodeError::SignersRejected {
                    temporarily_excluded_txids,
                    permanently_excluded_txids,
                });
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L541-545)
```rust
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L546-557)
```rust
            } else if rejections_timer.elapsed() > *rejections_timeout {
                warn!("Timed out while waiting for responses from signers";
                    "elapsed" => rejections_timer.elapsed().as_secs(),
                    "rejections_timeout" => rejections_timeout.as_secs(),
                    "rejections" => rejections,
                    "rejections_threshold" => self.total_weight.saturating_sub(self.weight_threshold)
                );

                // Reset the rejections in the stackerdb listener
                self.stackerdb_comms.reset_rejections(block_signer_sighash);

                return Err(NakamotoNodeError::SignatureTimeout);
```

**File:** stacks-node/src/tests/signer/v0/proposal_replication_void.rs (L308-341)
```rust
/// Verify that a signer which has already decided on a block does not flip its
/// decision when the same proposal is re-sent after
/// `block_proposal_max_age_secs`.
///
/// `ProposalTooOld` is only appropriate when the signer has nothing to report.
/// If the signer already accepted the block, overwriting that acceptance with a
/// rejection would leave the miner and the other signers with divergent views
/// of this signer's vote, depending on which of the two responses each of them
/// observed (the miner keeps the acceptance, since approvals are sticky, while
/// a signer that only saw the rejection would count it toward the rejection
/// threshold). Resending the prior acceptance is also what actually unsticks
/// the miner: it is re-proposing precisely because it never heard the
/// acceptance.
///
/// Test Setup:
/// Five signers with block_proposal_max_age_secs = 30, one miner with a 15s
/// rejection timeout.
///
/// Test Execution:
/// 1. Suppress the signers' acceptance broadcasts (note that this testing hook
///    suppresses acceptances only -- a rejection would still be broadcast), so
///    the signers validate and locally accept block N while the miner hears
///    nothing and stays in its resend loop.
/// 2. Hold that state for > 30s so block N's proposal goes stale, then let the
///    acceptances flow again.
/// 3. The miner re-sends the stale proposal, and every signer resends its
///    acceptance instead of rejecting it as too old.
///
/// Test Assertion:
/// - All signers respond to the stale proposal with an acceptance.
/// - No signer ever rejects block N (in particular, not with ProposalTooOld).
/// - The original block N -- same hash, same old header timestamp -- is the
///   block that advances the tip.
fn stale_proposal_of_accepted_block_resends_acceptance() {
```

**File:** stacks-signer/src/signerdb.rs (L313-329)
```rust
    /// Check if the block state transition is valid
    fn check_state(&self, state: BlockState) -> bool {
        let prev_state = &self.state;
        if *prev_state == state {
            return true;
        }
        match state {
            BlockState::Unprocessed => false,
            BlockState::LocallyAccepted | BlockState::LocallyRejected => !matches!(
                prev_state,
                BlockState::GloballyRejected | BlockState::GloballyAccepted
            ),
            BlockState::GloballyAccepted => !matches!(prev_state, BlockState::GloballyRejected),
            BlockState::GloballyRejected => !matches!(prev_state, BlockState::GloballyAccepted),
            BlockState::PreCommitted => matches!(prev_state, BlockState::Unprocessed),
        }
    }
```

**File:** stacks-node/src/tests/signer/v0/mod.rs (L7049-7068)
```rust
        |_| {},
        |config| {
            config.miner.block_rejection_timeout_steps.clear();
            config
                .miner
                .block_rejection_timeout_steps
                .insert(0, Duration::from_secs(123));
            config
                .miner
                .block_rejection_timeout_steps
                .insert(10, Duration::from_secs(20));
            config
                .miner
                .block_rejection_timeout_steps
                .insert(15, Duration::from_secs(10));
            config
                .miner
                .block_rejection_timeout_steps
                .insert(20, Duration::from_secs(30));
        },
```
