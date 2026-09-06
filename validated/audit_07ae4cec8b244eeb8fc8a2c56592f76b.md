## Title
Signer broadcasts a signed block to the node even when the local `BlockInfo` state transition to `LocallyAccepted` is rejected (e.g. from a terminal `GloballyRejected` state) — a rejection-guard bypassed on the critical push path (`File: stacks-signer/src/v0/signer.rs`)

### Summary
`store_and_process_block_signature` computes signature weight, and once the acceptance threshold is reached it calls `block_info.mark_locally_accepted(true)`, which internally calls `move_to(BlockState::LocallyAccepted)` — a check that is supposed to enforce the block-state equality invariant (`BlockInfo::check_state`, which forbids moving into `LocallyAccepted` from a terminal `GloballyRejected` state). However, the function ignores the `Err` result of this check for any block that `has_reached_consensus()`, and unconditionally proceeds to `self.broadcast_signed_block(...)`, which calls `handle_post_block` and posts the block to the stacks-node via `stacks_client.post_block(block)` regardless of whether the state transition actually succeeded.

### Finding Description
The state machine in `stacks-signer/src/signerdb.rs` defines a strict equality/ordering invariant between local and global outcomes via `BlockInfo::check_state` (`stacks-signer/src/signerdb.rs:314-329`):

```rust
BlockState::LocallyAccepted | BlockState::LocallyRejected => !matches!(
    prev_state,
    BlockState::GloballyRejected | BlockState::GloballyAccepted
),
```

This means once a block reaches `GloballyRejected` (or `GloballyAccepted`), `move_to` refuses any further local-state overwrite — the intent is clearly that a globally-terminal decision cannot be silently flipped by a late local re-evaluation.

`store_and_process_block_signature` (`stacks-signer/src/v0/signer.rs:2442-2538`) is the *only* place a signer assembles/aggregates acceptance signatures and pushes the block to the node. After tallying weight and crossing `min_weight`, it does:

```rust
if let Err(e) = block_info.mark_locally_accepted(true) {
    if !block_info.has_reached_consensus() {
        warn!("{self}: Failed to mark block as locally accepted: {e:?}");
    }
}
let _ = self.signer_db.insert_block(block_info).map_err(...);
self.broadcast_signed_block(stacks_client, block_info.block.clone(), &addrs_to_sigs);
```

The `Err(e)` branch from `mark_locally_accepted` is swallowed for exactly the case where `has_reached_consensus()` is true (i.e., the block is already `GloballyAccepted` or `GloballyRejected`) — the branch only logs a warning otherwise. Crucially, execution does **not** return early in either branch: `broadcast_signed_block` → `handle_post_block` → `stacks_client.post_block(block)` runs unconditionally, using the signatures gathered in `addrs_to_sigs`.

This reproduces the report's bug class exactly: a guard (`onlyActiveProtocol`-equivalent here is `check_state`/`move_to`) exists and is correctly *defined*, but the critical state-changing/externally-visible action (pushing an aggregated signature set to the node, i.e. the local analogue of "this block is accepted") is not gated on the guard's outcome. The check is computed, its failure is even partially handled (for logging), but the actual operation proceeds regardless — the equality "signed vs. validated/consensus-state" is broken at the point of use.

### Impact Explanation
This falls under "a rejection recounted as an accept": if a block this signer previously marked `GloballyRejected` (>30% weight rejected it — see `store_and_process_block_rejection`, `stacks-signer/src/v0/signer.rs:2274-2341`) subsequently receives enough late/duplicate acceptance signatures to cross `min_weight` (e.g., because rejections are documented as "revocable opinions" — see the comment and test at `stacks-signer/src/signerdb.rs` around `has_signed_block` — signers can rescind a rejection and sign later), the local state machine correctly refuses to transition (`check_state` blocks `GloballyRejected -> LocallyAccepted`), yet the code still calls `broadcast_signed_block`, submitting the assembled signature set to this signer's own node via `POST /v3/blocks` (`handle_post_block`). This can cause a node to accept and process a block its own signer's state machine considers globally rejected, undermining the safety property that a block only becomes canonical once the local state machine — not just raw weight arithmetic — agrees it is accepted. In the worst case this is the "rejection recounted as acceptance" scenario explicitly named as a Critical-severity impact category.

### Likelihood Explanation
Reaching this path does not require a signer majority or foreign keys — it only requires:
1. A block first crossing the rejection threshold (`store_and_process_block_rejection`), setting `GloballyRejected`.
2. The same block later gaining enough (re-broadcast or late-arriving) acceptance signatures to cross `min_weight` in `store_and_process_block_signature`, at the exact same local signer instance.
This is reachable purely by ordinary gossip/relay timing of `BlockResponse` messages across the network (which any single relaying party, including a miner or another peer, can influence by delaying/re-ordering messages) and does not require a majority of signers to be malicious — the signer's own accounting of already-received votes, replayed via `process_pending_responses_for_block`/`add_block_signature`, is sufficient to trigger the crossing after a prior rejection tally. Likelihood is Medium: it depends on natural or adversarial message timing/reordering rather than a distinct majority-controlled action, and the underlying weight math is normally complementary, but the "rejections are revocable" design explicitly documented in this codebase makes the scenario reachable without breaking any cryptographic assumption.

### Recommendation
In `store_and_process_block_signature`, if `mark_locally_accepted` returns `Err`, return early (do not call `insert_block`/`broadcast_signed_block`) whenever the failure is due to the block already being in a terminal state (`GloballyRejected`/`GloballyAccepted`) that disagrees with the intended transition. Concretely:

