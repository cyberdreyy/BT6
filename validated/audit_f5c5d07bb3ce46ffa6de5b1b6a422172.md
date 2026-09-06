### Title
Stale/unrelated sortition-view mutation in `capitulate_viewpoint` can flip a valid current miner to `InvalidatedBeforeFirstBlock`, causing the signer to accept blocks from a superseded miner - (File: `stacks-signer/src/v0/signer_state.rs`)

### Summary
`LocalStateMachine::capitulate_viewpoint` mutates the signer's independently-fetched `SortitionsView.cur_sortition.miner_status` by comparing the *global-consensus-derived* `new_miner`'s `current_miner_pkh` against `sortition_state.cur_sortition.data.miner_pkh`, without first checking that `new_miner`'s `tenure_id` actually corresponds to `cur_sortition`'s tenure (its `consensus_hash`). This is the same class of bug as the reported `HonestChallengeTree` issue: a locally cached snapshot (`SortitionsView`) is used together with newer, differently-scoped data without being validated/refreshed against the same reference point, so state derived from unrelated tenures gets conflated.

### Finding Description
`LocalStateMachine` tracks two independent views of "who is the valid current miner":
1. `SortitionsView` (`stacks-signer/src/chainstate/v1.rs`), a per-node RPC-fetched snapshot (`cur_sortition`, `last_sortition`) that is only refreshed via `fetch_view`/`reset_view` [1](#0-0) , and reset to `None` when this node observes a `NewBurnBlock` event [2](#0-1) .
2. `SignerStateMachine.current_miner`, derived from the *global* gossip-based `GlobalStateEvaluator` (other signers' state-machine-update messages), consulted in `capitulate_viewpoint`/`capitulate_miner_view`.

In `capitulate_viewpoint`, after determining a `new_miner` from the global evaluator, the code does: [3](#0-2) 

Only `current_miner_pkh` is extracted from `new_miner` (tenure_id, parent_tenure_id are discarded via `..`), and it is compared directly to `sortition_state.cur_sortition.data.miner_pkh` with **no check that `new_miner`'s tenure_id equals `cur_sortition.data.consensus_hash`**. If the mismatch is found, the code unconditionally flips this node's *locally fetched* `cur_sortition.miner_status` to `SortitionMinerStatus::InvalidatedBeforeFirstBlock`.

`SortitionsView` is only refreshed on this node's own observed burn-block events, whereas the global evaluator's view of `current_miner` can already reflect a newer tenure the moment enough other signers gossip it - independent of whether this node's own `SortitionsView` cache has been refreshed to match. This creates a window in which `capitulate_viewpoint` compares a `new_miner` pkh belonging to a *different* (newer) tenure against this node's still-valid `cur_sortition` (an older, still-active tenure it hasn't rolled over yet), and, since the pkhs will almost certainly differ, incorrectly marks the currently-valid sortition as `InvalidatedBeforeFirstBlock`.

This same `sortition_state` object (an `Option<SortitionsView>`) is threaded directly into `check_proposal`, which treats `InvalidatedBeforeFirstBlock` as a strong signal to fall back to accepting blocks from the *previous* (`last_sortition`) miner: [4](#0-3) 

So an incorrect flip in `capitulate_viewpoint` doesn't just make the signer overly conservative - it actively re-opens the "accept last sortition's miner" branch, which is designed only for the case where the *current* miner has legitimately misbehaved. If triggered spuriously by an unrelated cross-tenure pkh mismatch, the signer can end up signing off on a block produced by a superseded miner while a valid, current sortition winner exists, i.e., helping build/sign a non-canonical/conflicting block.

### Impact Explanation
This breaks the equality that should exist between "the miner this signer's own sortition view says is valid" and "the miner it actually accepts blocks from." A signer manipulated (or naturally raced) into flipping `cur_sortition.miner_status` incorrectly can:
- Sign a block from `last_sortition`'s miner (a superseded/non-canonical tenure) while an actual current, valid sortition winner exists — a conflicting/non-canonical signature (Critical), or
- Alternatively wedge itself into distrusting its own currently valid miner, refusing to sign legitimate blocks until the next sortition (High, liveness).

Both outcomes are one-slot-miner/gossip-triggerable: an attacker only needs to win one sortition (naturally, no majority needed) and rely on ordinary propagation-timing skew between this node's own sortition-RPC refresh cadence and the global state-machine-update gossip that other signers broadcast.

### Likelihood Explanation
`capitulate_viewpoint` is called periodically/opportunistically (gated only by `is_capitulation_check_ready`'s timeout, not by proof that both views reference the same tenure) [5](#0-4) , so any window where the signer's node-fetched `SortitionsView` lags (or leads) the gossip-derived global view is sufficient to trigger the flawed comparison. Because `SortitionsView` is only invalidated by this node's own `NewBurnBlock` observation while the global evaluator can update purely from stackerdb gossip, this timing skew is a normal operating condition, not an edge case, making the likelihood moderate-to-high in any live signer set experiencing typical network/RPC latency variance.

### Recommendation
- **Short term:** In `capitulate_viewpoint`, before comparing `current_miner_pkh` against `sortition_state.cur_sortition.data.miner_pkh`, verify that `new_miner`'s `tenure_id` matches `sortition_state.cur_sortition.data.consensus_hash` (the same tenure). If they don't match, either refresh/reset the `SortitionsView` for that tenure or skip the mutation entirely.
- **Long term:** Consolidate the two parallel "current miner" tracking mechanisms (`SortitionsView` and the global-state-derived `SignerStateMachine.current_miner`) so cross-comparisons are only ever made between values proven to reference the identical tenure/consensus hash, and add a test that exercises capitulation while the local `SortitionsView` is intentionally stale relative to the global evaluator's view, asserting `cur_sortition.miner_status` is not corrupted.

### Proof of Concept
1. Signer S has a locally valid `SortitionsView.cur_sortition` for tenure `T1` (miner pkh `PK1`), fetched via `fetch_view` and not yet reset (no `NewBurnBlock` event seen locally yet, e.g. RPC/node lag).
2. Enough other signers gossip `StateMachineUpdate` messages that already reflect tenure `T2` (miner pkh `PK2`) as `current_miner` in the `GlobalStateEvaluator` (this only requires normal state propagation, not a signer majority acting maliciously - just ordinary timing skew, or a miner deliberately timing sortitions to create this window).
3. S's runloop calls `capitulate_viewpoint`; `capitulate_miner_view` returns `new_miner = ActiveMiner { current_miner_pkh: PK2, tenure_id: T2, ... }`.
4. Code at `signer_state.rs:964-977` compares `PK2 != sortition_state.cur_sortition.data.miner_pkh (PK1)` → true, and sets `sortition_state.cur_sortition.miner_status = InvalidatedBeforeFirstBlock`, despite `cur_sortition` referring to tenure `T1`, unrelated to `T2`.
5. A subsequent block proposal for `T1`'s `last_sortition` predecessor (i.e., the miner from the tenure *before* T1) now satisfies `ProposedBy::LastSortition` in `check_proposal` (`v1.rs:301-316`) because `cur_sortition.miner_status != Valid`, letting S sign/accept a block from a superseded tenure it should have already stopped considering, alongside (or instead of) the actual current sortition's blocks.

Note: I could not fully trace, within the available index, the exact call frequency/scheduling of `capitulate_viewpoint` from the runloop (only found its call sites in `signer.rs`/tests) or confirm definitive production-realistic timing bounds between node RPC refresh and stackerdb gossip propagation; a Devin session with full repo access would be needed to reproduce this end-to-end in the test harness (`stacks-node/src/tests/signer/v0/capitulate_parent_tenure_view.rs`) to empirically confirm exploitability.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L288-316)
```rust
        // check that this miner is the most recent sortition
        match proposed_by {
            ProposedBy::CurrentSortition(sortition) => {
                if sortition.miner_status != SortitionMinerStatus::Valid {
                    warn!(
                        "Current miner behaved improperly, this signer views the miner as invalid.";
                        "proposed_block_consensus_hash" => %block.header.consensus_hash,
                        "signer_signature_hash" => %block.header.signer_signature_hash(),
                        "current_sortition_miner_status" => ?sortition.miner_status,
                    );
                    return Err(RejectReason::InvalidMiner);
                }
            }
            ProposedBy::LastSortition(last_sortition) => {
                // should only consider blocks from the last sortition if the new sortition was invalidated
                //  before we signed their first block.
                if self.cur_sortition.miner_status
                    != SortitionMinerStatus::InvalidatedBeforeFirstBlock
                {
                    warn!(
                        "Miner block proposal is from last sortition winner, when the new sortition winner is still valid. Considering proposal invalid.";
                        "proposed_block_consensus_hash" => %block.header.consensus_hash,
                        "signer_signature_hash" => %block.header.signer_signature_hash(),
                        "current_sortition_miner_status" => ?self.cur_sortition.miner_status,
                        "last_sortition" => %last_sortition.data.consensus_hash
                    );
                    return Err(RejectReason::NotLatestSortitionWinner);
                }
            }
```

**File:** stacks-signer/src/chainstate/v1.rs (L522-544)
```rust
    /// Fetch a new view of the recent sortitions
    pub fn fetch_view(
        config: ProposalEvalConfig,
        client: &StacksClient,
    ) -> Result<Self, ClientError> {
        let CurrentAndLastSortition {
            current_sortition,
            last_sortition,
        } = client.get_current_and_last_sortition()?;

        let cur_sortition = SortitionState::try_from(current_sortition)?;
        let last_sortition = last_sortition
            .map(SortitionState::try_from)
            .transpose()
            .ok()
            .flatten();

        Ok(Self {
            cur_sortition,
            last_sortition,
            config,
        })
    }
```

**File:** stacks-signer/src/v0/signer.rs (L664-673)
```rust
                let active_signer_protocol_version = self.get_signer_protocol_version();
                self.local_state_machine
                    .bitcoin_block_arrival(&mut self.signer_db, stacks_client, &self.proposal_config, Some(NewBurnBlock {
                        burn_block_height: *burn_height,
                        consensus_hash: consensus_hash.clone(),
                    }),
                    &mut self.tx_replay_scope
                , &self.global_state_evaluator, active_signer_protocol_version)
                    .unwrap_or_else(|e| error!("{self}: failed to update local state machine for latest bitcoin block arrival"; "err" => ?e));
                *sortition_state = None;
```

**File:** stacks-signer/src/v0/signer_state.rs (L964-977)
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
```
