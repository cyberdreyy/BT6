### Title
Stale `BlockInfo` Clobbers a Just-Signed Block's State via Reentrant `handle_block_pre_commit` in `process_pending_responses_for_block` — ([File: stacks-signer/src/v0/signer.rs])

### Summary
`process_pending_responses_for_block` replays early (pending) votes for a newly-tracked block proposal by iterating three queues — pre-commits, rejections, then signatures — all operating nominally on one `&mut BlockInfo` held by the caller. However, the pre-commit branch of that loop calls `handle_block_pre_commit`, which does **its own independent, fresh fetch of the same block from `signer_db`** (rather than operating on the caller's in-memory `block_info`), and can drive that fresh copy all the way to a signature (`mark_locally_accepted` + `handle_block_signature` + `signer_db.insert_block`). The caller's `block_info` is never refreshed after this nested write. When the loop subsequently processes queued **rejections** using that now-stale `block_info`, `store_and_process_block_rejection` can cross the rejection threshold and call `block_info.mark_globally_rejected()` followed by `signer_db.insert_block(block_info)`, overwriting the signerdb row that the nested pre-commit call had just updated to `LocallyAccepted`/signed — with a stale, "never signed" copy now marked `GloballyRejected`.

### Finding Description
The lifecycle is defined in `stacks-signer/src/v0/signer.rs`:

- `process_pending_responses_for_block` (lines 1730–1780) takes `block_info: &mut BlockInfo` from its caller (the fresh proposal-handling path) and loops over `pending_responses.pre_commits`, `.rejections`, and `.signatures` in that fixed order, calling `handle_block_pre_commit`, `store_and_process_block_rejection`, and `store_and_process_block_signature` respectively [1](#0-0) .
- Crucially, `handle_block_pre_commit` does **not** take the caller's `block_info` as an argument. It performs its own lookup: `let Some(mut block_info) = self.block_lookup_by_reward_cycle(block_hash)` [2](#0-1) . This is a second, independent in-memory copy of the same DB row.
- If enough pre-commits are already queued to cross the signing threshold, `handle_block_pre_commit` proceeds to sign: `mark_locally_accepted`, `handle_block_signature`, and writes the updated (now signed) `BlockInfo` back via `self.signer_db.insert_block(&block_info)` [3](#0-2) . This commit happens on the *fresh* copy, entirely independent of the caller's `block_info` in `process_pending_responses_for_block`.
- The outer loop's `block_info` (in `process_pending_responses_for_block`) is never re-read from `signer_db` after this nested write. It then moves on to the rejections queue and calls `store_and_process_block_rejection(sortition_state, block_info, &stacker_address, reject_reason)` [4](#0-3)  passing the same, now-stale `block_info`.
- `store_and_process_block_rejection` gates on `block_info.has_reached_consensus()` — evaluated against the **stale** object, which still shows `signed_self`/`signed_group` unset, since the update happened on the other in-memory copy [5](#0-4) . If enough queued rejections cross the rejection weight threshold, it calls `block_info.mark_globally_rejected()` and then `self.signer_db.insert_block(block_info)` [6](#0-5) , unconditionally overwriting the DB row with the stale, "never-signed" object now forced into `GloballyRejected` state — clobbering the `LocallyAccepted`/signed row the nested pre-commit path had just persisted.

This is structurally the same bug class as the `Oj::Doc` UAF: an outer iteration (`process_pending_responses_for_block`'s sequential replay) holds a handle to shared state, a nested/"reentrant" call (`handle_block_pre_commit` → sign path) mutates the backing store through a different handle, and the outer iteration's stale handle is used to perform a further write that clobbers the fresher state — a lost update rather than a memory UAF, but the same "act on a handle invalidated by a reentrant call" root cause.

### Impact Explanation
The persisted `signer_db` state for the block becomes internally inconsistent: the signer has already emitted and broadcast a valid signature (`add_block_signature`/`handle_block_signature`), but `signerdb`'s `BlockInfo` row is left in `GloballyRejected` state with `signed_self`/`signed_group` cleared to the stale (unsigned) values. Downstream logic that trusts `signerdb` state to prevent equivocation — e.g. `get_signed_conflicts`/`check_block_against_signer_db_state`, used in `handle_block_pre_commit`'s conflict guard (section 5 of `docs/signer-flows.md`) — relies on `signerdb` accurately reflecting "did I sign this" to block a future conflicting signature at the same height [7](#0-6) . With the signed state clobbered back to unsigned/rejected, the signer's own bookkeeping no longer proves it already signed this block, undermining the "one-per-height" equivocation guard and creating a path toward the signer later signing a conflicting block at the same height — a Critical-severity outcome per the specified impact classes (signer signing a conflicting block).

### Likelihood Explanation
This requires the pending queues for a single block hash to already contain enough queued pre-commits to cross the sign threshold *and* enough queued rejections to cross the reject threshold at the moment the proposal is finally processed — i.e., a genuine race between honest, gossip-timing-driven early votes recorded before the proposal itself arrives (the "early votes: acceptances, rejections, and pre-commits... parked in pending tables and replayed once the proposal is known" pattern explicitly documented in `docs/signer-flows.md`, lines 196–198). A single miner/relay controlling proposal delivery timing relative to gossip (the "one-slot miner plus gossip" primitive in scope) can influence when a proposal reaches this signer relative to peers' votes, making this ordering reachable without needing a signer majority — the necessary vote weight comes from otherwise-honest peers whose messages simply arrive out of order relative to the delayed proposal.

### Recommendation
`process_pending_responses_for_block` should not retain a separate, unsynchronized `block_info` handle across calls that internally re-fetch and mutate the same row. Either:
- Re-fetch `block_info` from `signer_db` (or return the updated copy) after each call in the pre-commit/rejection/signature replay loop, checking `has_reached_consensus()` against the freshest state before proceeding to the next queue, or
- Make `handle_block_pre_commit` (and the other handlers) accept and operate on the caller-owned `&mut BlockInfo` directly instead of performing an independent `block_lookup_by_reward_cycle`, so there is a single source of truth throughout the replay.

### Proof of Concept
1. A miner delays broadcasting `BlockProposal(B)` at height `h` relative to the signer set's gossip.
2. Peers, having already seen `B` by other means (e.g. relayed early, or re-proposal path), send `BlockPreCommit` and `BlockResponse::Rejected` for `signer_signature_hash(B)` before this signer has `B` tracked; these land in `pending_responses.pre_commits` / `.rejections` via `add_pending_block_pre_commit_response` / rejection pending tables.
3. When the proposal for `B` finally arrives and validation completes, `handle_block_proposal` creates a fresh `BlockInfo`, inserts it, and calls `process_pending_responses_for_block(..., &mut block_info, pending_responses)`.
4. The pre-commits loop replays enough weight to cross the sign threshold inside `handle_block_pre_commit`, which fetches its own copy of `B`'s row, signs it, and writes `LocallyAccepted` (signed) back to `signer_db`.
5. The rejections loop then runs on the caller's stale `block_info` (still showing unsigned); if the replayed rejection weight also crosses the reject threshold, `store_and_process_block_rejection` marks it `GloballyRejected` and calls `signer_db.insert_block(block_info)`, overwriting the just-signed row.
6. `signer_db` for `B` now shows `GloballyRejected`/unsigned even though a valid signature over `B` was broadcast — an inconsistency exploitable to weaken the conflict guard against a later conflicting block at height `h`. [8](#0-7) [2](#0-1) [3](#0-2) [9](#0-8)

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1251-1274)
```rust
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
```

**File:** stacks-signer/src/v0/signer.rs (L1466-1478)
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
```

**File:** stacks-signer/src/v0/signer.rs (L1730-1780)
```rust
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

**File:** docs/signer-flows.md (L248-268)
```markdown
    TH -- yes --> RECHECK{"chainstate checks still pass?<br/>check_block_against_signer_db_state<br/>→ section 7"}
    RECHECK -- no --> REJ["mark_locally_rejected,<br/>handle_block_rejection,<br/>broadcast rejection"]:::bad
    RECHECK -- yes --> CONF["signed conflicts at height ≥ h,<br/>in ANY tenure<br/>get_signed_conflicts"]
    CONF --> PERM{"covered by a reorg permit whose<br/>permitting sortition is still canonical?<br/>reorg_permit_stands"}
    PERM -- yes --> EXCL(["excluded — our signature must not<br/>block a replacement we sanctioned"]):::good
    PERM -- no --> FRESH{"any of them still fresh?<br/>last_endorsed > cutoff"}
    FRESH -- yes --> SORT{"conflict_still_blocks, question 1:<br/>is its tenure's sortition still on the<br/>canonical burn chain?<br/>get_sortition_by_burn_hash"}
    SORT -- "404, with the node's burnchain tip<br/>at or past the burn block — a fork<br/>orphaned the tenure" --> OWN
    SORT -- "canonical, or we never<br/>saved its burn block" --> LIVE{"question 2: does the node's chain<br/>still reach the block itself?<br/>get_tenure_tip(its tenure)"}
    SORT -- "could not ask, or 404 with the<br/>node's tip still below the burn block" --> HOLD1
    LIVE -- "yes — real chain state" --> HOLD1["refuse to sign for now<br/>(may sign once conflict is stale)"]:::hold
    LIVE -- "no, and it was<br/>globally accepted" --> OWN
    LIVE -- "no, only locally accepted<br/>— but above this height" --> OWN
    LIVE -- "no, only locally accepted<br/>and a sibling at this height" --> HOLD1
    LIVE -- "could not ask" --> HOLD1
    FRESH -- "no — all stale" --> OWN{"a conflict in this block's<br/>OWN tenure?"}
    OWN -- yes --> TIP{"own tenure confirmed<br/>at ≥ this height?<br/>get_tenure_tip(own tenure)"}
    TIP -- yes --> HOLD2["refuse to sign"]:::hold
    TIP -- "no — never confirmed" --> SIGN
    TIP -- "node unreachable" --> SIGN
    OWN -- no --> SIGN["SIGN: mark_locally_accepted,<br/>handle_block_signature,<br/>broadcast acceptance"]:::good
```
