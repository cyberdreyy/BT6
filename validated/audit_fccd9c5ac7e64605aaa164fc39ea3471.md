### Title
Stale in-memory `BlockInfo` snapshots clobber DB state advanced by nested handler calls, undoing signature/rejection tallies and the same-height conflict guard - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`BlockInfo` is not held as a single shared, referenced record — every handler independently fetches its own snapshot via `block_lookup_by_reward_cycle`, mutates it in memory, and persists the whole snapshot back with `self.signer_db.insert_block(&block_info)`, which serializes the entire struct into the `blocks` table (`block_info TEXT` column, see the migration schema at `stacks-signer/src/signerdb.rs:527-548`) with no optimistic-concurrency or monotonicity check at the persistence layer (only `BlockInfo::check_state`/`move_to`, an in-memory guard, exists — `stacks-signer/src/signerdb.rs:313-341`).

`process_pending_responses_for_block` (`stacks-signer/src/v0/signer.rs:1729-1780`) holds one such stale outer snapshot (`block_info: &mut BlockInfo`) for the whole function. While iterating `pending_responses.pre_commits`, it calls the full nested handler `handle_block_pre_commit` (`stacks-signer/src/v0/signer.rs:1250-1466`), which does its *own* independent `block_lookup_by_reward_cycle`, can advance the block all the way to `mark_locally_accepted` (SIGN) and `insert_block` its own updated copy — a self-reentrant call into the same handler pipeline before the outer function has finished. The outer loop then continues with `store_and_process_block_rejection` (`stacks-signer/src/v0/signer.rs:2268-2369`, final write at line 2338) and `store_and_process_block_signature` (`stacks-signer/src/v0/signer.rs:2442-2538`, final write at line 2533), both of which unconditionally call `self.signer_db.insert_block(block_info)` on the *outer, now-stale* snapshot — blindly overwriting whatever more-advanced `BlockState`/`signed_self`/`signed_group`/pre-commit bookkeeping the nested call just persisted.

### Finding Description
The pattern is a same-thread reentrancy analog of the audited Solidity issue: handler A (`process_pending_responses_for_block`) reads state, then — before writing its own update — invokes handler B (`handle_block_pre_commit`) which reads, mutates, and commits its own copy of the *same* record; A then resumes and commits its stale copy, clobbering B's write. Concretely:

1. `handle_block_proposal` inserts a fresh `Unprocessed` block and calls `process_pending_responses_for_block(&mut block_info, pending_responses)` (`stacks-signer/src/v0/signer.rs:1716-1725`).
2. Inside, the pre-commit loop calls `self.handle_block_pre_commit(...)` for each pending committer (`stacks-signer/src/v0/signer.rs:1738-1750`). That call re-fetches its own `BlockInfo` from the DB, can reach the ≥70% weight threshold, run the conflict/chainstate re-checks, call `mark_locally_accepted` (setting `signed_self`, `state = LocallyAccepted`), and persist it via its own `insert_block`.
3. Control returns to `process_pending_responses_for_block`, which still holds the *original* `Unprocessed` snapshot taken in step 1. The subsequent rejection/signature loops (lines 1751-1779) operate on this stale copy and, at the end of `store_and_process_block_rejection`/`store_and_process_block_signature`, write it back with `insert_block`, overwriting the DB row and erasing the `LocallyAccepted`/`signed_self`/`signed_group` state that step 2 just committed.

Because `signed_self`/`signed_group`/`state` are the exact fields that (a) guard against double-signing (`if block_info.signed_self.is_some() { … return; }` at `stacks-signer/src/v0/signer.rs:1316-1321`) and (b) feed `get_signed_conflicts` used by the same-height conflict guard in `handle_block_pre_commit` (`stacks-signer/src/v0/signer.rs:1368-1466`), silently reverting them to `Unprocessed`/`None` removes the record that this signer already signed this block. A subsequent pre-commit/proposal replay for a *different, conflicting* block at the same height (or a re-proposal of the same block) will then find no recorded conflict/no `signed_self`, and can proceed to sign again — breaking the "one signature per height" invariant that `get_signed_conflicts`/`conflict_still_blocks` is designed to enforce.

