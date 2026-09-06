### Title
Adversary can indefinitely defer miner-inactivity timeout by resending proposals that always get rejected, wedging the signer's fallback-to-prior-miner logic - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionsView::check_proposal` decides whether the current sortition's miner should be marked invalid (and thus whether signers should fall back to accepting blocks/tenure-extends from the *previous* miner) purely based on `SortitionState::is_timed_out`, which measures elapsed time since a `last_activity` timestamp rather than since the sortition began. Because that activity timestamp is bumped by proposals that ultimately get *rejected* — not only by successfully signed blocks — a malicious current-sortition miner can keep resending cheap, guaranteed-to-be-rejected proposals just inside the `block_proposal_timeout`/`reorg_attempts_activity_timeout` window to perpetually reset the clock. This mirrors the reported decay-interval bug: any single "dummy" event just before the deadline prevents the state from ever reaching its terminal (decayed/timed-out) condition, letting the adversary block a state transition that the protocol relies on for liveness.

### Finding Description
`SortitionState::is_timed_out` computes: [1](#0-0) 
It uses `db.get_last_activity_time(sortition)`, falling back to the burn-block receive time only if no activity has been recorded, and returns `true` only once `elapsed > block_proposal_timeout` since that *last activity*, not since the sortition/tenure started. This is invoked from `check_proposal` to decide whether to flip `cur_sortition.miner_status` to `InvalidatedBeforeFirstBlock`, the gate that permits falling back to the previous miner's tenure-extend: [2](#0-1) 

The integration test suite explicitly documents and asserts that a block proposal which is *rejected* (e.g., due to a reorg attempt) still counts toward "miner activity" as long as it arrives before the timeout window elapses: [3](#0-2) 
and the companion test shows that once such an "activity" proposal arrives *after* the timeout window, the miner is correctly invalidated — confirming that arrival timing of any (not necessarily valid/accepted) proposal is what resets the countdown: [4](#0-3) 

Because the "activity" signal is satisfied by proposals that are ultimately rejected, a miner holding the current sortition slot can generate a steady stream of proposals it knows will be rejected (bad bitvec, wrong parent tenure, invalid tenure-change payload, etc.) spaced just under `block_proposal_timeout`/`reorg_attempts_activity_timeout` apart. Each such proposal refreshes `last_activity`, so `is_timed_out` never returns `true`, and `miner_status` is never flipped to `InvalidatedBeforeFirstBlock`. This is the same "block the decay just before the deadline" pattern as the reported bug: an adversary intentionally creates dummy events near the boundary of a time-based state transition to prevent that transition from completing, and thereby manipulate downstream behavior (here, permanently suppressing the signers' fallback path to the previous, presumably honest, miner).

### Impact Explanation
While `has_signed_block_in_tenure` prevents this from causing signers to sign or accept an invalid block, it does wedge the state machine's liveness guarantee: the mechanism designed to let signers abandon an unresponsive/misbehaving current-sortition miner and resume progress via the prior miner's tenure-extend can be kept perpetually disabled by a miner that never produces a valid block but keeps "pinging" the signer with doomed proposals. This matches the allowed "High" impact category of a signer being wedged such that it never falls back to signing valid blocks, stalling chain progress for the duration the adversary controls its sortition slot.

### Likelihood Explanation
A single miner that wins (or has already won) the current sortition slot can trigger this without collusion — it only needs to submit proposals it knows will fail validation at a cadence tighter than `block_proposal_timeout`. No majority of signers, no other party's key, and no privileged access is required, matching the rules' constraint that the analog be triggerable by a one-slot miner plus gossip alone.

### Recommendation
Base `is_timed_out` on a monotonic clock tied to the *sortition start* (or the first-received proposal for it) rather than a repeatedly-refreshable `last_activity` timestamp, or only allow rejected/invalid proposals to refresh activity up to a bounded number of times / bounded total window, so that a miner cannot indefinitely defer the fallback-to-prior-miner transition purely by resubmitting doomed proposals.

### Proof of Concept
1. Current sortition winner is a malicious miner `M`.
2. `M` submits a block proposal that will be rejected for any cheap-to-produce reason (e.g., non-canonical parent tenure, per `check_proposal`'s `ReorgNotAllowed` path at `stacks-signer/src/chainstate/v1.rs:180-201`), refreshing `last_activity` via signerdb.
3. `M` repeats step 2 every `< block_proposal_timeout` seconds indefinitely.
4. `SortitionState::is_timed_out` (`stacks-signer/src/chainstate/v1.rs:55-94`) never observes `elapsed > block_proposal_timeout`, so `cur_sortition.miner_status` is never set to `InvalidatedBeforeFirstBlock`.
5. Signers therefore never accept a tenure-extend or block proposal from the previous (honest) miner, per the `ProposedBy::LastSortition` gate at `stacks-signer/src/chainstate/v1.rs:301-316`, wedging chain progress for as long as `M` keeps sending rejected proposals.

Note: I was not able to fully trace every call site in `stacks-signer/src/signerdb.rs` that updates `last_activity_time` before the session ended (17 matches were found but not individually inspected), so the exact set of rejection reasons that do/do not refresh the timestamp is not fully enumerated here; a Devin session with fuller access to `signerdb.rs` would be needed to confirm the complete list of rejection paths that trigger the refresh.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L55-94)
```rust
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

**File:** stacks-node/src/tests/signer/v0/reorg.rs (L65-88)
```rust
#[test]
#[ignore]
/// Test that signers count a block proposal that was rejected due to a reorg towards miner activity since it showed up BEFORE
/// the reorg_attempts_activity_timeout
///
/// Test Setup:
/// The test spins up five stacks signers, one miner Nakamoto node, and a corresponding bitcoind.
/// The stacks node is then advanced to Epoch 3.0 boundary to allow block signing. The block proposal timeout is set to 20 seconds.
///
/// Test Execution:
/// Test validation endpoint is stalled.
/// The miner proposes a block N.
/// A new tenure is started.
/// The miner proposes a block N'.
/// The test waits for block proposal timeout + 1 second.
/// The validation endpoint is resumed.
/// The signers accept block N.
/// The signers reject block N'.
/// The miner proposes block N+1.
/// The signers accept block N+1.
///
/// Test Assertion:
/// Stacks tip advances to N+1
fn reorg_attempts_count_towards_miner_validity() {
```

**File:** stacks-node/src/tests/signer/v0/reorg.rs (L271-296)
```rust
#[test]
#[ignore]
/// Test that signers do not count a block proposal that was rejected due to a reorg towards miner activity since it showed up AFTER
/// the reorg_attempts_activity_timeout
///
/// Test Setup:
/// The test spins up five stacks signers, one miner Nakamoto node, and a corresponding bitcoind.
/// The stacks node is then advanced to Epoch 3.0 boundary to allow block signing. The block proposal timeout is set to 20 seconds.
///
/// Test Execution:
/// Test validation endpoint is stalled.
/// The miner A proposes a block N.
/// Block proposals are stalled.
/// A new tenure is started.
/// The test waits for reorg_attempts_activity_timeout + 1 second.
/// The miner B proposes a block N'.
/// The test waits for block proposal timeout + 1 second.
/// The validation endpoint is resumed.
/// The signers accept block N.
/// The signers reject block N'.
/// The miner B proposes block N+1.
/// The signers reject block N+1.
///
/// Test Assertion:
/// Stacks tip advances to N.
fn reorg_attempts_activity_timeout_exceeded() {
```
