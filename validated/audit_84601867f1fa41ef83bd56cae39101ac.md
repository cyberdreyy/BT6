### Title
`mark_pre_committed` sets `valid = Some(true)` before the fallible state transition, letting a signer sign a block it has already recorded as `GloballyRejected` - (File: `stacks-signer/src/signerdb.rs`, `stacks-signer/src/v0/signer.rs`)

### Summary
`BlockInfo::mark_pre_committed` mutates `valid` and `approved_time` unconditionally and only afterwards attempts the fallible `move_to(BlockState::PreCommitted)` transition, exactly like `Booster::shutdownPool()` setting `pool.shutdown = true` regardless of whether the guarded `withdrawAll()` try/catch actually succeeded. The caller in `handle_block_validate_ok` treats the resulting error as ignorable whenever `has_reached_consensus()` is true, without distinguishing `GloballyAccepted` (harmless, just a stale metadata update) from `GloballyRejected` (a terminal state that must never regain a valid signature path). The tainted `valid = Some(true)` is then persisted and later trusted by `handle_block_pre_commit`'s validity guard, letting the signer proceed to actually sign and broadcast an `Accepted` `BlockResponse` for a block already recorded as globally rejected.

### Finding Description
`mark_pre_committed` performs its side effects before the transition check: [1](#0-0) 

`move_to` can fail (returns `Err`) when the prior state is not `Unprocessed`, per `check_state`: [2](#0-1) 

In `handle_block_validate_ok`, when `mark_pre_committed()` errors, the code only bails out if the block has *not* reached consensus and is not `LocallyAccepted` — it does not distinguish `GloballyRejected` from `GloballyAccepted`: [3](#0-2) 

If the block was independently pushed to `GloballyRejected` by peer rejection weight (`store_and_process_block_rejection` → `mark_globally_rejected`, which never touches `valid`) before this signer's own node returns its (asynchronous) validation verdict, then when that verdict arrives as `Ok`:
- `block_info.valid` is still `None` (guard at signer.rs:1932 does not trigger),
- `mark_pre_committed()` sets `valid = Some(true)` and `approved_time`, then fails to move the state (still `GloballyRejected`),
- because `has_reached_consensus()` is `true`, the code does **not** return, and instead persists the corrupted `block_info` (`valid = Some(true)`, `state = GloballyRejected`) and calls `handle_block_pre_commit` for our own address.

`handle_block_pre_commit`'s validity guard trusts the tainted flag rather than the real block state: [4](#0-3) 

Passing that guard, the function proceeds through the chainstate/conflict checks and reaches the signing step, where the exact same non-return-on-consensus pattern is repeated for `mark_locally_accepted`: [5](#0-4) 

Because `state == GloballyRejected` already satisfies `has_reached_consensus()`, the `Err` from `mark_locally_accepted` (transition from `GloballyRejected` to `LocallyAccepted` is disallowed by `check_state`) is silently swallowed, and execution falls through to `create_block_acceptance` + `handle_block_signature` + `send_block_response(accepted.into())` — the signer signs and broadcasts an acceptance for a block it has already recorded as globally rejected.

### Impact Explanation
This breaks the "rejection recounted as an accept" invariant explicitly called out as Critical: `GloballyRejected` is supposed to be terminal (`check_state` forbids any further transition away from it), yet the tainted `valid` flag and the blanket `has_reached_consensus()` fallthrough let the signer emit a real signature/acceptance response for that same block. Downstream, `store_and_process_block_signature` counts this vote toward the 70% signing threshold together with any signatures from other signers similarly racing the async validation path, which can push a block that the network had already begun finalizing as rejected back toward broadcast/acceptance — a genuine safety violation, not merely a metadata inconsistency.

### Likelihood Explanation
The trigger requires only ordinary asynchronous timing already anticipated by the code's own comment ("The block may have reached enough signatures before we validated the block..."), but the guard only accounts for the accept side of that race, not the reject side. All that is needed is: (a) enough independent peer rejections to cross the >30% weight threshold and call `mark_globally_rejected` on this signer's local copy, and (b) this signer's own (slower) node validation response for the same block subsequently returning `Ok`. No majority coordination, no other signer's key, and no StackerDB-transport-level exploit is required — it is a plain consequence of concurrent validation and gossip that any node in the network can incidentally trigger.

### Recommendation
- In `BlockInfo::mark_pre_committed` (and any similar `mark_*` helper), only mutate `valid`/timestamps after `move_to` succeeds, or roll the mutation back on `Err`, mirroring the C4 report's suggested fix of gating the "shutdown" side-effect on the actual success of the guarded operation.
- In `handle_block_validate_ok` and `handle_block_pre_commit`, replace the blanket `has_reached_consensus()` fallthrough with an explicit check that only tolerates `GloballyAccepted` (where signing metadata truly is moot) and always bails out — without persisting the failed mutation — when the state is `GloballyRejected`.
- In `handle_block_pre_commit`'s validity guard (`!block_info.valid.unwrap_or(false)`), also check `!block_info.has_reached_consensus()` (or specifically `state != GloballyRejected`) before proceeding, instead of relying solely on the `valid` boolean, which can be corrupted by the above race.

### Proof of Concept
1. Signer `S` receives a `BlockProposal` for block `B` and submits it to its node for validation (`submit_block_for_validation`), and stores `block_info` (state `Unprocessed`, `valid = None`).
2. Before `S`'s node responds, enough other signers independently reject `B` (e.g., legitimate rejections crossing >30% weight) such that `store_and_process_block_rejection` → `mark_globally_rejected` sets `S`'s local `block_info.state = GloballyRejected` (see `stacks-signer/src/v0/signer.rs` `handle_block_response`/`handle_block_rejection` path referenced in `docs/signer-flows.md:349-375`). Note `mark_globally_rejected` never touches `valid`, so it remains `None`.
3. `S`'s node finally returns `BlockValidateResponse::Ok` for `B`. `handle_block_validate_response` → `handle_block_validate_ok` is invoked (`stacks-signer/src/v0/signer.rs:1888`). `block_info.valid.is_some()` is `false`, so the early-out guard at line 1932 does not fire.
4. `check_block_against_signer_db_state` returns `None` (the chainstate-tip checks are independent of the gossip-driven rejection tally), so the `else` branch at line 1960 runs `mark_pre_committed()`.
5. `mark_pre_committed` sets `valid = Some(true)` and `approved_time`, then `move_to(PreCommitted)` fails because `state == GloballyRejected` (`check_state` disallows this). Because `has_reached_consensus()` is `true`, the guard at lines 1964-1969 does not return.
6. `S` persists the corrupted `block_info` (`valid = Some(true)`, `state = GloballyRejected`) via `insert_block`, then calls `send_block_pre_commit` and `handle_block_pre_commit` for its own address.
7. Inside `handle_block_pre_commit`, the guard `if !block_info.valid.unwrap_or(false) { return; }` (line 1323) is bypassed because `valid == Some(true)`. If the pre-commit weight threshold is already met (plausible if most other signers had validated the block before rejecting/committing, or via `S`'s own pre-commit combined with existing ones), execution reaches `mark_locally_accepted(false)` at line 1467, which also fails silently (state can't move from `GloballyRejected`) but again does not return.
8. `S` proceeds to `create_block_acceptance`, `handle_block_signature`, and `send_block_response(accepted.into())` — broadcasting a real `Accepted` signature for a block `S` had already recorded internally as `GloballyRejected`.

### Citations

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

**File:** stacks-signer/src/v0/signer.rs (L1960-1975)
```rust
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
```