### Impact Explanation
This breaks the safety invariant that a signer never signs two conflicting blocks at the same height. If the reverted write erases `signed_self`/`signed_group`/`state=LocallyAccepted` for a block the signer already signed, the DB no longer reflects that fact; a later pre-commit or proposal replay for a sibling block at the same height (or a rival tenure) will not be seen as a signed conflict by `get_signed_conflicts` (`stacks-signer/src/v0/signer.rs:1368` region, feeding into the checks described in `docs/signer-flows.md:229-272`), and the signer can be induced to sign it too. This is a concrete instance of "a signer signing a conflicting block" (Critical bucket) reachable purely through message ordering that a single miner (proposing/re-proposing) plus normal gossip replay of pending pre-commits/rejections/signatures can trigger — no majority of signers or key compromise required.

### Likelihood Explanation
This requires only the ordinary flow already exercised by the codebase's own tests: pending pre-commits/rejections/signatures parked before a proposal arrives (`drain_pending_block_responses`) and then replayed together via `process_pending_responses_for_block` when the proposal is finally received (`stacks-signer/src/v0/signer.rs:1720-1725`, `docs/signer-flows.md:196-198`). Any timing where a pre-commit response for the same block arrives/replays before a pending rejection or signature entry for it in the same batch reproduces the clobber, which is a common ordering for a slow/rebroadcasting one-slot miner plus normal StackerDB gossip.

### Recommendation
Do not thread a long-lived, separately-mutated `BlockInfo` snapshot across a call that itself re-fetches and persists the same row. Either:
- have `handle_block_pre_commit` (and other nested handlers reachable from `process_pending_responses_for_block`) accept and mutate the *same* `&mut BlockInfo` reference instead of doing their own `block_lookup_by_reward_cycle`, or
- after any nested handler call that may have mutated the record, re-fetch the latest `BlockInfo` from `signer_db` before continuing the loop and before any subsequent `insert_block` call, or
- make `insert_block` perform a compare-and-swap / merge against the currently stored `state`, `signed_self`, and `signed_group` fields rather than an unconditional overwrite, rejecting writes that would move backward per `BlockInfo::check_state`.

