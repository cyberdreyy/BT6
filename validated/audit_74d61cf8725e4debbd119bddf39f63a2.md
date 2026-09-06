### Title
Late `BlockValidateOk` re-arms pre-commit/signing for a block already `GloballyRejected`, due to swallowed `mark_pre_committed` error - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`handle_block_validate_ok` treats the error returned by `BlockInfo::mark_pre_committed()` as recoverable whenever the block has "reached consensus" or is `LocallyAccepted`, but the guard condition also silently swallows the error when the block's state is already the terminal `GloballyRejected`. Because `mark_pre_committed()` mutates `valid`/`approved_time` *before* checking whether the state transition is legal, a stale, delayed validation response for a block that other signers already drove to `GloballyRejected` will still cause this signer to broadcast a pre-commit and invoke `handle_block_pre_commit` (which can go on to sign), directly contradicting the documented invariant that `GloballyRejected` is terminal.

### Finding Description
`BlockState` is defined with two terminal states, `GloballyAccepted` and `GloballyRejected` [1](#0-0) , and the project's own flow documentation states both terminal states go to `[*]` with no further transitions [2](#0-1) .

`BlockInfo::mark_pre_committed` is implemented as:
```
pub fn mark_pre_committed(&mut self) -> Result<(), String> {
    self.valid = Some(true);
    self.approved_time.get_or_insert(get_epoch_time_secs());
    self.move_to(BlockState::PreCommitted)
}
``` [3](#0-2) 

Note that `self.valid` and `self.approved_time` are mutated unconditionally, *before* the legality of the transition is checked. `move_to` enforces that `PreCommitted` may only be reached from `Unprocessed`:
```
BlockState::PreCommitted => matches!(prev_state, BlockState::Unprocessed),
``` [4](#0-3) 

So calling `mark_pre_committed()` on a block whose state is `GloballyRejected` (or `GloballyAccepted`) returns `Err`, but the side effects (`valid = Some(true)`, `approved_time` set) have already been applied in memory, and `has_reached_consensus()` on that block returns `true` [5](#0-4) .

In `handle_block_validate_ok`, the error from `mark_pre_committed` is handled as follows:
```
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
let address = self.stacks_address.clone();
self.handle_block_pre_commit(
    stacks_client,
    sortition_state,
    &address,
    signer_signature_hash,
);
``` [6](#0-5) 

The abort condition `!has_reached_consensus() && state != LocallyAccepted` is only true (causing an early `return`) when the prior state is `LocallyRejected`. When the prior state is `GloballyRejected` (or `GloballyAccepted`), `has_reached_consensus()` is `true`, so the code does **not** return — instead it persists `block_info` with `valid = Some(true)` (state remaining `GloballyRejected` since `move_to` failed), then unconditionally calls `send_block_pre_commit` and `handle_block_pre_commit`, re-entering the pre-commit/signing pipeline for a block the local signer's own DB already recorded as terminally rejected by the network.

This is reachable purely by event timing/ordering that a one-slot miner plus normal gossip can create: propose a block, have peer signers' rejections/pre-commit-driven rejection tally push this signer's local `BlockInfo` to `GloballyRejected` (via the group-rejection path) while this signer's own node-side validation of the same block is still outstanding; when the delayed `BlockValidateResponse::Ok` for that same `signer_signature_hash` finally arrives, `handle_block_validate_ok` runs, `block_info.valid` is still `None` (the earlier check at line 1932 only skips if `valid.is_some()`), and the flawed guard lets a re-validated, terminal-state block flow back into `send_block_pre_commit`/`handle_block_pre_commit`.

### Impact Explanation
This breaks the "GloballyRejected is terminal" invariant that the rest of the signer state machine (and its documentation) relies on: `GloballyRejected --> [*]`. Re-entering `handle_block_pre_commit` for such a block can cause the signer to broadcast a pre-commit and, if its own pre-commit plus already-recorded peer pre-commits cross the weight threshold, ultimately call the signing path (`mark_locally_accepted` / `handle_block_signature`) for a block the network already rejected. That is a signer contributing a valid signature to a block that lost consensus — a conflicting/non-canonical signature contributing to the pre-commit weight tally, which the audit's bug class (silently-swallowed error leaving the actor to act on stale/invalid data) directly maps onto.

### Likelihood Explanation
The trigger requires only ordinary timing skew between (a) the local node's `/v3/block_proposal` validation completing late and (b) peers' rejection messages/pre-commit tally reaching the reject-threshold first for the same block — both are normal race conditions in the existing event-driven design, not requiring a signer majority, key compromise, or the auth token. It is plausible under network delay/latency between multiple signers running independent stacks-nodes, which the flow doc itself calls out as a known race ("we may have signed a different block... between validation and threshold, the world must be re-checked").

### Recommendation
- In `handle_block_validate_ok`, replace the loose recoverable-error heuristic with an explicit check: only continue past a `mark_pre_committed` error if the prior state was `LocallyAccepted`/`PreCommitted` (races that are actually safe to no-op on); if the prior state is `GloballyRejected` or `GloballyRejected`-adjacent, return immediately without calling `send_block_pre_commit`/`handle_block_pre_commit`.
- Make `BlockInfo::mark_pre_committed` (and the other `mark_*` helpers) transactional: only set `valid`/`approved_time` after `move_to` succeeds, so a failed transition never leaves side effects applied to `block_info` that get persisted to the DB.
- Add an explicit early check at the top of `handle_block_validate_ok`/`handle_block_validate_reject` for `block_info.has_reached_consensus()` (in addition to the existing `valid.is_some()` check), and short-circuit unconditionally in that case.

### Proof of Concept
Not independently reproduced with a runnable test in this pass (tool budget exhausted before locating/inspecting `handle_block_pre_commit`'s full body and the exact call sites of `mark_globally_rejected` to confirm the precise `valid=None` + `state=GloballyRejected` combination is reachable via message ordering alone). The control-flow analysis above is grounded directly in the cited `handle_block_validate_ok`, `mark_pre_committed`, `move_to`/`check_state`, and `has_reached_consensus` code; confirming end-to-end exploitability would require tracing `handle_block_rejection`/`mark_globally_rejected` call sites and `handle_block_pre_commit`'s threshold-crossing logic, which should be validated with a targeted unit/integration test (e.g., driving a `BlockInfo` to `GloballyRejected` via simulated peer rejections, then delivering a late `BlockValidateResponse::Ok` for the same block and asserting no pre-commit/signature is emitted).

### Citations

**File:** stacks-signer/src/signerdb.rs (L112-127)
```rust
define_u8_enum!(
/// Block state relative to the signer's view of the stacks blockchain
BlockState {
    /// The block has not yet been processed by the signer
    Unprocessed = 0,
    /// The block is accepted by the signer but a threshold of signers has not yet signed it
    LocallyAccepted = 1,
    /// The block is rejected by the signer but a threshold of signers has not accepted/rejected it yet
    LocallyRejected = 2,
    /// A threshold number of signers have signed the block
    GloballyAccepted = 3,
    /// A threshold number of signers have rejected the block
    GloballyRejected = 4,
    /// The block is pre-committed by the signer, but not yet signed
    PreCommitted = 5
});
```

**File:** stacks-signer/src/signerdb.rs (L272-277)
```rust
    /// Mark this block as valid, record the approved time timestamp if not already set and attempt to mark it as pre-committed.
    pub fn mark_pre_committed(&mut self) -> Result<(), String> {
        self.valid = Some(true);
        self.approved_time.get_or_insert(get_epoch_time_secs());
        self.move_to(BlockState::PreCommitted)
    }
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

**File:** stacks-signer/src/signerdb.rs (L344-349)
```rust
    pub fn has_reached_consensus(&self) -> bool {
        matches!(
            self.state,
            BlockState::GloballyAccepted | BlockState::GloballyRejected
        )
    }
```

**File:** docs/signer-flows.md (L146-148)
```markdown
    LocallyAccepted --> GloballyAccepted : mark_globally_accepted
    LocallyRejected --> GloballyRejected : mark_globally_rejected
    GloballyAccepted --> [*]
```

**File:** stacks-signer/src/v0/signer.rs (L1961-1984)
```rust
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
