### Title
Group-signature tally path bypasses the mandatory chainstate/conflict recheck, letting a signer push a block it locally rejected - ([File: stacks-signer/src/v0/signer.rs])

### Summary
When a signer decides to sign a block itself (via `handle_block_pre_commit` crossing the pre-commit threshold), it re-runs `check_block_against_signer_db_state` and the `get_signed_conflicts`/`conflict_still_blocks`/`reorg_permit_stands` checks immediately before producing a signature. But when a signer merely tallies *other* signers' `BlockAccepted` signatures and the acceptance-weight threshold is crossed in `store_and_process_block_signature`, none of those rechecks run before the block is marked accepted and pushed to the node. This is the same class of bug as the flash-loan report: a state-consistency step (`accrue_interest` there, the chainstate/conflict recheck here) is invoked on one path but skipped on an equivalent alternate path that reaches the same terminal action.

### Finding Description
The pre-commit → signature path is documented and coded to always re-validate before signing: [1](#0-0) [2](#0-1) 

This is explicitly called out in the design docs as mandatory: "Between validation and threshold, we may have signed a *different* block at the same height... the world must be re-checked before the signature leaves the box." [3](#0-2) 

However, the alternate path that reaches the *same* terminal effect — marking a block accepted and broadcasting it to the node — is `handle_block_signature` → `store_and_process_block_signature`, triggered purely by receiving enough `BlockAccepted` messages from other signers: [4](#0-3) [5](#0-4) 

`store_and_process_block_signature` only checks whether the signature was already stored and whether `signed_group` is already set; it never calls `check_block_against_signer_db_state`, `get_signed_conflicts`, `conflict_still_blocks`, or `reorg_permit_stands` before calling `mark_locally_accepted(true)` and `broadcast_signed_block` → `handle_post_block` (which POSTs the fully-signed block to this signer's own node).

Critically, `BlockInfo::check_state` permits the transition `LocallyRejected -> LocallyAccepted`: [6](#0-5) 

So a signer that has already locally rejected a block (e.g. because `check_block_against_signer_db_state` found it now conflicts with something this signer has already signed, or an equivocation guard fired) can still have that verdict silently overridden the moment enough *other* signers' acceptance signatures arrive, with no re-verification of its own view of the chain. The block is then submitted to this signer's node via `handle_post_block`.

### Impact Explanation
This breaks the "signed vs validated" equality the pre-commit recheck exists to protect: a block the signer's own logic determined to be invalid, stale, or conflicting with a fresh signature it already produced can nonetheless be marked accepted and pushed to the node by that same signer, purely as a side effect of counting others' signatures. This matches the Critical bar ("a signer signing/pushing an invalid, non-canonical, or conflicting block") because the equivocation/conflict guard that is enforced on the self-signing path is not enforced on the signature-tally path, even though both paths converge on broadcasting a fully-signed block to the node.

### Likelihood Explanation
This requires no majority collusion and no key compromise — it is triggered by ordinary network timing: any time a signer's own chainstate view diverges from a threshold of already-collected `BlockAccepted` signatures (e.g. it processed a conflicting/rejecting event slightly later than others, or the conflict/reorg-permit state changed between the signatures being sent and being tallied by this node), the gap in `store_and_process_block_signature` is hit. Given the codebase explicitly built (and tested — `signer_refuses_to_sign_second_sibling_tenure_start`, `stale_sibling_still_refused_when_canonical_tip_at_height`, etc.) a rich state machine of rechecks specifically for the moment a signature is about to be produced/pushed, and that logic is proven absent from this second path, likelihood is non-trivial in any real network with propagation delay or forks.

### Recommendation
Before `store_and_process_block_signature` calls `mark_locally_accepted`/`broadcast_signed_block`, re-run the same recheck sequence used in `handle_block_pre_commit`: `check_block_against_signer_db_state`, and the `get_signed_conflicts` / `reorg_permit_stands` / `conflict_still_blocks` freshness-and-liveness checks. If the block no longer passes, the accumulated signatures should not be used to push the block, and the signer should fall back to (or remain in) its locally-rejected/pending state instead of overriding it.

### Proof of Concept
1. Signer S receives a block proposal B and validates it OK, then observes a conflict (e.g. it has already signed a fresh sibling block A at the same height) so `check_block_against_signer_db_state` in `handle_block_pre_commit` marks B `LocallyRejected` per [1](#0-0) .
2. Other signers who have not yet learned of the conflict with A finish signing B and broadcast `BlockAccepted` for B.
3. S receives these `BlockAccepted` messages via `handle_block_signature` → `store_and_process_block_signature` [5](#0-4) . No recheck of the conflict/chainstate is performed; once the accumulated acceptance weight crosses `min_weight`, `mark_locally_accepted(true)` succeeds (allowed transition from `LocallyRejected`, per `check_state` [6](#0-5) ), and `broadcast_signed_block`/`handle_post_block` submits the fully-signed B to S's own node — even though S's own state machine had just rejected B as conflicting.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1345-1366)
```rust
        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
        {
            warn!(
                "{self}: Reached the pre-commit threshold for a block, but it no longer passes the chainstate checks. Rejecting.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "reject_code" => %block_rejection.reason_code,
                "reject_reason" => &block_rejection.reason,
            );
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
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1383-1421)
```rust
        let conflicts = match self
            .signer_db
            .get_signed_conflicts(block_info.block.header.chain_length, &block_hash)
        {
            Ok(conflicts) => conflicts,
            Err(e) => {
                warn!("{self}: Failed to query the signed blocks. Refusing to sign block {block_hash}: {e:?}");
                return;
            }
        };
        let freshness_cutoff = get_epoch_time_secs().saturating_sub(
            self.proposal_config
                .tenure_last_block_proposal_timeout
                .as_secs(),
        );
        // A fresh signature only blocks while the block it covers could still be part of the
        // chain: see `conflict_still_blocks`, which asks the node whether it is. Check
        // freshness first: it is a local timestamp comparison, while `reorg_permit_stands`
        // and `conflict_still_blocks` each query the node, so stale conflicts cost no
        // round-trips.
        if let Some(conflict) = conflicts.iter().find(|conflict| {
            conflict.last_endorsed > freshness_cutoff
                && !self.reorg_permit_stands(stacks_client, conflict)
                && self.conflict_still_blocks(
                    stacks_client,
                    conflict,
                    block_info.block.header.chain_length,
                )
        }) {
            warn!(
                "{self}: Reached the pre-commit threshold for a block, but we have recently signed or accepted a different block at the same or higher height. Refusing to sign.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "conflicting_signer_signature_hash" => %conflict.signer_signature_hash,
                "conflicting_block_height" => conflict.stacks_height,
                "conflicting_consensus_hash" => %conflict.consensus_hash,
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

**File:** docs/signer-flows.md (L229-235)
```markdown
## 5. Pre-commit threshold → signature

The only place the signer produces a block signature by counting votes.
Pre-commits from peers (and our own) accumulate; at ≥70% weight the signer
decides whether to follow through. Between validation and threshold, we may have
signed a _different_ block at the same height, possibly in another tenure, so
the world must be re-checked before the signature leaves the box.
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