### Proof of Concept
Not executed against a live signer; derived from static analysis of the call graph:
1. Two pending pre-commit entries and one pending rejection entry are recorded for block `B` before its proposal arrives (`add_pending_block_pre_commit_response` / rejection equivalents).
2. Proposal for `B` arrives; `handle_block_proposal` inserts `Unprocessed` `block_info` and calls `process_pending_responses_for_block`.
3. The pre-commit loop's call to `handle_block_pre_commit` pushes the aggregated pre-commit weight over threshold, re-checks pass, and it signs: `mark_locally_accepted` + `insert_block` (DB now has `state=LocallyAccepted`, `signed_self=Some(t)`).
4. The loop moves to the (now stale, still `Unprocessed`) pending rejection entry and calls `store_and_process_block_rejection(block_info, …)`, which — regardless of outcome — is followed later by `store_and_process_block_signature` for any pending signature entries, ending in `self.signer_db.insert_block(block_info)` with the outer stale copy, reverting the DB row's `state`/`signed_self` back to pre-signing values.
5. A later replayed/re-proposed conflicting block at the same height calls into `handle_block_pre_commit` for the new block; `get_signed_conflicts`/freshness checks read the DB and no longer see the erased `signed_self`/`state=LocallyAccepted` record for `B`, so the conflict guard does not block signing the new, conflicting block. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1250-1321)
```rust
    /// Handle pre-commit message from another signer
    fn handle_block_pre_commit(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        stacker_address: &StacksAddress,
        block_hash: &Sha512Trunc256Sum,
    ) {
        let Some(mut block_info) = self.block_lookup_by_reward_cycle(block_hash) else {
            // A pre-commit for a block we have not seen proposed yet means the proposal
            // has not reached us. Log it at INFO: it is a direct signal that our view of
            // the proposal stream is behind the rest of the signer set.
            info!("{self}: Received block pre-commit for an unknown block, storing as pending";
                "signer_address" => %stacker_address,
                "signer_signature_hash" => %block_hash,
                "signer_weight" => self.signer_weights.get(stacker_address).copied().unwrap_or(0),
            );
            if let Err(e) = self
                .signer_db
                .add_pending_block_pre_commit_response(block_hash, stacker_address)
            {
                warn!("{self}: Failed to save pending block pre-commit response: {e:?}");
            }
            return;
        };
        // Always save the pre-commit - we will need to store signer responses for determining which
        // are misbehaving, offline, etc.
        // commit message is from a valid sender! store it
        self.signer_db
            .add_block_pre_commit(block_hash, stacker_address)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block pre-commit"));

        let block_hash = block_info.block.header.signer_signature_hash();
        // do we have enough pre-commits to reach consensus?
        // i.e. is the threshold reached?
        //
        // Tally this up front, before the early returns below, so that every pre-commit we
        // receive can be logged with the running weight. Crossing this threshold is what
        // triggers our block response, so without it the wait for the threshold, which can
        // be minutes and is the bulk of a stalled block's latency, leaves no trace at all.
        let committers = self
            .signer_db
            .get_block_pre_committers(&block_hash)
            .unwrap_or_else(|_| panic!("{self}: Failed to load block commits"));

        let commit_weight = self.compute_signature_signing_weight(committers.iter());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });

        info!("{self}: Received block pre-commit";
            "signer_address" => %stacker_address,
            "signer_signature_hash" => %block_hash,
            "consensus_hash" => %block_info.block.header.consensus_hash,
            "block_height" => block_info.block.header.chain_length,
            "signer_weight" => self.signer_weights.get(stacker_address).copied().unwrap_or(0),
            "pre_commit_weight" => commit_weight,
            "pre_commit_weight_required" => min_weight,
            "total_weight" => total_weight,
            "pre_commit_threshold_reached" => commit_weight >= min_weight,
            "already_signed" => block_info.signed_self.is_some(),
        );

        if block_info.signed_self.is_some() {
            debug!(
                "{self}: Received pre-commit for a block that we have already signed. Doing nothing...",
            );
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1729-1780)
```rust
    /// Process pending responses for a block proposal that we may have received late.
    fn process_pending_responses_for_block(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        block_info: &mut BlockInfo,
        pending_responses: PendingBlockResponses,
    ) {
        let signer_signature_hash = block_info.block.header.signer_signature_hash();
        for stacker_address in pending_responses.pre_commits {
            debug!("{self}: Processing pending pre-commit.";
                "stacker_address" => %stacker_address,
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_info.block.block_id(),
            );
            self.handle_block_pre_commit(
                stacks_client,
                sortition_state,
                &stacker_address,
                &signer_signature_hash,
            );
        }
        for (stacker_address, reject_reason) in pending_responses.rejections {
            debug!("{self}: Processing pending rejection.";
                "stacker_address" => %stacker_address,
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_info.block.block_id(),
                "reject_reason" => ?reject_reason,
            );
            self.store_and_process_block_rejection(
                sortition_state,
                block_info,
                &stacker_address,
                reject_reason,
            );
        }
        let block_id = block_info.block.block_id();
        for (stackers_address, signature) in pending_responses.signatures {
            debug!("{self}: Processing pending signature.";
                "stacker_address" => %stackers_address,
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_id,
            );
            self.store_and_process_block_signature(
                stacks_client,
                sortition_state,
                block_info,
                &stackers_address,
                &signature,
            );
        }
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1913-1919)
```rust
        }
        // For mutability reasons, we need to take the block_info out of the map and add it back after processing
        let Some(mut block_info) = self.block_lookup_by_reward_cycle(signer_signature_hash) else {
            // We have not seen this block before. Why are we getting a response for it?
            debug!("{self}: Received a block validate response for a block we have are not tracking. Ignoring...");
            return;
        };
```

**File:** stacks-signer/src/v0/signer.rs (L2268-2341)
```rust
    fn store_and_process_block_rejection(
        &mut self,
        sortition_state: &mut Option<SortitionsView>,
        block_info: &mut BlockInfo,
        signer_address: &StacksAddress,
        reject_reason: RejectReasonPrefix,
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

**File:** stacks-signer/src/signerdb.rs (L527-548)
```rust
static MIGRATE_BLOCKS_TABLE_2_BLOCKS_TABLE_3: &str = r#"
CREATE TABLE IF NOT EXISTS temp_blocks (
    -- The block sighash commits to all of the stacks and burnchain state as of its parent,
    -- as well as the tenure itself so there's no need to include the reward cycle.  Just
    -- the sighash is sufficient to uniquely identify the block across all burnchain, PoX,
    -- and stacks forks.
    signer_signature_hash TEXT NOT NULL PRIMARY KEY,
    reward_cycle INTEGER NOT NULL,
    block_info TEXT NOT NULL,
    consensus_hash TEXT NOT NULL,
    signed_over INTEGER NOT NULL,
    broadcasted INTEGER,
    stacks_height INTEGER NOT NULL,
    burn_block_height INTEGER NOT NULL,
    valid INTEGER,
    state TEXT NOT NULL,
    signed_group INTEGER,
    signed_self INTEGER,
    proposed_time INTEGER NOT NULL,
    validation_time_ms INTEGER,
    tenure_change INTEGER NOT NULL
) STRICT;
```