```rust
if let Err(e) = block_info.mark_locally_accepted(true) {
    if block_info.has_reached_consensus() {
        // state already terminal and disagrees — do not broadcast
        warn!("{self}: refusing to broadcast: block state conflicts with prior consensus: {e:?}");
        return;
    }
    warn!("{self}: Failed to mark block as locally accepted: {e:?}");
}
```
This ties the externally-visible push action to the success of the local state-machine guard, restoring the equality between "signed/aggregated" and "state-machine validated."

### Proof of Concept
Not independently executed (no filesystem/terminal access in this mode); the trace below is derived purely from static analysis of the cited functions:
1. Miner proposes block `B`. Signer validates it, pre-commits, and signs — some subset of signers eventually accumulates >30% rejection weight for `B` (e.g. due to a competing block winning consensus), causing this signer to call `store_and_process_block_rejection` → `block_info.mark_globally_rejected()` → state becomes `GloballyRejected` (`stacks-signer/src/v0/signer.rs:2335-2341`).
2. Due to message replay/relay timing (`process_pending_responses_for_block`, or late `BlockResponse::Accepted` messages still in flight), this signer later processes enough `BlockResponse::Accepted` messages for the *same* `B` via `store_and_process_block_signature`, crossing `min_weight` in the acceptance tally (`stacks-signer/src/v0/signer.rs:2503-2514`).
3. `block_info.mark_locally_accepted(true)` is called; `move_to(LocallyAccepted)` fails via `check_state` because `prev_state == GloballyRejected` (`stacks-signer/src/signerdb.rs:314-329`).
4. The `Err` is discarded because `block_info.has_reached_consensus()` is true, so no early return occurs.
5. `self.broadcast_signed_block(...)` → `self.handle_post_block(...)` → `stacks_client.post_block(&block)` executes, submitting the block (with the aggregated signatures) to the node, even though this signer's own state machine holds `GloballyRejected` for it (`stacks-signer/src/v0/signer.rs:2528-2537`, `2559-2560`, `2568-2582`). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** stacks-signer/src/v0/signer.rs (L2274-2341)
```rust
    ) {
        let block_hash = &block_info.signer_signature_hash();
        // We should still store signatures even on consensus reached blocks for auditing purposes.
        // signature is valid! store it
        match self.signer_db.add_block_rejection_signer_addr(
            block_hash,
            signer_address,
            reject_reason,
        ) {
            Err(e) => {
                warn!("{self}: Failed to save block rejection signature: {e:?}",);
            }
            Ok(false) => return, // We already have this signature, do not process it again.
            Ok(true) => (),
        }

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

**File:** stacks-signer/src/v0/signer.rs (L2525-2538)
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
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2540-2560)
```rust
    fn broadcast_signed_block(
        &mut self,
        stacks_client: &StacksClient,
        mut block: NakamotoBlock,
        addrs_to_sigs: &HashMap<StacksAddress, MessageSignature>,
    ) {
        #[cfg(any(test, feature = "testing"))]
        self.test_pause_block_broadcast(&block);

        // collect signatures for the block
        let signatures: Vec<_> = self
            .signer_addresses
            .iter()
            .filter_map(|addr| addrs_to_sigs.get(addr).cloned())
            .collect();

        block.header.signer_signature_hash();
        block.header.signer_signature = signatures;

        self.handle_post_block(stacks_client, &block);
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2562-2583)
```rust
    /// Attempt to post a block to the stacks-node and handle the result
    pub fn handle_post_block(&mut self, stacks_client: &StacksClient, block: &NakamotoBlock) {
        #[cfg(any(test, feature = "testing"))]
        if self.test_skip_block_broadcast(block) {
            return;
        }
        let block_hash = block.header.signer_signature_hash();
        match stacks_client.post_block(block) {
            Ok(accepted) => {
                debug!("{self}: Block {block_hash} accepted by stacks node: {accepted}");
                if let Err(e) = self
                    .signer_db
                    .set_block_broadcasted(&block_hash, get_epoch_time_secs())
                {
                    warn!("{self}: Failed to set block broadcasted for {block_hash}: {e:?}");
                }
            }
            Err(e) => {
                warn!("{self}: Failed to broadcast block {block_hash} to the node: {e}")
            }
        }
    }
```

**File:** stacks-signer/src/signerdb.rs (L272-329)
```rust
    /// Mark this block as valid, record the approved time timestamp if not already set and attempt to mark it as pre-committed.
    pub fn mark_pre_committed(&mut self) -> Result<(), String> {
        self.valid = Some(true);
        self.approved_time.get_or_insert(get_epoch_time_secs());
        self.move_to(BlockState::PreCommitted)
    }

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

    /// Mark this block's signed group time if not already set and attempt to mark it as globally accepted.
    pub fn mark_globally_accepted(&mut self) -> Result<(), String> {
        self.signed_group.get_or_insert(get_epoch_time_secs());
        self.move_to(BlockState::GloballyAccepted)
    }

    /// Mark this block as invalid and attempt to mark it as locally rejected
    pub fn mark_locally_rejected(&mut self) -> Result<(), String> {
        self.valid = Some(false);
        self.move_to(BlockState::LocallyRejected)
    }

    /// Attempt to mark the block as globally rejected
    pub fn mark_globally_rejected(&mut self) -> Result<(), String> {
        self.move_to(BlockState::GloballyRejected)
    }

    /// Return the block's signer signature hash
    pub fn signer_signature_hash(&self) -> Sha512Trunc256Sum {
        self.block.header.signer_signature_hash()
    }

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
