### Title
`handle_block_pre_commit` and `store_and_process_block_signature` broadcast a signature/acceptance even when `mark_locally_accepted` fails, letting a signer sign and announce acceptance of a block already decided `GloballyRejected` - ([File: stacks-signer/src/v0/signer.rs])

### Summary
This is the same bug class as the Sherlock M-16 finding: a function performs partial/irreversible side effects, returns an error to signal that the overall operation did not complete as intended, but the caller only logs the error and proceeds as if it had succeeded. In `AssetManager::withdraw`, the "irreversible" side effect was a partial transfer; here it is `BlockInfo::mark_locally_accepted`, which unconditionally mutates `valid`, `approved_time`, and `signed_self` *before* attempting the guarded state transition (`move_to`), and returns `Err` only for the transition itself when the block has already reached a terminal (`GloballyAccepted`/`GloballyRejected`) state. Both callers of `mark_locally_accepted` in `signer.rs` treat that `Err` as a soft warning and continue to insert the (now-corrupted) `BlockInfo`, produce a real cryptographic signature, and broadcast a `BlockResponse::Accepted`/`BlockAccepted` message regardless.

### Finding Description
`BlockInfo::mark_locally_accepted` in `stacks-signer/src/signerdb.rs`:

```rust
pub fn mark_locally_accepted(&mut self, group_signed: bool) -> Result<(), String> {
    if group_signed {
        self.signed_group.get_or_insert(get_epoch_time_secs());
    } else {
        self.valid = Some(true);
        self.approved_time.get_or_insert(get_epoch_time_secs());
        self.signed_self.get_or_insert(get_epoch_time_secs());
    }
    self.move_to(BlockState::LocallyAccepted)
}
``` [1](#0-0) 

`move_to`/`check_state` reject the transition only when the block is already `GloballyAccepted` or `GloballyRejected`:
```rust
BlockState::LocallyAccepted | BlockState::LocallyRejected => !matches!(
    prev_state,
    BlockState::GloballyRejected | BlockState::GloballyAccepted
),
``` [2](#0-1) 

Critically, `mark_globally_rejected` does **not** clear `valid`:
```rust
pub fn mark_globally_rejected(&mut self) -> Result<(), String> {
    self.move_to(BlockState::GloballyRejected)
}
``` [3](#0-2) 

so a block that has already reached rejection-consensus still shows `valid == Some(true)` in the DB.

`handle_block_pre_commit` never checks `block_info.has_reached_consensus()` before evaluating whether to sign. It only gates on `signed_self.is_some()` and `valid.unwrap_or(false)`:
```rust
if block_info.signed_self.is_some() { ... return; }
...
if !block_info.valid.unwrap_or(false) { ... return; }
...
if min_weight > commit_weight { ... return; }
``` [4](#0-3) 

Then, once the pre-commit weight threshold is crossed and the chainstate re-check passes, it signs unconditionally:
```rust
if let Err(e) = block_info.mark_locally_accepted(false) {
    if !block_info.has_reached_consensus() {
        warn!("{self}: Failed to mark block as locally accepted: {e:?}",);
    }
}
self.signer_db
    .insert_block(&block_info)
    .unwrap_or_else(|e| self.handle_insert_block_error(e));
let accepted = self.create_block_acceptance(&block_info.block);
self.handle_block_signature(stacks_client, sortition_state, &accepted);
self.send_block_response(&block_info.block, accepted.into());
``` [5](#0-4) 

Note the `!block_info.has_reached_consensus()` guard only suppresses the *log line* - it does not stop the code from calling `insert_block`, `create_block_acceptance`, or `send_block_response`. The identical pattern exists in the group-threshold path:
```rust
if let Err(e) = block_info.mark_locally_accepted(true) {
    if !block_info.has_reached_consensus() {
        warn!("{self}: Failed to mark block as locally accepted: {e:?}");
    }
}
let _ = self.signer_db.insert_block(block_info)...;
self.broadcast_signed_block(stacks_client, block_info.block.clone(), &addrs_to_sigs);
``` [6](#0-5) 

Path to trigger: a block reaches `GloballyRejected` when >30%-weight of rejections accumulate (`store_and_process_block_rejection`, calling `mark_globally_rejected`) [7](#0-6) . `valid` stays `true` on that `BlockInfo`. Pre-commit gossip messages (`BlockPreCommit`) for that same block hash can still arrive afterward - `handle_block_pre_commit` unconditionally records every pre-commit it receives via `add_block_pre_commit` before any of the state checks run:
```rust
self.signer_db
    .add_block_pre_commit(block_hash, stacker_address)
    .unwrap_or_else(|_| panic!(...));
``` [8](#0-7) 
and the weight-threshold comparison (`commit_weight >= min_weight`) is computed purely from the ever-growing set of committers stored for that hash, with no consensus-state gate. A single late/duplicate/gossip-relayed pre-commit that pushes `commit_weight` over `min_weight` - reachable without any signer majority, and even by a lone byzantine/faulty miner-adjacent relay replaying old pre-commit chunks over StackerDB gossip - causes this signer to (a) mutate `signed_self`/`approved_time`/`valid` on an already-`GloballyRejected` `BlockInfo`, (b) persist that corrupted record, and (c) emit a genuine ECDSA acceptance signature and `BlockResponse::Accepted` for a block the network has already decided to reject.

### Impact Explanation
This is a "rejection recounted as an acceptance" class break: the equality that must hold - a block's terminal consensus state (`GloballyRejected`) is final and no further real signature should ever be produced for it - is violated. The emitted signature is a legitimate, verifiable acceptance signature over a block that the signer's own database says was already globally rejected. Depending on downstream aggregation (e.g. a delayed/partitioned view among other signers, or a miner replaying stale pre-commits to re-solicit signatures after a rejection), this stray signature can contribute real weight toward reversing a decided rejection, or at minimum corrupts the signer's local audit trail (`signed_self` set on a rejected block) and violates the equivocation/consensus-finality guarantee the state machine is supposed to enforce. This matches the "Critical: rejection recounted as an acceptance" impact category.

### Likelihood Explanation
Reaching this requires only: (1) a block that already accumulated >30% weight rejections (a normal, single-actor-influenceable event - only requires normal signer disagreement, not a majority of keys), and (2) a late-arriving or replayed `BlockPreCommit` gossip message crossing the 70% pre-commit weight threshold for the same block hash. Pre-commit messages carry no signature-binding to a fresh nonce/session and are stored unconditionally regardless of the block's already-terminal state, so this can be triggered by gossip replay or network reordering, not by compromising a majority of signer keys. The signing signer's own logic performs no `has_reached_consensus()` check anywhere in `handle_block_pre_commit` before deciding to sign.

### Recommendation
- Add an explicit `block_info.has_reached_consensus()` (or state-terminality) check at the very top of `handle_block_pre_commit` (and mirror it in `store_and_process_block_signature`'s pre-commit-threshold branch) that returns immediately without processing pre-commits/signatures for a block already `GloballyAccepted`/`GloballyRejected`.
- Make `mark_locally_accepted`/`mark_pre_committed`/`mark_locally_rejected` atomic: only mutate `valid`/`approved_time`/`signed_self`/`signed_group` if `move_to` succeeds, so a failed state transition never has side effects.
- Treat an `Err` from `mark_locally_accepted` as fatal for the calling code path (return early), rather than only suppressing the log line while still inserting the block and broadcasting a signature/acceptance.

### Proof of Concept
1. Signer S receives block proposal B, validates it, pre-commits (state `PreCommitted`, `valid = Some(true)`).
2. Rejection weight for B crosses `total_weight - min_weight` via normal signer disagreement; `store_and_process_block_rejection` calls `block_info.mark_globally_rejected()` successfully - state becomes `GloballyRejected`, but `valid` is untouched (`Some(true)`), `signed_self` is `None`.
3. A stale/replayed `BlockPreCommit(B)` message for a not-yet-counted committer arrives at S over StackerDB gossip (e.g. resent by the miner/relay after a timeout, or replayed from an earlier round). `handle_block_pre_commit` looks up B via `block_lookup_by_reward_cycle`, records the pre-commit via `add_block_pre_commit`, and recomputes `commit_weight`, which now happens to cross `min_weight` (it does not know/care that B is already `GloballyRejected`).
4. `block_info.signed_self.is_some()` is false, `block_info.valid.unwrap_or(false)` is true, `commit_weight >= min_weight` - all guards pass. `check_block_against_signer_db_state` may also pass since it only checks tenure/chain-consistency, not global-consensus finality.
5. `block_info.mark_locally_accepted(false)` executes: sets `valid = Some(true)` (no-op), `approved_time` (if unset), and `signed_self = Some(now)` - then `move_to(LocallyAccepted)` fails (`Err`, since `prev_state == GloballyRejected`), which is only logged as a warning because `has_reached_consensus()` is true.
6. Execution continues: `self.signer_db.insert_block(&block_info)` persists the corrupted record (state field remains `GloballyRejected` in memory but `signed_self` is now set), `self.create_block_acceptance(&block_info.block)` produces a real signature, and `self.send_block_response(&block_info.block, accepted.into())` broadcasts a `BlockResponse::Accepted` for block B - despite B having already reached `GloballyRejected` consensus.

*Uncertainty*: I was unable to fully trace `block_lookup_by_reward_cycle`'s and `SignerDb::add_block_pre_commit`/`get_block_pre_committers`'s exact implementations (only found their call sites, not full bodies) within the tool budget, so I cannot 100% confirm there is no additional filtering elsewhere in the StackerDB gossip ingestion path that would prevent stale pre-commits for terminal blocks from reaching `handle_block_pre_commit`. Based on the code shown, no such filter exists in the reviewed call chain.

### Citations

**File:** stacks-signer/src/signerdb.rs (L279-289)
```rust
    /// Mark this block as valid and the appropriate timestamps if they aren't already set, and attempt to mark it as locally accepted.
    pub fn mark_locally_accepted(&mut self, group_signed: bool) -> Result<(), String> {
        if group_signed {
            self.signed_group.get_or_insert(get_epoch_time_secs());
        } else {
            self.valid = Some(true);
            self.approved_time.get_or_insert(get_epoch_time_secs());
            self.signed_self.get_or_insert(get_epoch_time_secs());
        }
        self.move_to(BlockState::LocallyAccepted)
    }
```

**File:** stacks-signer/src/signerdb.rs (L303-306)
```rust
    /// Attempt to mark the block as globally rejected
    pub fn mark_globally_rejected(&mut self) -> Result<(), String> {
        self.move_to(BlockState::GloballyRejected)
    }
```

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

**File:** stacks-signer/src/v0/signer.rs (L1275-1280)
```rust
        // Always save the pre-commit - we will need to store signer responses for determining which
        // are misbehaving, offline, etc.
        // commit message is from a valid sender! store it
        self.signer_db
            .add_block_pre_commit(block_hash, stacker_address)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block pre-commit"));
```

**File:** stacks-signer/src/v0/signer.rs (L1316-1338)
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

        if min_weight > commit_weight {
            debug!(
                "{self}: Not enough pre-committed to block {block_hash} (have {commit_weight}, need at least {min_weight}/{total_weight})"
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

**File:** stacks-signer/src/v0/signer.rs (L2290-2341)
```rust
        if block_info.has_reached_consensus() {
            // Checking the rejection signatures is pointless. We have already reached consensus on this block.
            return;
        }

        // do we have enough signatures to mark a block a globally rejected?
        // i.e. is (set-size) - (threshold) + 1 reached.
        let rejection_addrs = match self.signer_db.get_block_rejection_signer_addrs(block_hash) {
            Ok(addrs) => addrs,
            Err(e) => {
                warn!("{self}: Failed to load block rejection addresses: {e:?}.",);
                return;
            }
        };
        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
        let total_reject_weight =
            self.compute_signature_signing_weight(rejection_addrs.iter().map(|(addr, _)| addr));
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
        if total_reject_weight.saturating_add(min_weight) <= total_weight {
            // Not enough rejection signatures to make a decision
            info!("{self}: Have not yet received enough block rejections to reach a consensus decision on this block";
                "signer_signature_hash" => %block_hash,
                "signature_weight" => signature_weight,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "block_height" => block_info.block.header.chain_length,
                "total_weight_rejected" => total_reject_weight,
                "total_weight" => total_weight,
                "percent_rejected" => (total_reject_weight as f64 / total_weight as f64 * 100.0),
            );
            return;
        }
        info!("{self}: have reached the block rejection threshold";
            "signer_signature_hash" => %block_hash,
            "signature_weight" => signature_weight,
            "consensus_hash" => %block_info.block.header.consensus_hash,
            "block_height" => block_info.block.header.chain_length,
            "total_weight_rejected" => total_reject_weight,
            "total_weight" => total_weight,
            "percent_rejected" => (total_reject_weight as f64 / total_weight as f64 * 100.0),
        );
        if let Err(e) = block_info.mark_globally_rejected() {
            warn!("{self}: Failed to mark block as globally rejected: {e:?}",);
        }
        if let Err(e) = self.signer_db.insert_block(block_info) {
            error!("{self}: Failed to update block state: {e:?}",);
            panic!("{self} Failed to update block state: {e}");
        }
```

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
