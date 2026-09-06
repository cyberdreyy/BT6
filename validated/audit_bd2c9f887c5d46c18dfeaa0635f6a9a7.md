## Finding

### Title
Ignored `mark_locally_accepted` state-transition failure causes a globally-rejected block to be re-broadcast as accepted — (File: `stacks-signer/src/v0/signer.rs`)

### Summary
In `Signer::store_and_process_block_signature`, the `Result` returned by `BlockInfo::mark_locally_accepted` is examined only to decide whether to log a warning — it is never used to gate the subsequent `insert_block` and `broadcast_signed_block` calls. When the state transition is legally disallowed (the block already sits in the terminal `GloballyRejected` state), `mark_locally_accepted` returns `Err`, the state field is left unchanged, but the function still unconditionally persists the mutated `BlockInfo` and broadcasts the fully-signed block to the node. This is the same bug class as the referenced report: a return value that signals "this operation did not do what the caller assumes" is discarded, and execution proceeds as if it succeeded.

### Finding Description
`store_and_process_block_signature` tallies signature weight and, once the ≥70% acceptance threshold is reached, does: [1](#0-0) 

```rust
if let Err(e) = block_info.mark_locally_accepted(true) {
    if !block_info.has_reached_consensus() {
        warn!("{self}: Failed to mark block as locally accepted: {e:?}");
    }
}
let _ = self.signer_db.insert_block(block_info).map_err(|e| { ... });
self.broadcast_signed_block(stacks_client, block_info.block.clone(), &addrs_to_sigs);
```

`mark_locally_accepted` is: [2](#0-1) 

```rust
pub fn mark_locally_accepted(&mut self, group_signed: bool) -> Result<(), String> {
    if group_signed {
        self.signed_group.get_or_insert(get_epoch_time_secs());
    } else { ... }
    self.move_to(BlockState::LocallyAccepted)
}
```

Note that `self.signed_group` is mutated *before* `move_to` is evaluated, so even a failed transition leaves a `signed_group` timestamp stamped on the in-memory `BlockInfo`.

`move_to`/`check_state` explicitly forbid this transition once the block has reached the terminal `GloballyRejected` state: [3](#0-2) 

```rust
fn check_state(&self, state: BlockState) -> bool {
    ...
    BlockState::LocallyAccepted | BlockState::LocallyRejected => !matches!(
        prev_state,
        BlockState::GloballyRejected | BlockState::GloballyAccepted
    ),
    ...
}
pub fn move_to(&mut self, state: BlockState) -> Result<(), String> {
    if !self.check_state(state) {
        return Err(format!("Invalid state transition from {} to {state}", self.state));
    }
    self.state = state;
    Ok(())
}
```

Because `has_reached_consensus()` is true exactly when `state == GloballyRejected`, the `if !block_info.has_reached_consensus()` guard around the `warn!` call actively *suppresses* the only diagnostic for this exact failure case — so the error is not merely ignored, it is deliberately silenced for the one scenario where ignoring it matters (block already `GloballyRejected`).

The result: a `BlockInfo` whose `state` field remains `GloballyRejected` (unchanged, since `move_to` returned `Err`) but whose `signed_group` timestamp is now stamped, is written back to the signer DB, and `broadcast_signed_block` → `handle_post_block` → `stacks_client.post_block(block)` unconditionally pushes the assembled, fully-signed block to this signer's own stacks-node: [4](#0-3) 

This can be reached without any signer acting maliciously or a majority colluding: it only requires normal network asynchrony among honest signers. A block can accumulate ≥30% rejection weight (crossing the `GloballyRejected` threshold checked in `store_and_process_block_rejection`, [5](#0-4) ) from signers who, e.g., hit `ConnectivityIssues` or a stale chainstate check, while other signers' honest acceptances for the same block arrive later (network delay, retried gossip) and separately cross the ≥70% signature-weight threshold. Both tallies are computed independently from different message streams (`get_block_rejection_signer_addrs` vs. `get_block_signatures`), so there is no built-in mutual exclusion preventing a signer's local view from crossing both thresholds over time — especially since re-evaluation explicitly permits `LocallyRejected → LocallyAccepted` transitions elsewhere in the code (per `docs/signer-flows.md`), meaning the signer set's own DB rows are not append-only per-address decisions.

### Impact Explanation
This breaks the equality between "verified aggregated weight" and "the FSM's recorded local decision": the on-disk `BlockInfo.state` says `GloballyRejected` (this signer's committed verdict) while the code path proceeds exactly as if the block were freshly `LocallyAccepted`, submitting the block to the local stacks-node for chain acceptance and to peers as an accepted block. It is a concrete instance of "a rejection recounted as an acceptance," pushed through to the node's block-acceptance path via `handle_post_block`. This qualifies as a Critical-class impact per the rules (rejection recounted as acceptance) and additionally leaves the persisted `BlockInfo` in an internally inconsistent state (`state = GloballyRejected`, `signed_group = Some(t)`), which downstream logic (`get_tenure_last_block_info`, freshness/timeout checks) may itself misinterpret since it inspects `signed_group`/`signed_self` timestamps independent of `state`.

### Likelihood Explanation
No attacker-controlled majority weight is strictly required — only ordinary network asynchrony causing independent reject/accept weight tallies to each cross their respective thresholds for the same block at the same signer, which is plausible under normal partial-partition or retry conditions in a large signer set. The suppression of the warning specifically in the `has_reached_consensus()` case shows the failure path was anticipated by the code but its consequence (broadcasting anyway) was left unguarded.

### Recommendation
`store_and_process_block_signature` must check the return value of `mark_locally_accepted` and short-circuit before `insert_block`/`broadcast_signed_block` whenever the transition fails while the block has already reached a terminal state (`GloballyRejected`/`GloballyAccepted`). Additionally, `mark_locally_accepted` (and the sibling `mark_*` functions) should not mutate `signed_group`/`signed_self`/`approved_time` before confirming the state transition via `move_to` succeeds — the side effect and the state change must be atomic, e.g. call `move_to` first and only stamp the timestamp fields on success.

### Proof of Concept
1. Signer S receives rejections from a subset of signers totaling ≥30% weight for block B (e.g., due to `ConnectivityIssues`/stale-chainstate rejections), reaching `min_weight` in `store_and_process_block_rejection`, which calls `block_info.mark_globally_rejected()` successfully and persists `state = GloballyRejected`.
2. Independently, delayed but valid acceptance signatures for the same block B continue to arrive at S from other signers and are recorded via `add_block_signature`.
3. Once the accumulated signature weight (`total_signature_weight`) reaches `min_weight` (≥70%), `store_and_process_block_signature` calls `block_info.mark_locally_accepted(true)`.
4. Because `block_info.state == GloballyRejected`, `move_to(BlockState::LocallyAccepted)` returns `Err(...)`; the warning is suppressed because `has_reached_consensus()` is `true`.
5. Execution falls through: `self.signer_db.insert_block(block_info)` persists a `BlockInfo` with `state = GloballyRejected` but `signed_group = Some(now)`, and `self.broadcast_signed_block(...)` unconditionally runs, pushing the collected-signature block to S's stacks-node via `handle_post_block` → `stacks_client.post_block(block)`.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L2296-2341)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L2540-2583)
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
