## Finding: Signature does not authenticate the rejection reason, allowing content-swapped `BlockRejection` forgery

### Title
Unauthenticated `reason_code`/`response_data` fields in `BlockRejection` allow a single relaying party to forge a signer's rejection reason - ([File: libsigner/src/v0/messages.rs])

### Summary
`BlockRejection::hash()` (and thus the message-level signature produced by `BlockRejection::sign`/verified by `BlockRejection::verify`/`recover_public_key`) only covers `signer_signature_hash` and `chain_id`:

```
pub fn hash(&self) -> Sha256Sum {
    let domain_tuple = make_structured_data_domain("block-rejection", "1.0.0", self.chain_id);
    let data = Value::buff_from(self.signer_signature_hash.as_bytes().into()).unwrap();
    structured_data_message_hash(data, domain_tuple)
}
``` [1](#0-0) 

Neither `reason_code`, `reason`, nor any field of `response_data` (which carries `reject_reason`, `failed_txid`, `tenure_extend_timestamp`, etc.) is included in the signed payload. `recover_public_key`/`verify` recompute this same restricted `hash()`, so they will happily validate against a `BlockRejection` whose `reason_code`/`response_data` have been swapped out, as long as `signer_signature_hash` and `chain_id` (and the untouched `signature` bytes) are kept intact.

This is the same class of bug as the scratch-vm advisory (CWE-502): a protection mechanism (the signature) was intended to bind the whole message, but a structural detail (only a sub-field is actually hashed) lets an attacker swap out a "trusted" part of the payload while the authenticity check still passes.

### Finding Description
Every consumer of a `BlockRejection` trusts `reason_code`/`response_data.reject_reason` as if it were signed by the recovered signer:

- `stacks-signer/src/v0/signer.rs::handle_block_rejection` recovers the public key with `rejection.recover_public_key()`, checks it is a valid signer address, and then feeds `rejection.response_data.reject_reason` straight into `store_and_process_block_rejection`, which tallies rejection weight **per `RejectReasonPrefix`** to decide whether to mark the block globally rejected and — pre-global-state — whether ≥30% of weight rejected specifically for `RejectReasonPrefix::ReorgNotAllowed`, in which case the miner is flagged `InvalidatedBeforeFirstBlock`. [2](#0-1) [3](#0-2) 

- On the miner/node side, `stacks-node/src/nakamoto_node/stackerdb_listener.rs` recovers the public key from `rejected_data.recover_public_key()`, checks it matches the expected signer for that StackerDB slot, and then uses `rejected_data.reason_code` and `rejected_data.response_data.failed_txid` to accumulate per-txid `total_weight`/`problematic_weight`, which feeds `permanently_excluded_txids`/`temporarily_excluded_txids` in `signer_coordinator.rs`. [4](#0-3) [5](#0-4) 

Because `reason_code`/`response_data` sit outside the signed hash, any party that can observe a genuine `BlockRejection` from Signer A (rejection messages are broadcast in the clear over StackerDB/gossip) can construct a new `BlockRejection` struct that keeps A's original `signer_signature_hash`, `chain_id`, and `signature` bytes unchanged, but substitutes an arbitrary `reason_code` / `response_data` (different `failed_txid`, different `reject_reason`, e.g. swapping a benign `ValidationFailed` into `ReorgNotAllowed`, or attributing a `ProblematicTransaction` verdict to an unrelated txid). `recover_public_key()`/`verify()` will still report the message as authentically signed by A, because the hash they check never included the swapped fields.

The forger needs their own valid StackerDB write slot (any signer/miner-observer with normal write access) to relay the doctored message — no majority of signers, no private key of A, and no auth token are required. This is the exact "single non-majority actor plus gossip" primitive the scope calls for.

### Impact Explanation
This breaks the "rejection recounted" equality explicitly called out in scope:
- A doctored rejection can be counted toward the `ReorgNotAllowed` bucket in `store_and_process_block_rejection` even though the actual signer never rejected for that reason, causing the sortition view to mark a legitimate miner `InvalidatedBeforeFirstBlock` on the strength of a forged 30% threshold contribution — a safety-relevant miscount of accept/reject weight that can wedge or misdirect block production.
- On the node side, a forged `reason_code`/`failed_txid` can inject an unrelated transaction into `permanently_excluded_txids`/`temporarily_excluded_txids`, again driven by weight that was never actually cast for that reason — corrupting the "aggregated-weight vs verified-accepts" invariant that `signer_coordinator.rs` relies on to decide whether to keep or drop transactions from a block.

Both outcomes match the Critical/High impact categories in scope: a miscounted response feeding into consensus-affecting decisions (miner invalidation, tx exclusion) without requiring a majority of colluding signers.

### Likelihood Explanation
Rejections are broadcast in the clear on StackerDB (and reach the node via `stackerdb_listener.rs`), so an attacker only needs (a) to observe one genuine `BlockRejection` from a target signer for the block in question, and (b) normal write access to any StackerDB slot to relay the doctored copy. No signer private key, no majority, and no elevated node access is needed — only the ability to construct and gossip a re-serialized `BlockRejection` with the reason fields altered while keeping the byte-for-byte `signer_signature_hash`/`chain_id`/`signature`.

### Recommendation
Bind `reason_code` (and ideally the full `response_data`) into the structured-data hash signed/verified in `BlockRejection::hash()`, so any tampering with the reason invalidates the signature. This mirrors how `BlockAccepted`/other signer messages should authenticate their full semantic payload, not just the block identifier.

### Proof of Concept
1. Signer A validates a proposed block and rejects it for `RejectCode::ValidationFailed(...)`, producing a signed `BlockRejection { signer_signature_hash: H, chain_id: C, signature: S, reason_code: ValidationFailed(...), response_data: {...} }`, broadcast over StackerDB.
2. An observer (any signer, or the node itself) copies `H`, `C`, `S` verbatim into a new `BlockRejection` struct but replaces `reason_code` with `RejectCode::ReorgNotAllowed` (or swaps `response_data.failed_txid` to target an arbitrary transaction), and republishes it (e.g. attributed under its own write slot or relayed as gossip).
3. Any recipient calling `recover_public_key()`/`verify()` on the doctored message recomputes `hash()` from `H`/`C` only — unchanged — so the signature check succeeds and the recovered public key still resolves to Signer A's address.
4. `store_and_process_block_rejection` (signer side) or the `failed_txids` accumulation in `stackerdb_listener.rs` (node side) now counts Signer A's weight toward a rejection reason A never actually chose.

### Note on verification limits
I was unable to directly inspect `stacks-signer/src/signerdb.rs::add_block_rejection_signer_addr` (the last tool call returned only a match count, not the body) to confirm exactly how a second/duplicate rejection for the same `(block_hash, signer_address)` but a different `reason_code` is deduped or overwritten — that would refine whether the forged message must be the *first* rejection observed for that address/block, or can supersede an already-stored one. The core defect (unauthenticated `reason_code`/`response_data`) is confirmed directly from `libsigner/src/v0/messages.rs`, independent of that storage detail.

### Citations

**File:** libsigner/src/v0/messages.rs (L1802-1807)
```rust
    /// The signature hash for the block rejection
    pub fn hash(&self) -> Sha256Sum {
        let domain_tuple = make_structured_data_domain("block-rejection", "1.0.0", self.chain_id);
        let data = Value::buff_from(self.signer_signature_hash.as_bytes().into()).unwrap();
        structured_data_message_hash(data, domain_tuple)
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2208-2265)
```rust
    /// Handle an observed rejection from another signer
    fn handle_block_rejection(
        &mut self,
        rejection: &BlockRejection,
        sortition_state: &mut Option<SortitionsView>,
    ) {
        debug!("{self}: Received a block-reject signature: {rejection:?}");

        let block_hash = &rejection.signer_signature_hash;
        let signature = &rejection.signature;

        // recover public key
        let Ok(public_key) = rejection.recover_public_key() else {
            debug!("{self}: Received block rejection with an unrecovarable signature. Will not store.";
               "signer_signature_hash" => %block_hash,
               "signature" => %signature
            );
            return;
        };

        // authenticate the signature -- it must be signed by one of the stacking set
        let signer_address = StacksAddress::p2pkh(self.mainnet, &public_key);
        if !self.is_valid_signer(&signer_address) {
            debug!("{self}: Received block rejection with an invalid signature. Will not store.";
                "signer_public_key" => ?public_key,
                "signer_address" => %signer_address,
                "signer_signature_hash" => %block_hash,
                "signature" => %signature
            );
            return;
        }

        let Some(mut block_info) = self.block_lookup_by_reward_cycle(block_hash) else {
            if let Err(e) = self.signer_db.add_pending_block_rejection_response(
                block_hash,
                &signer_address,
                (&rejection.response_data.reject_reason).into(),
            ) {
                warn!("{self}: Failed to add pending block rejection response: {e:?}");
            }
            return;
        };

        info!("{self}: Received block rejection";
            "signer_pubkey" => public_key.to_hex(),
            "signer_signature_hash" => %block_hash,
            "consensus_hash" => %block_info.block.header.consensus_hash,
            "block_height" => block_info.block.header.chain_length,
            "reject_reason" => ?rejection.response_data.reject_reason,
        );

        self.store_and_process_block_rejection(
            sortition_state,
            &mut block_info,
            &signer_address,
            (&rejection.response_data.reject_reason).into(),
        );
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2267-2369)
```rust
    // Store the block rejection signature and check if we have reached a consensus decision on the block because of it. If we have, update the block state accordingly.
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
        // NOTE: This is only used by active signer protocol versions < Global state activation
        // If 30% of the signers have rejected the block due to an invalid
        // reorg, mark the miner as invalid.
        // If we cannot determine the active signer protocol version it means we are
        // running a global state machine version that couldn't reach consensus, so we can skip this check
        if self
            .determine_active_signer_protocol_version()
            .map(|version| version.uses_global_state())
            .unwrap_or(true)
        {
            return;
        };
        let total_reorg_reject_weight = self.compute_reject_code_signing_weight(
            rejection_addrs.iter(),
            RejectReasonPrefix::ReorgNotAllowed,
        );
        if total_reorg_reject_weight.saturating_add(min_weight) > total_weight {
            // Mark the miner as invalid
            if let Some(sortition_state) = sortition_state {
                let ch = block_info.block.header.consensus_hash.clone();
                if sortition_state.cur_sortition.data.consensus_hash == ch {
                    info!("{self}: Marking miner as invalid for attempted reorg");
                    sortition_state.cur_sortition.miner_status =
                        SortitionMinerStatus::InvalidatedBeforeFirstBlock;
                }
            }
        }
    }
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L486-543)
```rust
                    SignerMessageV0::BlockResponse(BlockResponse::Rejected(rejected_data)) => {
                        let (lock, cvar) = &*self.blocks;
                        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");

                        let Some(block) = blocks.get_mut(&rejected_data.signer_signature_hash)
                        else {
                            info!(
                                "StackerDBListener: Received rejection for block that we did not request. Ignoring.";
                                "signer_signature_hash" => %rejected_data.signer_signature_hash,
                                "slot_id" => slot_id,
                                "signer_set" => self.signer_set,
                            );
                            continue;
                        };

                        let rejected_pubkey = match rejected_data.recover_public_key() {
                            Ok(rejected_pubkey) => {
                                if rejected_pubkey != signer_pubkey {
                                    warn!("StackerDBListener: Recovered public key from rejected data does not match signer's public key. Ignoring.");
                                    continue;
                                }
                                rejected_pubkey
                            }
                            Err(e) => {
                                warn!("StackerDBListener: Failed to recover public key from rejected data: {e:?}. Ignoring.");
                                continue;
                            }
                        };

                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);

                            // Track transactions that failed validation, accumulating
                            // per-txid signer weight and whether any signer flagged
                            // the tx as genuinely problematic.
                            if let Some(txid) = &rejected_data.response_data.failed_txid {
                                match &rejected_data.reason_code {
                                    RejectCode::ValidationFailed(
                                        ValidateRejectCode::BadTransaction
                                        | ValidateRejectCode::ProblematicTransaction,
                                    ) => {
                                        let info =
                                            block.failed_txids.entry(txid.clone()).or_default();
                                        info.total_weight =
                                            info.total_weight.saturating_add(signer_entry.weight);
                                        if matches!(
                                            rejected_data.reason_code,
                                            RejectCode::ValidationFailed(
                                                ValidateRejectCode::ProblematicTransaction
                                            )
                                        ) {
                                            info.problematic_weight = info
                                                .problematic_weight
                                                .saturating_add(signer_entry.weight);
                                        }
                                    }
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L512-540)
```rust
                > self.total_weight
            {
                info!(
                    "{}/{} signer weight votes to reject block",
                    block_status.total_weight_rejected, self.total_weight;
                    "signer_signature_hash" => %block_signer_sighash,
                );
                counters.bump_naka_rejected_blocks();

                // Only act on failed txids that a blocking minority (>30% weight) agrees on
                let blocking_minority = self.total_weight.saturating_sub(self.weight_threshold);
                let mut temporarily_excluded_txids = HashSet::new();
                let mut permanently_excluded_txids = HashSet::new();
                for (txid, info) in &block_status.failed_txids {
                    if info.total_weight > blocking_minority {
                        // Do not perma ban txids that only a small minority of signers reported as problematic
                        // But make sure its removed from the next block proposal
                        if info.problematic_weight > blocking_minority {
                            permanently_excluded_txids.insert(txid.clone());
                        } else {
                            temporarily_excluded_txids.insert(txid.clone());
                        }
                    }
                }

                return Err(NakamotoNodeError::SignersRejected {
                    temporarily_excluded_txids,
                    permanently_excluded_txids,
                });
```
