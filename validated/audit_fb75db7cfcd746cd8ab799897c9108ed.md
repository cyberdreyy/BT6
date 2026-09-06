### Title
`store_and_process_block_signature` recounts peer signatures into local acceptance without re-running the conflict/chainstate guard that `handle_block_pre_commit` enforces before signing - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`handle_block_pre_commit` (the only place *this* signer produces its own signature) re-checks chainstate consistency (`check_block_against_signer_db_state`) and same/cross-tenure signed conflicts (`get_signed_conflicts` + `conflict_still_blocks` + `reorg_permit_stands`) immediately before calling `mark_locally_accepted(false)`. The parallel path that processes *peers'* `BlockAccepted` messages, `store_and_process_block_signature`, tallies weight and — once ≥70% signature weight is reached — calls `mark_locally_accepted(true)` and `broadcast_signed_block` with **none** of those checks. A signer can therefore be driven into `LocallyAccepted` (and push a block to its own node) for a block that conflicts with one it has already signed, exactly the double-sign scenario the pre-commit guard exists to prevent.

### Finding Description
`BlockInfo::mark_locally_accepted` treats `group_signed=true` (peer-driven) and `group_signed=false` (self-signed) as equivalent transitions into `BlockState::LocallyAccepted`, differing only in which timestamp field is stamped: [1](#0-0) 

The self-signing path (`handle_block_pre_commit`) only reaches this call after: (1) `check_block_against_signer_db_state` passes, and (2) no fresh, still-live conflict from `get_signed_conflicts` blocks it (freshness + `conflict_still_blocks` + `reorg_permit_stands` + own-tenure tip check): [2](#0-1) [3](#0-2) 

The peer-signature path, `store_and_process_block_signature`, stores the incoming signature, tallies total signing weight against the 70% threshold, and — if reached — calls `mark_locally_accepted(true)` and immediately `broadcast_signed_block`, without ever calling `check_block_against_signer_db_state` or `get_signed_conflicts`: [4](#0-3) 

The only conditional gate on this path is whether the sender previously sent a pre-commit (`has_committed`); if so, the code skips straight to tallying rather than re-routing through the pre-commit's conflict check: [5](#0-4) 

Per the state-machine documentation, `LocallyRejected -> LocallyAccepted` is an allowed "re-evaluated" transition, and `check_state` does not forbid it: [6](#0-5) 

Consequently, a signer that has already signed block A at height *h* (via `handle_block_pre_commit`'s full-guard path), and then locally rejected a conflicting sibling block B at the same height *h* through the exact same guard (`check_block_against_signer_db_state` / conflict check failing), can still be forced to accept and broadcast B purely because other signers' `BlockAccepted` messages for B arrive over gossip and cross the 70% weight threshold in `store_and_process_block_signature`. Nothing in that path re-derives whether B still conflicts with this signer's own signed block A.

This is a single-miner-plus-gossip-reachable scenario: a one-slot miner proposes two sibling blocks at the same height (own tenure re-proposal, or a competing tenure-start block, both explicitly discussed in section 5 of `docs/signer-flows.md` as the intended targets of the conflict guard). Natural timing differences mean different signers sign different siblings; whichever sibling's signatures happen to reach this signer via gossip first and cross 70% total weight will be recounted into this signer's own acceptance and pushed to its node, regardless of the fact that this very signer's own guard (`handle_block_pre_commit`) had refused, or would refuse, to sign that sibling itself.

### Impact Explanation
This breaks the "rejection re-counted as an accept" / equivocation-guard invariant: a signer that locally rejected (or would refuse to sign) a conflicting block due to the double-sign/equivocation guard can nonetheless be driven to record `signed_group`, transition to `LocallyAccepted`, and call `broadcast_signed_block` → `handle_post_block` for that same conflicting block, purely by recounting other signers' gossip. This defeats the entire purpose of the freshness/conflict machinery documented in `docs/signer-flows.md` section 5, and risks the node being handed two conflicting blocks that the signer's own state machine determined were mutually exclusive.

### Likelihood Explanation
No compromised keys or majority collusion are required — only a normal miner re-proposal or competing tenure-start block at the same height (a routine, explicitly-anticipated race per the "Pre-commit threshold → signature" flow) combined with ordinary StackerDB gossip of `BlockAccepted` responses from other signers who happened to validate/sign the other sibling first. The asymmetry is deterministic and always reachable whenever such a sibling race occurs and 70% weight assembles on the side this signer did not (or would not) sign.

### Recommendation
Before calling `mark_locally_accepted(true)` in `store_and_process_block_signature`, re-run the same chainstate and conflict checks used in `handle_block_pre_commit` (`check_block_against_signer_db_state`, `get_signed_conflicts`/`conflict_still_blocks`/`reorg_permit_stands`) so that recounted peer signatures cannot push this signer past a conflict it would otherwise refuse to sign over.

### Proof of Concept
1. Miner proposes block A at height h in tenure T1; signer S validates and, via `handle_block_pre_commit`, signs A (`mark_locally_accepted(false)`), per `stacks-signer/src/v0/signer.rs:1466-1479`.
2. Miner (or a competing tenure) proposes conflicting block B at height h; S receives B, and when B's own pre-commit threshold is evaluated, `handle_block_pre_commit`'s conflict guard (`get_signed_conflicts` finds A fresh) causes S to reject B (`mark_locally_rejected`), per `stacks-signer/src/v0/signer.rs:1345-1421`.
3. Other signers who validated B before A became fresh in their own views sign B and gossip `BlockAccepted` for B.
4. S receives these via `handle_block_signature` → `store_and_process_block_signature` (`stacks-signer/src/v0/signer.rs:2442-2538`). Since S already sent/received pre-commits from these signers, the code skips the pre-commit re-route and simply tallies weight.
5. Once tallied weight for B reaches 70%, S calls `mark_locally_accepted(true)` on B (transition `LocallyRejected -> LocallyAccepted` allowed per `docs/signer-flows.md:142-150`) and then `broadcast_signed_block`, despite S itself having determined B conflicts with its already-signed A.

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

**File:** stacks-signer/src/v0/signer.rs (L1345-1421)
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

        // A pre-commit may be superseded by a competing proposal at the same height (e.g. a
        // re-proposed tenure-start block after the first failed to reach consensus), but a
        // signature must not be superseded while it's still "fresh". A signed block at the
        // same or higher height in ANY tenure is a conflict: two blocks at the same height are
        // siblings no matter which tenure they belong to (e.g. the next tenure's tenure-start
        // block conflicts with the current tenure's block at the same height). Blocks in
        // tenures whose reorg we sanctioned under the reorg-timing rules are excluded, but
        // only while the sortition the permit was granted to is still canonical
        // (`check_parent_tenure_choice` records the permit, `reorg_permit_stands` re-derives
        // its validity from the node); every other question about whether a conflict is
        // still live is derived from the node in `conflict_still_blocks`.
        //
        // Unlike the chainstate check above, a refusal here is "for now" rather than a
        // broadcast rejection: a later pre-commit re-evaluation may still sign the block once
        // the conflicting signature has gone stale.
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

**File:** docs/signer-flows.md (L142-150)
```markdown
    Unprocessed --> LocallyRejected : mark_locally_rejected
    PreCommitted --> LocallyRejected : mark_locally_rejected
    LocallyRejected --> LocallyAccepted : re-evaluated
    LocallyAccepted --> LocallyRejected : re-evaluated
    LocallyAccepted --> GloballyAccepted : mark_globally_accepted
    LocallyRejected --> GloballyRejected : mark_globally_rejected
    GloballyAccepted --> [*]
    GloballyRejected --> [*]
```
```
