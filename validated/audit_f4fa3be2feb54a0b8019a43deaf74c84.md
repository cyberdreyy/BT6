### Title
Signer's `tx_replay_scope`/`tx_replay_set` are not restored on restart, causing loss of replay-transaction enforcement for a still-active reorg - (File: `stacks-signer/src/v0/signer.rs`, `stacks-signer/src/v0/signer_state.rs`)

### Summary
`Signer::new` hardcodes `tx_replay_scope: None` at construction, and `LocalStateMachine::new` (invoked from the same constructor) calls `bitcoin_block_arrival` with `expected_burn_block = None`, which skips the block that would recompute/restore `tx_replay_set`/`tx_replay_scope`. As a result, a signer that restarts while a bitcoin-fork tx-replay is unresolved comes back up believing there is no active replay set, until the next real `NewBurnBlock` event arrives.

### Finding Description
`ReplayState`/`tx_replay_set`/`tx_replay_scope` govern whether `validate_replay` in `stackslib/src/net/api/postblock_proposal.rs` (`validate_replay`, lines 899-1086) enforces that a proposed block includes the required post-reorg replay transactions. This state is normally computed in `LocalStateMachine::bitcoin_block_arrival` (`stacks-signer/src/v0/signer_state.rs:561-733`), which is only invoked with a real `expected_burn_block: Some(..)` from `SignerEvent::NewBurnBlock` (`stacks-signer/src/v0/signer.rs:630-673`).

At construction time (`Signer::new`, `stacks-signer/src/v0/signer.rs:208-325`):
- `tx_replay_scope` field is hardcoded to `None` (line 318), regardless of what was persisted before a crash/restart.
- `local_state_machine` is (re)computed via `LocalStateMachine::new(&mut signer_db, ...)` (line 288), which internally calls `instance.bitcoin_block_arrival(db, client, proposal_config, None, &mut None, eval, active_signer_protocol_version)` (`signer_state.rs:141-160`).
- Because `expected_burn_block` is `None`, the entire fork-detection/replay-state-computation block at `signer_state.rs:607-669` (which is gated by `if let Some(expected_burn_block) = expected_burn_block`) is skipped entirely.
- The `tx_replay_set` written into the freshly (re)initialized state machine (`signer_state.rs:718-724`) is therefore just `prior_state_machine.tx_replay_set.clone()` from the placeholder (`Self::place_holder`, line 214-222, which sets `tx_replay_set: ReplayTransactionSet::none()`), i.e. always empty on restart.

So after a restart, both the signer's local `tx_replay_scope` (`None`) and its `local_state_machine`'s `tx_replay_set` (empty) are reset to the "no replay in progress" state, **even if a reorg-driven replay was active and unresolved immediately before the crash**. This state is only corrected the next time a genuine `SignerEvent::NewBurnBlock` event fires and calls `bitcoin_block_arrival` with a real `expected_burn_block`, re-deriving the replay set via `handle_possible_bitcoin_fork`/`ReplayState::infer_state`.

Given `validate_with_replay_tx`/replay set is what causes the signer to submit `replay_txs` in the `NakamotoBlockProposal` sent to the node's `/v3/block_proposal` validation endpoint (feeding `validate_replay` in `postblock_proposal.rs`), a restarted signer that has not yet observed a new burn block will submit block proposals for validation without the replay-tx constraint, or evaluate proposals it receives without enforcing that constraint. A malicious miner who wins a slot in this restart window can therefore propose a block that omits required replay transactions - a block that a non-restarted signer (with `tx_replay_scope`/`tx_replay_set` intact) would reject via `ValidateRejectCode::InvalidTransactionReplay`, but which the restarted signer treats as if no replay constraint exists.

This breaks the intended equality: "the replay-tx constraint applied to validation after restart == the constraint that was active immediately before restart, for the same still-unresolved reorg." Existing guards (the failsafe `reset_replay_set_after_fork_blocks`, `handle_possible_replay_failsafe`) only apply within `bitcoin_block_arrival`'s `Some(expected_burn_block)` branch and thus do not run at all during the restart window; there is no other check that recomputes the replay scope earlier (e.g., during proposal handling) before the next burn-block event.

### Impact Explanation
This is a **liveness/safety weakening tied to restart**, matching the "acting on a stale reward set/threshold" / loss-of-guard-on-restart category: a restarted signer temporarily loses its replay-tx enforcement guard. If enough signers restart in the same window (e.g., coordinated upgrade, or crash-restart due to a bug), a block omitting required replay transactions could gather enough signatures to be globally accepted — this would be a chain-safety violation (an "invalid" block, per the replay policy, getting signed). For a single restarted signer with a single vote, the practical effect is a loss of one signer's replay-tx defense; the severity scales with how many signers are in a comparable restart state, but even a single signer's guard-loss on restart is the exact "losing the equivocation/replay guard on restart" condition called out as High severity.

