### Title
Signer accepts and counts peer `BlockAccepted` signatures toward the group threshold without checking its own `block_info.valid` verdict, allowing a signer to broadcast a block as signed that it has locally rejected - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`handle_block_signature` / `store_and_process_block_signature` store and tally a peer's `BlockAccepted` signature purely on the basis of "known sender + known block," never checking `block_info.valid`. This mirrors the Palmera `setRole` bug, where a state field (`_safe.lead` there, `BlockState`/`signed_group` here) is updated on the strength of one boolean-adjacent condition while ignoring the enable/validity flag that should gate the write.

### Finding Description
In the pre-commit path, the signer explicitly gates any state change on its own validation verdict: [1](#0-0) 

`handle_block_pre_commit` refuses to act — `if !block_info.valid.unwrap_or(false) { ... return; }` — when the local signer has not itself validated the block as OK.

By contrast, `handle_block_signature` (called for every observed `BlockResponse::Accepted` from any signer) only checks the sender's signature/address, never `block_info.valid`: [2](#0-1) 

It then hands off to `store_and_process_block_signature`, which stores the signature, tallies `total_signature_weight`, and once ≥70% threshold is reached calls `block_info.mark_locally_accepted(true)` and `broadcast_signed_block` — again with no check of `block_info.valid`: [3](#0-2) 

`BlockInfo::check_state` (the state-machine guard analogous to Palmera's missing `enabled` check) only enforces monotonic transitions between `BlockState` values; it does not consult the `valid` field at all: [4](#0-3) 

So a signer that locally rejected a block (`block_info.valid == Some(false)`, state `LocallyRejected`) can still be driven by peer gossip through `mark_locally_accepted(true)` the moment enough other signers' `Accepted` weight arrives, and will then call `broadcast_signed_block` with the aggregated signature set — announcing group acceptance of a block this very signer determined to be invalid.

### Impact Explanation
This breaks the "signed vs validated" equality the state machine is supposed to preserve: a signer node is made to assert (via `broadcast_signed_block`, which pushes the block onward, e.g. to the node/mempool relay) that a block reached the signing threshold, without that signer having reconciled the group's acceptance against its own `valid = false` verdict or re-running `check_block_against_signer_db_state`. In the pre-commit path this exact re-check is done deliberately before crossing the threshold (comment at signer.rs:1340-1345 notes "the chain and signer db state may have changed materially... Re-run the chainstate checks before putting a signature over the block"), but that safety net is absent on the acceptance-counting path. This is a High-severity liveness/safety class issue per the rubric: a signer can end up acting on/propagating consensus for a block it never itself judged valid, which can also wedge or misdirect the local BlockState machine (`LocallyRejected → LocallyAccepted` without re-validation).

### Likelihood Explanation
Triggerable with only routine gossip: a single miner proposing (and the majority of the signer set — not the target signer — accepting) a block that the target signer's own validation rejected (e.g. due to a stale or diverging local sortition/chainstate view) is enough; no majority compromise or key access by the attacker/miner is required, since the "cause" is ordinary asynchronous group traffic exploiting a missing local check, not signer collusion.

### Recommendation
Add the same guard used in `handle_block_pre_commit` to `store_and_process_block_signature` (and/or `handle_block_signature`): before tallying an accepted signature toward the threshold and before calling `mark_locally_accepted`, verify `block_info.valid == Some(true)` (or re-run `check_block_against_signer_db_state` as pre-commit does) and reject/park the observation otherwise, so a signer's own negative validation verdict cannot be silently overridden by peer signature-counting.

### Proof of Concept
1. Signer S validates block B and sets `block_info.valid = Some(false)` (locally rejected) via `handle_block_validate_reject` / `check_block_against_signer_db_state`.
2. Peers P1..Pn, whose local views differ (e.g., a stale sortition view or race condition), each broadcast `BlockResponse::Accepted(B)`.
3. S's `handle_block_signature` processes each, storing signatures and calling `store_and_process_block_signature` — no code path there consults `block_info.valid`.
4. Once weight ≥ `min_weight` is reached, S executes `block_info.mark_locally_accepted(true)` (state transition allowed per `check_state`, since `LocallyRejected -> LocallyAccepted` is permitted) and calls `broadcast_signed_block`, announcing/propagating the block as signed despite S's own `valid = false` determination. [5](#0-4)

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1323-1331)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L2371-2440)
```rust
    /// Handle an observed signature from another signer
    fn handle_block_signature(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        accepted: &BlockAccepted,
    ) {
        let BlockAccepted {
            signer_signature_hash: block_hash,
            signature,
            metadata,
            ..
        } = accepted;
        debug!(
            "{self}: Received a block-accept signature: ({block_hash}, {signature}, {})",
            metadata.server_version
        );

        // recover public key
        let Ok(public_key) = Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(
            block_hash.bits(),
            signature,
        ) else {
            debug!("{self}: Received unrecovarable signature. Will not store.";
                   "signature" => %signature,
                   "signer_signature_hash" => %block_hash);

            return;
        };

        // authenticate the signature -- it must be signed by one of the stacking set
        let signer_address = StacksAddress::p2pkh(self.mainnet, &public_key);
        if !self.is_valid_signer(&signer_address) {
            debug!("{self}: Received block acceptance with an invalid signature. Will not store.";
                "signer_public_key" => ?public_key,
                "signer_address" => %signer_address,
                "signer_signature_hash" => %block_hash,
                "signature" => %signature
            );
            return;
        }
        let Some(mut block_info) = self.block_lookup_by_reward_cycle(block_hash) else {
            if let Err(e) = self.signer_db.add_pending_block_signature_response(
                block_hash,
                &signer_address,
                signature,
            ) {
                warn!("{self}: Failed to add pending block signature response: {e:?}");
            }
            return;
        };

        info!("{self}: Received block acceptance";
            "signer_pubkey" => public_key.to_hex(),
            "signer_address" => %signer_address,
            "signer_signature_hash" => %block_hash,
            "consensus_hash" => %block_info.block.header.consensus_hash,
            "block_height" => block_info.block.header.chain_length,
            "signer_weight" => self.signer_weights.get(&signer_address).copied().unwrap_or(0),
            "tenure_extend_timestamp" => accepted.response_data.tenure_extend_timestamp,
            "tenure_extend_read_count_timestamp" => accepted.response_data.tenure_extend_read_count_timestamp
        );
        self.store_and_process_block_signature(
            stacks_client,
            sortition_state,
            &mut block_info,
            &signer_address,
            signature,
        );
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2454-2537)
```rust
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
