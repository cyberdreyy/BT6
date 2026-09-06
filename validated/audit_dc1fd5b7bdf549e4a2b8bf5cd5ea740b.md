## Finding

### Title
Signer broadcasts a fully-signed block after failing to record it as accepted, silently discarding the state-transition error - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`store_and_process_block_signature` computes the aggregate signature weight for a block and, once the 70% threshold is crossed, tries to transition the block's local bookkeeping to `LocallyAccepted` before broadcasting the fully-signed block to the node. The `Result` of that transition is checked, but the failure branch only logs a warning when the block has *not* already reached a terminal (global) state — meaning that in the one case that actually matters (the block was already recorded `GloballyRejected`), the error is swallowed with no log at all, and execution falls straight through to persisting the block and calling `broadcast_signed_block`, which pushes the assembled signature set to the node regardless.

### Finding Description
The relevant code: [1](#0-0) 

```rust
// have enough signatures to broadcast!
// move block to LOCALLY accepted state.
// It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
if let Err(e) = block_info.mark_locally_accepted(true) {
    if !block_info.has_reached_consensus() {
        warn!("{self}: Failed to mark block as locally accepted: {e:?}");
    }
}
let _ = self.signer_db.insert_block(block_info).map_err(|e| {
    warn!("Failed to set group threshold signature timestamp for {block_hash}: {e:?}");
    panic!("{self} Failed to write block to signerdb: {e}");
});
self.broadcast_signed_block(stacks_client, block_info.block.clone(), &addrs_to_sigs);
```

`mark_locally_accepted` can fail specifically when the block is already `GloballyRejected`, per `BlockInfo::check_state`: [2](#0-1) 

```rust
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
        ...
    }
}
```

and `has_reached_consensus()` is exactly `GloballyAccepted | GloballyRejected`: [3](#0-2) 

So the `if !block_info.has_reached_consensus() { warn!(...) }` guard means: when the block was already `GloballyRejected` on this signer (a >30% weighted rejection tally previously drove `mark_globally_rejected`, see the "6. Responses from other signers" flow in `docs/signer-flows.md:349-388`), the failure of `mark_locally_accepted` is not even logged — the function proceeds unconditionally to `insert_block` and `broadcast_signed_block`.

Crucially, the acceptance-signature tally (`block_signatures` table, checked via `get_block_signatures` / `compute_signature_signing_weight`) and the rejection tally (`block_rejection_signer_addrs` table, tallied elsewhere) are independent counters with no cross-invalidation: nothing in `store_and_process_block_signature` checks the current `block_info.state` before assembling and pushing signatures. Signature messages (`BlockAccepted`) are individually authenticated (`is_valid_signer`, `recover_to_pubkey_without_validating_low_s`) but are accepted into the tally regardless of whether the block has already been driven to `GloballyRejected` by this same signer.

### Impact Explanation
This breaks the "rejection recounted as accept" invariant the reward-set threshold scheme is supposed to guarantee: once a signer's local view has firmly decided a block is dead (`GloballyRejected`, reached via a blocking >30%-weight rejection), that signer should never again act to finalize the same block. Instead, if a set of individually-valid but stale/late `Accepted` signatures (e.g. from signers who voted before later changing their mind, or replayed by a malicious/faulty gossip relay — both within the stated scope of "a one-slot miner plus gossip") arrive after the local `GloballyRejected` verdict and independently cross the 70% signature-weight threshold, the signer still calls `broadcast_signed_block` → `handle_post_block` → `stacks_client.post_block`, submitting a fully-signed block to the node for finalization. This is exactly the "signer signing/pushing a conflicting or already-rejected block" / "rejection recounted as acceptance" class of Critical impact defined in scope, since it can drive the node to accept a block the signer set (or this signer) had already determined should not proceed, undermining the single-decision guarantee of the accept/reject threshold state machine.

### Likelihood Explanation
Triggering this requires no compromise of a majority of signer keys or of `auth_token`/local access — the accept-weight and reject-weight tallies are separate and unsynchronized within the signer, and the state-transition failure path already exists specifically to catch this case (it correctly warns for other failure combinations, only failing to react when the block already reached global consensus). All that is required is for the independently-tallied signature set (built entirely from valid, previously-seen `BlockAccepted` messages) to be delivered/registered after this signer's rejection tally already crossed the blocking-minority threshold for the same block — plausible via ordinary gossip/message-timing variance or replay, both explicitly in-scope trigger vectors.

### Recommendation
Before broadcasting in `store_and_process_block_signature`, explicitly bail out (rather than merely deciding whether to log) whenever `mark_locally_accepted` fails or whenever `block_info.has_reached_consensus()` is already true and the reached state is `GloballyRejected`. i.e., treat the `Err` from `move_to`/`mark_locally_accepted` as authoritative and return immediately instead of falling through to `insert_block` / `broadcast_signed_block`, exactly as `add_block_signature`'s `false` return is already handled earlier in the same function (line 2454-2460).

### Proof of Concept
1. Node proposes block `B` at height `h`; signer `S` validates it and gathers rejection votes from a blocking (>30% weight) subset of the signer set for a legitimate reason (e.g. a reorg/conflict check failure), driving `S`'s local `BlockInfo` for `B` to `GloballyRejected` via `mark_globally_rejected` (per the rejection flow in `docs/signer-flows.md:349-388`).
2. A set of individually-valid `BlockAccepted` messages for the same `signer_signature_hash` (from signers who signed earlier, or replayed by gossip) arrive at `S` and are stored via `add_block_signature`, with their aggregate weight crossing the 70% threshold (`total_signature_weight >= min_weight` at `stacks-signer/src/v0/signer.rs:2503`).
3. `store_and_process_block_signature` calls `block_info.mark_locally_accepted(true)` (`stacks-signer/src/v0/signer.rs:2528`); this returns `Err` because `check_state` forbids `GloballyRejected -> LocallyAccepted`.
4. Because `block_info.has_reached_consensus()` is `true`, the warning is suppressed and no early return occurs; the code proceeds to `insert_block` and `broadcast_signed_block` (`stacks-signer/src/v0/signer.rs:2533-2537`), pushing the full signature set for `B` to the node via `handle_post_block`/`stacks_client.post_block`, even though `S`'s own database still marks `B` as `GloballyRejected`.

This finding could not be fully validated against a live multi-signer network trace (no runtime environment available in this analysis); the control-flow and state-machine logic above is confirmed directly from source, but the precise network timing needed to reliably reproduce the race (rejection crossing 30% before/concurrently with signature crossing 70%) would need to be exercised in an integration test harness such as `stacks-node/src/tests/signer/v0/mod.rs` to fully confirm exploitability end-to-end.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L2525-2537)
```rust
        // have enough signatures to broadcast!
        // move block to LOCALLY accepted state.
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
        if let Err(e) = block_info.mark_locally_accepted(true) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}");
            }
        }
        let _ = self.signer_db.insert_block(block_info).map_err(|e| {
            warn!("Failed to set group threshold signature timestamp for {block_hash}: {e:?}");
            panic!("{self} Failed to write block to signerdb: {e}");
        });
        self.broadcast_signed_block(stacks_client, block_info.block.clone(), &addrs_to_sigs);
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

**File:** stacks-signer/src/signerdb.rs (L343-349)
```rust
    /// Check if the block is globally accepted or rejected
    pub fn has_reached_consensus(&self) -> bool {
        matches!(
            self.state,
            BlockState::GloballyAccepted | BlockState::GloballyRejected
        )
    }
```