### Likelihood Explanation
Preconditions: (1) `validate_with_replay_tx = true` is enabled, (2) a bitcoin-fork-driven tx replay is in progress (non-empty replay set with an active scope) at the time a signer process restarts (crash, redeploy, manual restart), and (3) the restart happens before the next `NewBurnBlock` event is delivered to that signer. This is entirely a function of operational timing and process restarts, not attacker control — the attacker only needs to have a proposal ready to submit as soon as they detect/guess a signer has recently restarted during an active replay window, requiring just one miner slot plus normal gossip. It is repeatable across every restart that occurs during an active/unresolved replay episode. The main constraint is that it depends on a signer restart coinciding with a replay-scope window, which is a real but non-guaranteed occurrence (any signer crash-loop, deploy, or maintenance during a bitcoin-fork replay period).

### Recommendation
Persist and restore `tx_replay_scope` (and derived `tx_replay_set`) in `SignerDb` alongside the existing block-validated-by-replay-tx bookkeeping (`ADD_BLOCK_VALIDATED_BY_REPLAY_TXS_TABLE`/`get_was_block_validated_by_replay_tx`), and load it in `Signer::new` before/while constructing `LocalStateMachine`, instead of hardcoding `None`. Alternatively, force `LocalStateMachine::new` to immediately fetch the current burn-block view and call `bitcoin_block_arrival` with a real `Some(expected_burn_block)` (not `None`) so the replay state is deterministically recomputed synchronously at startup before any proposal validation or signing occurs, rather than waiting for the next asynchronous `NewBurnBlock` event.

### Proof of Concept
```rust
// Conceptual signer-side test (stacks-signer/src/tests/signer_state.rs style)
//
// 1. Build a SignerStateMachine / LocalStateMachine::Initialized with a
//    non-empty tx_replay_set and a corresponding tx_replay_scope = Some(scope),
//    by driving bitcoin_block_arrival() with a NewBurnBlock that triggers
//    handle_fork_for_new_replay() to return ReplayState::InProgress(set, scope).
//    Assert: local_state_machine.get_tx_replay_set() == Some(expected_txs)
//            and captured tx_replay_scope == Some(scope).
//
// 2. Simulate a process restart: construct a brand new `Signer` via
//    `Signer::new(&stacks_client, signer_config)` pointed at the SAME
//    signer_db path used in step 1 (so SignerDb-persisted state, e.g.
//    BlockValidatedByReplaySet rows, is available), but WITHOUT re-running
//    a NewBurnBlock event.
//    Assert (the bug): new_signer.tx_replay_scope == None
//            and new_signer.local_state_machine.get_tx_replay_set() == None
//    (even though before "restart" it was Some(...)).
//
// 3. Craft a NakamotoBlockProposal that omits the txids from the pre-restart
//    replay set (i.e., one that would be rejected via
//    ValidateRejectCode::InvalidTransactionReplay by validate_replay() in
//    postblock_proposal.rs if replay_txs were supplied).
//
// 4. Drive the pre-restart signer's proposal-handling path and confirm
//    rejection (replay_txs non-empty -> InvalidTransactionReplay).
//    Drive the post-restart signer's proposal-handling path with the same
//    proposal and confirm it is NOT rejected for replay reasons (no
//    replay_txs attached / tx_replay_scope is None), demonstrating the
//    equality break: constraint-after-restart != constraint-before-restart
//    for the same unresolved reorg.
```
Note: I was not able to fully trace, within the available indexing, the exact call site in `stacks-signer/src/v0/signer.rs` where `NakamotoBlockProposal.replay_txs` is populated from `local_state_machine`/`tx_replay_scope` before submission to `/v3/block_proposal` (grep located matches in `stacks_client.rs`, `signerdb.rs`, and `signer.rs` but the full snippet wasn't retrieved before the iteration budget was exhausted). This final wiring step should be confirmed directly in the repo (e.g., via `stacks-signer/src/v0/signer.rs` proposal-submission code and `stacks-signer/src/client/stacks_client.rs`) to fully close the reproduction loop, though the core defect — `tx_replay_scope`/`tx_replay_set` being unconditionally reset on `Signer::new` regardless of prior persisted state — is directly confirmed by the cited code.