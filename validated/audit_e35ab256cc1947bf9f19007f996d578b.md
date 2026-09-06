### Title
Signer broadcasts a signature over an already-Globally-Rejected block when `mark_locally_accepted` fails silently - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
In `Signer::handle_block_pre_commit`, once the pre-commit weight threshold (≥70%) is reached and the chainstate/conflict checks pass, the signer calls `block_info.mark_locally_accepted(false)` to record its acceptance before signing. The `Result` of this call is only used for logging, gated by `block_info.has_reached_consensus()` — it is never used to abort the signing flow. If the state transition is illegal (which, per `BlockInfo::check_state`, only happens when the block is already `GloballyAccepted` or `GloballyRejected`), the code still unconditionally proceeds to `create_block_acceptance`, `handle_block_signature`, and `send_block_response` with an `Accepted` message, producing and gossiping a real signature over a block the signer's own database already recorded as terminal (dead).

### Finding Description
The block lifecycle is documented as a one-way state machine where "Global states are terminal against each other" (`docs/signer-flows.md` §2). This is enforced in code by `BlockInfo::check_state`: [1](#0-0) 
`LocallyAccepted` cannot be reached once the block is `GloballyRejected` or `GloballyAccepted`, and `move_to`/`mark_locally_accepted` return `Err` in that case: [2](#0-1) 

In `handle_block_pre_commit`, after the pre-commit threshold and chainstate/conflict re-checks pass, the SIGN step is: [3](#0-2) 

Note that the error from `mark_locally_accepted` is only used to decide whether to *log* a warning (suppressed when `has_reached_consensus()` — i.e., precisely when the block is already `GloballyAccepted`/`GloballyRejected`). There is no `return` on error. Execution falls straight through to `insert_block`, `create_block_acceptance` (which unconditionally produces a valid ECDSA signature over the block's `signer_signature_hash`), `handle_block_signature`, and `send_block_response(... accepted.into())`, which broadcasts the acceptance over StackerDB regardless of whether the local record actually transitioned.

Nothing earlier in `handle_block_pre_commit` checks `block_info.state` against a terminal global state before entering the SIGN branch; the only earlier guards are `signed_self.is_some()`, `valid.unwrap_or(false)`, and the pre-commit weight threshold — none of which exclude a block that this signer has already locally recorded as `GloballyRejected` via a prior `handle_block_rejection` tally (>30% weight rejecting a rival/duplicate response), while a separate, previously-received/replayed `BlockPreCommit` stream for the same `signer_signature_hash` subsequently pushes `commit_weight` over `min_weight`.

This breaks the "signed vs validated"/terminal-state equality the whole `BlockState` design is built to guarantee: a block already deemed dead by 30%+ signer weight (per this signer's own bookkeeping) can still receive this signer's cryptographic signature and be gossiped as an `Accepted` `BlockResponse`.

### Impact Explanation
This falls under the "Critical" impact class defined by the scan rules: a signer producing a signature over a block that its own state machine has already classified as `GloballyRejected` (i.e., non-adoptable/dead per the documented invariant), which is a concrete safety break of the "signed vs validated" / global-state-terminality equality. A stray valid signature on a supposedly-dead block undermines the guarantee that "a signature is never given away cheaply" and that global states are terminal, and could contribute toward reviving/legitimizing a block other honest signers have already discarded.

### Likelihood Explanation
This requires only message ordering/timing that a single miner plus normal gossip can influence (a `BlockPreCommit` for a given hash processed/replayed after this signer's local record for that same block has already moved to `GloballyRejected` via rejection tally) — no majority key, node access, or auth token is needed. It is a same-signer logic bug reachable purely by controlling proposal/pre-commit/rejection timing, consistent with the "one-slot miner plus gossip" threat model in scope.

### Recommendation
In `handle_block_pre_commit` (`stacks-signer/src/v0/signer.rs`), check `block_info.state` for a terminal global state (`GloballyAccepted`/`GloballyRejected`) — or equivalently branch on the `Err` from `mark_locally_accepted` — and `return` immediately without signing/broadcasting when the transition fails, mirroring the intent already expressed by the "It is only considered globally accepted IFF..." comment. The warning-suppression logic should not also suppress the abort; logging and control flow need to be decoupled.

### Proof of Concept
1. Signer S receives a `BlockProposal` for block B, validates it, and pre-commits (state `PreCommitted`).
2. S separately receives/tallies enough `BlockResponse::Rejected` weight (>30%) for B from other signers via `handle_block_rejection`, moving S's local `BlockInfo` for B to `GloballyRejected` (terminal, per `check_state`).
3. A batch of previously-queued/replayed `BlockPreCommit` messages for B (or a delayed round) is then processed by `handle_block_pre_commit`, pushing `commit_weight` past `min_weight`; `valid` is still `true` and `signed_self` is still `None`, so none of the early guards fire.
4. Chainstate/conflict re-checks pass (nothing in those checks inspects the already-`GloballyRejected` local state).
5. `mark_locally_accepted(false)` fails (blocked by `check_state`), the warning is suppressed because `has_reached_consensus()` is `true`, but execution still reaches `create_block_acceptance`/`handle_block_signature`/`send_block_response`, and S broadcasts a valid `Accepted` signature over a block it has already recorded as dead.

I could not directly view the bodies of `mark_locally_accepted`, `has_reached_consensus`, or `create_block_acceptance`'s full signing precondition within the remaining tool budget (only `check_state`/`move_to` and the call site were confirmed), so the exact wording/semantics of `has_reached_consensus()` (beyond what's inferable from `check_state`) and any additional undiscovered guard inside `mark_locally_accepted` itself remain unverified from source; this should be double-checked against the full function bodies in `stacks-signer/src/signerdb.rs` before treating this as fully confirmed.

### Citations

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

**File:** stacks-signer/src/signerdb.rs (L331-341)
```rust
    /// Attempt to transition the block state
    pub fn move_to(&mut self, state: BlockState) -> Result<(), String> {
        if !self.check_state(state) {
            return Err(format!(
                "Invalid state transition from {} to {state}",
                self.state
            ));
        }
        self.state = state;
        Ok(())
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
