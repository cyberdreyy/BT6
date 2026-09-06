## Title
Signer broadcasts a block it has locally rejected once external gossip reaches signature threshold, because `store_and_process_block_signature` never re-checks `BlockInfo::valid` before calling `mark_locally_accepted` — ([File: stacks-signer/src/v0/signer.rs])

### Summary
When a signer receives another peer's `BlockAccepted` signature (`handle_block_signature` → `store_and_process_block_signature`), it tallies signature weight and, once the ≥70% threshold is crossed, calls `block_info.mark_locally_accepted(true)` and unconditionally broadcasts the fully-signed block to the node via `broadcast_signed_block`/`handle_post_block`. Unlike the sibling `handle_block_pre_commit` path, which explicitly guards with `if !block_info.valid.unwrap_or(false) { return; }` before acting on accumulated weight, `store_and_process_block_signature` performs no such check. `mark_locally_accepted(true)` (the `group_signed=true` branch) also does **not** set `self.valid = Some(true)` — it only stamps `signed_group`. So a block this signer had already marked `valid = Some(false)` / `state = LocallyRejected` can still be pushed to `state = LocallyAccepted` and broadcast, purely because enough *other* signers signed it, without this signer ever re-validating or re-running `check_block_against_signer_db_state`.

### Finding Description [1](#0-0) 
`store_and_process_block_signature` records an incoming signature, and — if the address is not our own and we haven't recorded a pre-commit from it — treats it as an implicit pre-commit and returns early. Once that gate is passed on a later call, it proceeds directly to weight computation. [2](#0-1) 
Here the function computes `total_signature_weight` from all stored signatures and, if it meets `min_weight`, calls `block_info.mark_locally_accepted(true)` and then `broadcast_signed_block`. At no point does it consult `block_info.valid` or re-run `check_block_against_signer_db_state`, in contrast to `handle_block_pre_commit`: [3](#0-2) 
which explicitly refuses to act (`if !block_info.valid.unwrap_or(false) { ... return; }`) when the locally-stored validity flag is not `Some(true)`, and even re-runs `check_block_against_signer_db_state` before signing. [4](#0-3) 
`mark_locally_accepted`'s `group_signed=true` branch only sets `signed_group`; it deliberately skips `self.valid = Some(true)` (that assignment only happens in the `else` branch, i.e., when we ourselves signed). This means a block whose `valid` field is `Some(false)` from an earlier `mark_locally_rejected` call: [5](#0-4) 

can transition straight to `LocallyAccepted` while `valid` remains `false` — a state/validity inconsistency the state-machine's `check_state` permits: [6](#0-5) 
`LocallyRejected -> LocallyAccepted` is allowed as long as neither `GloballyRejected` nor `GloballyAccepted` has been reached yet.

Once `mark_locally_accepted` succeeds, the block is unconditionally handed to `broadcast_signed_block` → `handle_post_block` → `stacks_client.post_block(block)`: [7](#0-6) 
There is no re-validation gate here either; the node-side `/v3/block_proposal` re-check happens only for the original *proposal*, not for this broadcast path, so a signer that already rejected the block locally still relays a fully-signed copy to the network on nothing but observed gossip weight.

This breaks the "signed vs validated" equality the scan targets: this signer's own validity determination (`valid = false`) is silently overridden by third-party signature counts, and the signer itself becomes complicit in broadcasting/aggregating signatures for a block it separately determined to be invalid/non-canonical — exactly the "rejection recounted as an accept" class of bug analogous to Wasmtime trusting stale/incorrect metadata (missing GC roots) instead of re-deriving ground truth at the point of use.

### Impact Explanation
If a single (or a few, non-majority) miner/gossip actor can get ≥70% of signature *weight* gossiped for a block that this particular signer has locally rejected (e.g., because it violates `check_block_against_signer_db_state`, is a duplicate/sibling, or fails a chainstate re-check that ran *after* the original proposal validation), this signer will still flip to `LocallyAccepted` and re-broadcast the signed block to the stacks-node. This matches the "Critical" impact bucket: a signer contributing to consensus on / relaying an invalid, non-canonical, or conflicting block, and a rejection effectively being recounted as an acceptance in this signer's own bookkeeping and broadcast behavior.

### Likelihood Explanation
This does not require a majority of signers to be malicious or colluding — it only requires that a threshold-weight majority of *other* signers sign a block that this particular signer separately marked invalid (e.g., due to a chainstate divergence, timing race, or reorg-adjacent condition that this signer alone observed, per the sibling-conflict logic described throughout `signer.rs`/`signerdb.rs`). Because `store_and_process_block_signature` is on the hot path for every observed `BlockAccepted` gossip message and has no revalidation gate (unlike the analogous pre-commit path), the condition is reachable purely through normal signer gossip plus a legitimate local rejection outcome — no compromised keys or auth are needed.

### Recommendation
In `store_and_process_block_signature`, before calling `mark_locally_accepted(true)` on threshold, re-check `block_info.valid == Some(true)` (or re-run `check_block_against_signer_db_state`, as `handle_block_pre_commit` does) and bail out / emit a rejection if the block is not currently valid according to this signer's own state. Additionally, `mark_locally_accepted`'s `group_signed=true` branch should not be allowed to transition `state` away from `LocallyRejected` without first restoring `valid = Some(true)` through an explicit re-validation, to avoid the `state`/`valid` field desynchronization.

### Proof of Concept
1. Signer S receives and validates a block proposal B, then later determines (via `check_block_against_signer_db_state`, called from `handle_block_validate_ok`/`handle_block_pre_commit`) that B conflicts with signer-db state, calling `mark_locally_rejected()` → `B.valid = Some(false)`, `state = LocallyRejected`.
2. Enough *other* signers (reachable without S being a majority — S's own opinion doesn't count toward the weight computed in `store_and_process_block_signature`) broadcast `BlockAccepted` signatures over B via StackerDB gossip.
3. S processes each incoming signature through `handle_block_signature` → `store_and_process_block_signature`; once `total_signature_weight >= min_weight`, S calls `block_info.mark_locally_accepted(true)` (no check on `block_info.valid`), transitioning `LocallyRejected -> LocallyAccepted` (permitted by `check_state`), and then unconditionally calls `broadcast_signed_block` → `handle_post_block` → `stacks_client.post_block(block)`.
4. S has now relayed a fully-signed copy of a block it itself determined was invalid, to the stacks-node, without ever re-validating it.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1316-1345)
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

        // The chain and signer db state may have changed materially since this block passed the
        // proposal-time checks (e.g. between validation and reaching the pre-commit threshold we
        // may have signed a block that this one would reorg). Re-run the chainstate checks
        // before putting a signature over the block, and respond with a rejection if they no
        // longer pass, just as the block validation response handler does.
        if let Some(block_rejection) =
```

**File:** stacks-signer/src/v0/signer.rs (L2442-2471)
```rust
    /// Store the block acceptance signature and check if we have reached a consensus decision on the block because of it. If we have, update the block state accordingly and broadcast the block if accepted.
    fn store_and_process_block_signature(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        block_info: &mut BlockInfo,
        signer_address: &StacksAddress,
        signature: &MessageSignature,
    ) {
        let block_hash = &block_info.signer_signature_hash();
        // signature is valid! store it.
        // if this returns false, it means the signature already exists in the DB, so just return.
        if !self
            .signer_db
            .add_block_signature(block_hash, signer_address, signature)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block signature"))
        {
            return;
        }

        // If this isn't our own signature and we haven't seen a pre-commit from this signer yet, try treating it as a pre-commit in case the caller is running an outdated version
        if signer_address != &self.stacks_address && !self.signer_db.has_committed(block_hash, signer_address).inspect_err(|e| warn!("Failed to check if pre-commit message already considered for {signer_address:?} for {block_hash}: {e}")).unwrap_or(false) {
            self.handle_block_pre_commit(stacks_client, sortition_state, signer_address, block_hash);
            return;
        }

        if block_info.signed_group.is_some() {
            // We have already processed this block to the accepted state. Adding more signatures will not change anything so nothing to check.
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2472-2538)
```rust
        // do we have enough signatures to broadcast?
        // i.e. is the threshold reached?
        let signatures = self
            .signer_db
            .get_block_signatures(block_hash)
            .unwrap_or_else(|_| panic!("{self}: Failed to load block signatures"));

        // put signatures in order by signer address (i.e. reward cycle order)
        let addrs_to_sigs: HashMap<_, _> = signatures
            .into_iter()
            .filter_map(|sig| {
                let Ok(public_key) = Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(
                    block_hash.bits(),
                    &sig,
                ) else {
                    return None;
                };
                let addr = StacksAddress::p2pkh(self.mainnet, &public_key);
                Some((addr, sig))
            })
            .collect();

        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
        let total_signature_weight = self.compute_signature_signing_weight(addrs_to_sigs.keys());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });

        if min_weight > total_signature_weight {
            info!("{self}: Received block acceptance, but have not yet reached the acceptance threshold.";
                "signer_signature_hash" => %block_hash,
                "signature_weight" => signature_weight,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "block_height" => block_info.block.header.chain_length,
                "total_weight_approved" => total_signature_weight,
                "total_weight" => total_weight,
                "percent_approved" => (total_signature_weight as f64 / total_weight as f64 * 100.0),
            );
            return;
        }
        info!("{self}: have reached the block acceptance threshold";
            "signer_signature_hash" => %block_hash,
            "signature_weight" => signature_weight,
            "consensus_hash" => %block_info.block.header.consensus_hash,
            "block_height" => block_info.block.header.chain_length,
            "total_weight_approved" => total_signature_weight,
            "total_weight" => total_weight,
            "percent_approved" => (total_signature_weight as f64 / total_weight as f64 * 100.0),
        );

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
