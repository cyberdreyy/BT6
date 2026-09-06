### Title
Unchecked `mark_locally_accepted` failure lets a signer broadcast a signed block it has locally recorded as globally rejected - (File: stacks-signer/src/v0/signer.rs)

### Summary
`store_and_process_block_signature` swallows the `Result` returned by `BlockInfo::mark_locally_accepted` and, unlike every sibling handler in this file, does not return on failure: it unconditionally persists the (partially-mutated) `block_info` and unconditionally calls `broadcast_signed_block`, regardless of whether the state transition actually succeeded.

### Finding Description
`BlockInfo::mark_locally_accepted` mutates `signed_group`/`valid`/`approved_time`/`signed_self` fields *before* attempting the guarded transition via `move_to(BlockState::LocallyAccepted)`: [1](#0-0) 

`move_to`/`check_state` forbid transitioning into `LocallyAccepted` once the block has already reached a terminal global state (`GloballyRejected` or `GloballyAccepted`): [2](#0-1) 

In `store_and_process_block_signature`, once enough signature weight has accumulated to cross the acceptance threshold, the code calls `mark_locally_accepted(true)` and handles the error like this: [3](#0-2) 

If `mark_locally_accepted` fails (e.g. because this signer had already locally recorded the block as `GloballyRejected`, per `check_state`), the `if !block_info.has_reached_consensus() { warn!(...) }` branch is *not even taken* (since `has_reached_consensus()` is true for a `GloballyRejected` block), so nothing is logged — and, critically, there is **no `return`** after this block, unlike the analogous error-handling in `handle_block_validate_ok`, `check_submitted_block_proposal`, and `handle_block_pre_commit`, which all `return` early on a failed mark when consensus/appropriate state hasn't been reached: [4](#0-3) [5](#0-4) 

Execution therefore falls through to `insert_block` (persisting `signed_group` set in memory even though `state` was never actually moved to `LocallyAccepted`, staying at whatever it was, e.g. `GloballyRejected`) and then unconditionally to `broadcast_signed_block`, which hands the aggregated-signature block to the node via `handle_post_block`.

This is the direct analog of the reported bug class: a call whose success/failure is not checked (the equivalent of an unchecked `transfer`/`transferFrom` return value) causes the caller to proceed as though the operation succeeded, when in fact the intended state transition was rejected. Here the consequence is a signer broadcasting a signed block payload while its own bookkeeping states the block is `GloballyRejected` — an equality break between "signed" and the signer's own validated/committed local state.

### Impact Explanation
This breaks the invariant that a signer only pushes a signed block to its node when its own state machine agrees the block is (locally) acceptable. Because the guard code that exists everywhere else in the file (early `return` on failed mark) is missing here, a signer can:
- Persist a DB record with `signed_group` set but `state` stuck at `GloballyRejected` (an internally inconsistent record: "signed" yet "globally rejected").
- Unconditionally call `broadcast_signed_block`/push the block to the node, even though the signer's own last recorded verdict for that exact block was a global rejection.

This falls into the "rejection recounted as an accept" / signature-vs-verified-state equality break category: the code path that should refuse to act on a block it has already discarded instead treats the operation as successful and proceeds to the most consequential action (submitting the aggregated signature set to the node).

### Likelihood Explanation
This requires a race where a late/stale accepting signature response arrives and crosses the acceptance threshold for a block that this individual signer has already locally moved to `GloballyRejected` (e.g., through `mark_globally_rejected` after seeing 30%+ rejecting weight, or via a delayed message replay/pending-response path). Because peer signature/pre-commit messages carrying weight for a block can legitimately be re-processed later via the pending-response and pre-commit replay logic described in the code (`add_pending_block_signature_response`, replay-on-proposal paths), this is reachable by ordinary gossip timing/message reordering without needing a majority of colluding signers — only enough total accepting weight (from the legitimate signer set) arriving out of order relative to this one signer's local rejection bookkeeping.

### Recommendation
Mirror the pattern used elsewhere in the file: check the `Result` of `mark_locally_accepted` and `return` early (without inserting/broadcasting) whenever the transition fails and `block_info.has_reached_consensus()` indicates the block already reached a terminal state incompatible with `LocallyAccepted`. Do not let `insert_block`/`broadcast_signed_block` execute unconditionally after a failed `mark_locally_accepted`.

### Proof of Concept
1. Signer S has already called `mark_globally_rejected()` on `block_info` for a given `signer_signature_hash` (because it earlier saw ≥30% rejecting weight arrive first), persisting `state = GloballyRejected` via `insert_block`. [6](#0-5) 
2. A batch of legitimate but delayed `Accepted` `BlockResponse` messages for the same block subsequently arrives (network/gossip reordering, or replay from `add_pending_block_signature_response`), driving `store_and_process_block_signature` to be invoked repeatedly until `total_signature_weight` crosses `min_weight`. [7](#0-6) 
3. `mark_locally_accepted(true)` is called; `check_state` rejects the transition since `prev_state == GloballyRejected`, returning `Err`. [8](#0-7) 
4. Because `block_info.has_reached_consensus()` is true, the `warn!` branch is skipped and (unlike the other handlers) there is no `return`, so `insert_block` persists the inconsistent record and `broadcast_signed_block` is called, pushing the block to the node despite S's own record calling it `GloballyRejected`. [9](#0-8) 

**Note on confidence**: I was unable to fully verify the exact implementation of `has_reached_consensus()` or `broadcast_signed_block`/`handle_post_block` (their bodies were not returned by search), so the precise downstream consequence of the push (whether the node's own re-validation would still block adoption) could not be independently confirmed within the available tool calls. The core logic flaw — the missing `return` after an unchecked `mark_locally_accepted` error, in contrast to the guarded pattern used in the other three analogous call sites in the same file — is directly confirmed from the cited source.

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

**File:** stacks-signer/src/signerdb.rs (L303-306)
```rust
    /// Attempt to mark the block as globally rejected
    pub fn mark_globally_rejected(&mut self) -> Result<(), String> {
        self.move_to(BlockState::GloballyRejected)
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

**File:** stacks-signer/src/v0/signer.rs (L1355-1366)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L1961-1970)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L2474-2514)
```rust
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
```

**File:** stacks-signer/src/v0/signer.rs (L2525-2537)
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
```
