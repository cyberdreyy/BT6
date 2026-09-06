### Title
Signer flips a locally-rejected/never-validated block to "accepted" and broadcasts it purely on peer signature weight, bypassing the pre-commit conflict re-check - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`store_and_process_block_signature`, invoked from `handle_block_signature` when a `BlockAccepted` message arrives from a peer, tallies signature weight and — once it crosses the 70% threshold — calls `block_info.mark_locally_accepted(true)` and `broadcast_signed_block` without ever checking this signer's own `block_info.valid` field or re-running the chainstate/conflict checks that gate every other path to signing.

### Finding Description
Every other path that leads to this signer producing/endorsing a signature explicitly re-verifies state immediately before doing so:
- `handle_block_validate_ok` re-checks `check_block_against_signer_db_state` before `mark_pre_committed`. [1](#0-0) 
- `handle_block_pre_commit` explicitly bails out if `!block_info.valid.unwrap_or(false)` before even counting weight, and additionally re-runs `check_block_against_signer_db_state` plus the conflict/reorg-permit logic before marking a block signed. [2](#0-1) 

However, `store_and_process_block_signature` — the function that tallies `BlockAccepted` responses from peers and decides whether *this* signer should mark the block accepted and push it to its node — has no such guard:

```
store_and_process_block_signature(...):
  add_block_signature(...)                        // just stores it
  if peer never pre-committed -> route to handle_block_pre_commit (has the guard) and return
  if block_info.signed_group.is_some() -> return   // only check present
  tally weight from stored signatures
  if weight < threshold -> return
  mark_locally_accepted(true)                      // <-- no check of block_info.valid or state
  broadcast_signed_block(...)                       // pushes block to this signer's node
``` [3](#0-2) 

`mark_locally_accepted` itself performs no validity check either — it unconditionally calls `self.move_to(BlockState::LocallyAccepted)`. [4](#0-3)  And `BlockInfo::check_state` explicitly allows the transition `LocallyRejected -> LocallyAccepted` ("local state reachable from anything not yet global"): [5](#0-4) 

So the sequence: this signer performs its own validation and detects a conflict (e.g. a sibling block it already signed in another tenure, or a `SortitionViewMismatch`), sets `valid = Some(false)` and moves to `LocallyRejected` via `mark_locally_rejected` in `handle_block_validate_ok`'s failure branch. [6](#0-5)  Meanwhile, other signers (who did not detect/care about the same conflict, or who are simply ahead in their view) pre-commit and sign the block, and their `BlockAccepted` responses arrive at this signer and are routed into `store_and_process_block_signature` (since they already have a recorded pre-commit via `has_committed`). Once their combined weight reaches 70%, this signer transitions its own already-`LocallyRejected` block straight to `LocallyAccepted` and calls `broadcast_signed_block`, which pushes the block (with the pre-commit/other signers' signatures) to its own stacks-node — with no re-verification that the conflict which caused the original rejection is now resolved.

This breaks the equality the rest of the codebase carefully maintains: "a signer's local accept/broadcast decision must reflect the outcome of its own re-checked chainstate/conflict evaluation" (see the doc's explicit design note: "the world must be re-checked before the signature leaves the box", and the extensive conflict-avoidance machinery in section 5 of `docs/signer-flows.md`, lines 229-330 — all bypassed by this path).

### Impact Explanation
This is a state-machine wedge/safety break of the class explicitly in scope: a rejection is recounted/overridden as an acceptance without the guard that exists everywhere else, and the signer will forward/broadcast a block to its own node that it had itself determined conflicted with chainstate — undermining the double-sign/reorg protections that `handle_block_pre_commit`'s `get_signed_conflicts`/`reorg_permit_stands` logic is specifically designed to enforce. Because the affected node still only forwards existing valid signer signatures (not a new bad signature of its own), the most direct consequence is that the equivocation/conflict guard is silently skipped for this code path, potentially causing the local node to accept/process a block its own signer had rejected — a liveness/safety inconsistency in the signer's local state.

### Likelihood Explanation
Reachable purely through the normal signer message flow (gossip of `BlockAccepted` messages) and does not require a majority of malicious signers or key compromise — it requires only that a legitimate signer has already locally rejected a block (a rejection reachable via a legitimate conflict) while ≥70% of other signers accept it, which is a plausible race in tenure-boundary/reorg scenarios that the codebase's own tests (e.g. `async_sibling_validation`, `signers_wait_for_validation.rs`) demonstrate are realistic operational conditions.

### Recommendation
Add the same guard used in `handle_block_pre_commit` to `store_and_process_block_signature`: verify `block_info.valid == Some(true)` (or re-run `check_block_against_signer_db_state`) before calling `mark_locally_accepted` and `broadcast_signed_block`, mirroring the check `if !block_info.valid.unwrap_or(false) { return; }` at [7](#0-6) .

### Proof of Concept
Not independently executed (index/tool budget exhausted before confirming `broadcast_signed_block`'s exact body); the control-flow trace above is derived directly from reading `store_and_process_block_signature`, `mark_locally_accepted`, and `check_state` as cited. A concrete reproduction would need a Devin session to run a local multi-signer test (similar to `stacks-node/src/tests/signer/v0/reorg.rs`'s sibling-conflict tests) where one signer's own validation is forced to reject a block (e.g. via `TEST_VALIDATE_STALL`/conflicting sibling setup) while the remaining signers reach 70% pre-commit/acceptance weight, then assert that the rejecting signer's `BlockInfo.state` flips to `LocallyAccepted` and `broadcast_signed_block`/`handle_post_block` is invoked.

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

**File:** stacks-signer/src/v0/signer.rs (L1946-1970)
```rust
        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
        {
            // The signer db state has changed. We no longer view this block as valid. Override the validation response.
            if let Err(e) = block_info.mark_locally_rejected() {
                if !block_info.has_reached_consensus() {
                    warn!("{self}: Failed to mark block as locally rejected: {e:?}");
                }
            };
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.handle_block_rejection(&block_rejection, sortition_state);
            self.send_block_response(&block_info.block, block_rejection.into());
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
```

**File:** stacks-signer/src/v0/signer.rs (L2442-2538)
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
