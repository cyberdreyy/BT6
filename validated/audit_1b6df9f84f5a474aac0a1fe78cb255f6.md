### Title
Signer silently signs and broadcasts an acceptance for a block already reached GloballyRejected consensus - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
In `handle_block_pre_commit`, once the pre-commit weight threshold is reached, the signer calls `block_info.mark_locally_accepted(false)` to record that it is about to sign. If this call returns an `Err` (which — per `BlockInfo::check_state` — can only happen when the block has already reached a terminal consensus state, i.e. `GloballyAccepted`/`GloballyRejected`), the code only *logs* a warning conditionally and then unconditionally falls through to create and broadcast a signature anyway. This is structurally identical to the reported Berachain bug: an error from a critical validity/state check is discarded and the code proceeds down the "success" path regardless.

### Finding Description
`BlockInfo::check_state` in `stacks-signer/src/signerdb.rs` explicitly forbids moving to `LocallyAccepted` from a terminal state: [1](#0-0) 

`move_to` (called internally by `mark_locally_accepted`) returns `Err` exactly when this guard fires, i.e. only when `has_reached_consensus()` (state is `GloballyAccepted` or `GloballyRejected`) is already `true` for that block/hash.

In `handle_block_pre_commit`, the sign path is: [2](#0-1) 

```rust
if let Err(e) = block_info.mark_locally_accepted(false) {
    if !block_info.has_reached_consensus() {
        warn!("{self}: Failed to mark block as locally accepted: {e:?}",);
    }
}
self.signer_db.insert_block(&block_info)...
let accepted = self.create_block_acceptance(&block_info.block);
self.handle_block_signature(stacks_client, sortition_state, &accepted);
self.send_block_response(&block_info.block, accepted.into());
```

Because `mark_locally_accepted` can only fail when `has_reached_consensus()` is `true`, the inner `warn!` is unreachable dead code, and the `Err` branch is effectively always silently swallowed. The function then proceeds — with no `return` in either branch — to call `create_block_acceptance`, `handle_block_signature`, and `send_block_response`, signing and broadcasting an acceptance for the block regardless of whether the local state machine legally allowed that transition.

This mirrors the Berachain root cause exactly: `err` (or its equivalent state-check failure) is captured, inspected, and then discarded, and the surrounding function proceeds as though the operation succeeded.

### Impact Explanation
This breaks the "rejection recounted as acceptance" invariant explicitly called out as Critical impact. If a tracked block ever reaches `state == GloballyRejected` (the network/local view has already recorded consensus rejecting it) while this signer's `valid` field is still `true` and `signed_self` is still `None` (e.g., the signer validated the block locally as OK and pre-committed to it before the rejection consensus was locally observed, and pre-commit messages for that same hash are still in flight or replayed via `process_pending_responses_for_block`), reaching the pre-commit weight threshold will cause this signer to sign and broadcast a `BlockAccepted` message for a block it has already recorded as `GloballyRejected`. This is a signer producing a conflicting/cross-state-invalid signature — undermining the meaning of a block signature as proof that the signer's state machine sanctioned it, and potentially confusing/poisoning downstream consumers (miners, other signers, chain observers) that treat accumulated signatures as ground truth.

### Likelihood Explanation
Reachable by a single miner/gossip actor without needing a signer majority: an attacker (or a race in ordinary operation) only needs to cause pre-commit messages for a given `signer_signature_hash` to arrive/be replayed (via `add_pending_block_pre_commit_response` → `process_pending_responses_for_block`) after this signer's local `signerdb` state for that block has already moved to `GloballyRejected`, while this signer's own `valid`/`signed_self` fields still permit entry into the sign branch. Given the network's asynchronous message delivery and the code path's explicit support for delayed/pending pre-commit replay, this is a plausible race rather than a purely theoretical one.

### Recommendation
Do not fall through to signing when `mark_locally_accepted` fails. Return early (mirroring the pattern already used for `mark_pre_committed` failures in `handle_block_validate_ok`, but tightened to actually `return`) whenever the state transition is rejected due to `has_reached_consensus()` being true, instead of only conditionally logging and then continuing to sign and broadcast the acceptance.

### Proof of Concept
1. Signer validates a block via `handle_block_validate_ok`; `valid = Some(true)`, state becomes `PreCommitted`.
2. Before pre-commit weight threshold is reached, this signer's local record for the same `signer_signature_hash` gets advanced to `GloballyRejected` (e.g., via processing of enough rejection responses / `mark_globally_rejected`, while `signed_self` remains `None`).
3. Delayed or replayed `BlockPreCommit` messages for that same hash arrive/are drained via `process_pending_responses_for_block`, pushing `commit_weight` over `min_weight` in `handle_block_pre_commit`.
4. Checks `signed_self.is_some()` (false) and `valid.unwrap_or(false)` (true) pass; conflict checks pass (no direct conflicting fresh block); the code reaches `mark_locally_accepted(false)`.
5. `move_to(LocallyAccepted)` fails because `check_state` forbids `GloballyRejected -> LocallyAccepted`; `has_reached_consensus()` is `true`, so the `warn!` inside the `if` is skipped too — the error is fully silent.
6. Execution falls through to `create_block_acceptance`, `handle_block_signature`, and `send_block_response`, broadcasting a valid signed acceptance for a block already recorded by this signer as `GloballyRejected`. [3](#0-2) [4](#0-3)

### Citations

**File:** stacks-signer/src/signerdb.rs (L313-341)
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

**File:** stacks-signer/src/v0/signer.rs (L1316-1331)
```rust
        if block_info.signed_self.is_some() {
            debug!(
                "{self}: Received pre-commit for a block that we have already signed. Doing nothing...",
            );
            return;
        }

        if !block_info.valid.unwrap_or(false) {
            // We received a pre-commit for a block that we have not validated or we have already marked this block as invalid.
            // We should not do anything further as we do not know what our response should be and we do not change our votes on rejected
            // blocks unless we receive a new block proposal for it and the reject reason allows us to reconsider.
            debug!(
                "{self}: Received a pre-commit for a block that we have not determined to be valid: {:?}. Doing nothing...", block_info.valid
            );
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1466-1479)
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
    }
```
