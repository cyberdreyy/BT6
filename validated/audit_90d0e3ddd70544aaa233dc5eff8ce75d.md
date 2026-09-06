### Title
Rejected block signature aggregation still triggers `broadcast_signed_block`, letting a rejection get recounted as an acceptance - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`store_and_process_block_signature` in `stacks-signer/src/v0/signer.rs` computes the aggregate signature weight over all rows in `block_signatures` and, once the 70% threshold is reached, unconditionally calls `broadcast_signed_block` regardless of whether the block's `BlockState` transition to `LocallyAccepted` actually succeeded. Because `BlockInfo::check_state` forbids moving out of `GloballyRejected` (a terminal state, `stacks-signer/src/signerdb.rs:314-329`), a block that has already reached `GloballyRejected` consensus can still have straggling/late signatures accumulate weight and get pushed to the node as a signed block.

### Finding Description
The rejection and acceptance paths are asymmetric in one important way:

- `store_and_process_block_rejection` explicitly bails out with `if block_info.has_reached_consensus() { return; }` before it even loads/tallies rejection weight [1](#0-0) .
- `store_and_process_block_signature` has **no equivalent guard**. It only checks `block_info.signed_group.is_some()` (i.e., whether the group threshold was already reached for *this* signature path) before tallying weight and deciding to broadcast [2](#0-1) .

`add_block_signature` deletes any rejection row from `block_rejection_signer_addrs` for that signer/block *before* inserting the signature [3](#0-2) , and `add_block_rejection_signer_addr` only blocks a *new* rejection if a signature already exists for that signer — it does nothing to prevent a signature from arriving after a rejection [4](#0-3) . This asymmetric "signature always wins" design is intentional per the design notes ("a rejection is a revocable opinion... the signature is a bearer instrument that can still be aggregated toward the 70% threshold" [5](#0-4) ) — but the intent is that this happens only *before* the local decision is finalized.

The actual code path does not respect that boundary: once `block_info.mark_globally_rejected()` fires (state → `GloballyRejected`, a decision the local signer already broadcasted/finalized), `store_and_process_block_signature` can still be entered again — via a late/gossip `BlockAccepted` message, or via `process_pending_responses_for_block` replaying a pending signature response recorded earlier in `signer_pending_signature_responses` (a message a single one-slot signer can have sent, or that a single miner/proposer-adjacent peer relayed) [6](#0-5) . Inside it:

1. `add_block_signature` succeeds and silently deletes the corresponding rejection row.
2. Weight is retallied from `get_block_signatures`; if the accumulated weight (from signers who signed before the rejection threshold was reached, plus this one) now reaches `min_weight`, the threshold branch executes.
3. `block_info.mark_locally_accepted(true)` is called; since `check_state` forbids `GloballyRejected → LocallyAccepted` [7](#0-6) , this returns `Err`, and the error is swallowed because `block_info.has_reached_consensus()` is true — so **no warning is even logged** [8](#0-7) .
4. `insert_block` persists `block_info` (state stays `GloballyRejected`, since the mutation failed).
5. `broadcast_signed_block(stacks_client, block_info.block.clone(), &addrs_to_sigs)` is called **unconditionally** — there is no check on `block_info.state` at all before this call [9](#0-8) .

So a block the local signer has already finalized as `GloballyRejected` (having broadcast its own rejection and observed/contributed to the >30% blocking rejection weight) is re-broadcast to the stacks-node as an accepted, signed block, with the local `BlockInfo.state` left inconsistent (`GloballyRejected` on disk, but a signed block object pushed to the node's `/v3/block_pushed` style path via `handle_post_block`). This breaks the "rejection vs recount" equality the scan is targeting: a decision the signer's own state machine recorded as *rejected* is recounted and delivered as *accepted*.

### Impact Explanation
This falls under the Critical impact category "a rejection recounted as an accept." The consequence is not merely a logging inconsistency: `broadcast_signed_block` is the same function used on the legitimate accept path and results in the signed block being handed to the node (`handle_post_block`). If the node accepts a block the signer's own local consensus had already finalized as globally rejected (e.g., because it lost a `DuplicateBlockFound`/conflict check, or was the losing side of a fork the signer set explicitly voted down), the signer participates in pushing state the signer protocol's own bookkeeping says should never reach the chain. At minimum it desynchronizes the signer's persisted `BlockState` from the actual network action taken (`GloballyRejected` state coexists with a successful `broadcast_signed_block` call), and in the worst case it lets a block the majority already rejected still get delivered/signed-over to the node if enough of the *earlier* (pre-rejection) signature weight is still on file, since nothing purges previously-collected signatures when a block moves to `GloballyRejected`.

### Likelihood Explanation
The trigger requires only ordinary, single-participant conditions already anticipated by the codebase: any late `BlockAccepted` gossip message, or a pending signature response recorded in `signer_pending_signature_responses` before the proposal was known and replayed later via `process_pending_responses_for_block`. Both paths are normal, already-documented flows for out-of-order delivery (not requiring a malicious majority) — a single straggling accept re-delivered after this signer (or peers) already pushed the block to `GloballyRejected` is sufficient to reach `store_and_process_block_signature` with a state that has already reached consensus. The missing `has_reached_consensus()` guard (present on the rejection path, absent on the acceptance path) means this is a straightforward, always-reachable code path rather than a rare race.

### Recommendation
Add the same early-return guard used in `store_and_process_block_rejection` to `store_and_process_block_signature`: if `block_info.has_reached_consensus()` is true (particularly `GloballyRejected`), return before tallying weight or calling `broadcast_signed_block`. At minimum, gate the `broadcast_signed_block` call itself on the success of `mark_locally_accepted`/on `block_info.state` not being `GloballyRejected`, so a failed state transition can never be followed by delivering the block to the node.

### Proof of Concept
1. Signer S is tracking block B (state `Unprocessed`/`PreCommitted`).
2. A blocking minority (>30% weight) of rejections arrive/are recorded, and `store_and_process_block_rejection` calls `block_info.mark_globally_rejected()` → B's stored state becomes `GloballyRejected` [10](#0-9) .
3. A late `BlockAccepted` message (or a `signer_pending_signature_responses` entry recorded earlier and replayed via `process_pending_responses_for_block` when a re-proposal of the same block arrives) is processed by `handle_block_signature` → `store_and_process_block_signature`.
4. `add_block_signature` inserts the new signature and deletes any rejection row for that signer [11](#0-10) .
5. If the cumulative signature weight (this straggler plus earlier legitimate accept votes still on file from before rejection was reached) now ≥ `min_weight`, the threshold branch runs, `mark_locally_accepted` fails silently (state stays `GloballyRejected`), and `broadcast_signed_block` still executes, delivering B to the node despite the signer's own bookkeeping already recording it as globally rejected.

### Citations

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

**File:** stacks-signer/src/v0/signer.rs (L2290-2293)
```rust
        if block_info.has_reached_consensus() {
            // Checking the rejection signatures is pointless. We have already reached consensus on this block.
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2335-2341)
```rust
        if let Err(e) = block_info.mark_globally_rejected() {
            warn!("{self}: Failed to mark block as globally rejected: {e:?}",);
        }
        if let Err(e) = self.signer_db.insert_block(block_info) {
            error!("{self}: Failed to update block state: {e:?}",);
            panic!("{self} Failed to update block state: {e}");
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2467-2472)
```rust

        if block_info.signed_group.is_some() {
            // We have already processed this block to the accepted state. Adding more signatures will not change anything so nothing to check.
            return;
        }
        // do we have enough signatures to broadcast?
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

**File:** stacks-signer/src/signerdb.rs (L319-329)
```rust
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

**File:** stacks-signer/src/signerdb.rs (L1870-1890)
```rust
    /// Record an observed block signature
    pub fn add_block_signature(
        &self,
        block_sighash: &Sha512Trunc256Sum,
        signer_addr: &StacksAddress,
        signature: &MessageSignature,
    ) -> Result<bool, DBError> {
        // Remove any block rejection entry for this signer and block hash
        let del_qry = "DELETE FROM block_rejection_signer_addrs WHERE signer_signature_hash = ?1 AND signer_addr = ?2";
        let del_args = params![block_sighash, signer_addr.to_string()];
        self.db.execute(del_qry, del_args)?;

        // Insert the block signature
        let qry = "INSERT OR IGNORE INTO block_signatures (signer_signature_hash, signer_addr, signature) VALUES (?1, ?2, ?3);";
        let args = params![
            block_sighash,
            signer_addr.to_string(),
            serde_json::to_string(signature).map_err(DBError::SerializationError)?
        ];
        let rows_added = self.db.execute(qry, args)?;

```

**File:** stacks-signer/src/signerdb.rs (L1922-1940)
```rust
    /// Record an observed block rejection_signature
    pub fn add_block_rejection_signer_addr(
        &self,
        block_sighash: &Sha512Trunc256Sum,
        addr: &StacksAddress,
        reject_reason: RejectReasonPrefix,
    ) -> Result<bool, DBError> {
        // If this signer/block already has a signature, do not allow a rejection
        let sig_qry = "SELECT EXISTS(SELECT 1 FROM block_signatures WHERE signer_signature_hash = ?1 AND signer_addr = ?2)";
        let sig_args = params![block_sighash, addr.to_string()];
        let exists = self.db.query_row(sig_qry, sig_args, |row| row.get(0))?;
        if exists {
            warn!("Cannot add block rejection because a signature already exists.";
                "signer_signature_hash" => %block_sighash,
                "signer_address" => %addr,
                "reject_reason" => ?reject_reason
            );
            return Ok(false);
        }
```

**File:** docs/signer-flows.md (L322-327)
```markdown
A conflict is any block a signature was ever put over — ours, or a group
threshold we observed — whatever its state now. In particular rejection, even
_global_ rejection, does not clear one: a rejection is a revocable opinion,
while a signature is a bearer instrument that can still be aggregated toward
the 70% threshold if rejecting signers change their minds. Only staleness or
node-derived death (the two questions above) clears a conflict.
```
