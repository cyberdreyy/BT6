### Title
Signer rejection signature only commits to the block hash, letting the reason/response-data be swapped without invalidating the signature - (File: [libsigner/src/v0/messages.rs](libsigner/src/v0/messages.rs))

### Summary
`BlockRejection::hash()` builds the value that gets signed and verified for a signer's rejection message, but it only binds `self.signer_signature_hash` (plus `chain_id` via the domain tuple). It does **not** bind `reason`, `reason_code`, or `response_data` (which carries `reject_reason`, `failed_txid`, and the tenure/read-count extend timestamps). Any party that can re-wrap the payload (a relaying signer, a StackerDB write path, or any code that reconstructs a `SignerMessage`) can swap those fields while the original signature still verifies successfully, because `verify()`/`recover_public_key()` only check the hash of the block-hash+chain-id, never the reason metadata that consumers actually act on. [1](#0-0) 

### Finding Description
`BlockRejection` is the signed message a signer broadcasts to reject a block: [2](#0-1) 

Its signature is produced and verified purely over the block's `signer_signature_hash`: [1](#0-0) 

`reason`, `reason_code`, and `response_data` (including `reject_reason: RejectReason`, `failed_txid`, and both tenure-extend timestamps) are plain, unauthenticated fields of the struct that ride alongside the signature but are never hashed into it. Both consumers of this message trust these fields directly once `recover_public_key`/signer-set membership succeeds:

- `handle_block_rejection` in the signer trusts `rejection.response_data.reject_reason` to decide which `RejectReasonPrefix` weight bucket the vote counts toward, and ultimately (via `store_and_process_block_rejection`) uses the `ReorgNotAllowed` reject-code weight to flip the miner's sortition status to `InvalidatedBeforeFirstBlock`: [3](#0-2) [4](#0-3) 

- The node's `StackerDBListener` trusts `rejected_data.reason_code` and `rejected_data.response_data.failed_txid` to accumulate per-txid "problematic" weight that is later used to temporarily/permanently exclude transactions from future block proposals: [5](#0-4) [6](#0-5) 

Because the signature only authenticates `signer_signature_hash` (+ `chain_id`), the rest of the struct is exactly the "swapData" analog from the external report: the code blindly trusts a value (`reason_code`/`response_data`) that a message-forwarding party can rewrite without breaking the cryptographic check that gates acceptance. `verify()` will return `true` for a message whose signature was produced for one `reason_code`/`response_data` combination but whose fields have been swapped to a different combination for the same block hash — an equivocation the signature was supposed to prevent.

### Impact Explanation
A single relaying/gossip party (not a majority, no other signer's key, no auth token needed) that can re-emit a legitimately-signed rejection for a given block can rewrite:
- `response_data.reject_reason` to `ReorgNotAllowed` while keeping the original signature, artificially inflating the `ReorgNotAllowed` weight bucket that `store_and_process_block_rejection` uses to mark the current miner's sortition `InvalidatedBeforeFirstBlock` — this can wedge a signer into refusing to sign a valid, canonical block from the legitimate miner (liveness break), or
- `response_data.failed_txid` / `reason_code` to `ProblematicTransaction`/`BadTransaction` for an arbitrary txid, feeding the node's `failed_txids` weight tally in `stackerdb_listener.rs` and causing transactions to be excluded from future proposals under a forged signer attribution.

Both are miscounted-response outcomes stemming directly from a signature that fails to bind the data it is meant to authenticate — matching the "Critical: rejection recounted as acceptance"/"High: signer wedged" impact classes, since the signer set's safety/liveness accounting is driven by fields the signature never covers.

### Likelihood Explanation
Reaching this requires only re-transmitting an already-signed `BlockRejection` with mutated `reason`/`reason_code`/`response_data` fields — no majority collusion, no possession of any signer's private key, and no privileged access, only the ability to relay/gossip a message (explicitly in-scope). The primary caveat is the outer transport: if the outer StackerDB chunk write requires a signature over the full serialized bytes (chunk-level signing by the slot owner), then a third-party relay cannot alter these fields without breaking that outer signature; only the original signer (or anything reconstructing `BlockRejection` and re-signing at the chunk level while reusing the inner unmodified `signature`/`hash()` value) could exploit this without needing the outer signing key of the message's true author. This is a real code-level defect (the signed payload is under-specified relative to what is trusted), independent of that transport nuance.

### Recommendation
Include all fields that consumers act upon in the signed hash — at minimum `reason_code`/`response_data` (which subsumes `reject_reason`, `failed_txid`, and the extend timestamps) — inside `BlockRejection::hash()`, mirroring how `chain_id` and `signer_signature_hash` are already committed. This ensures `verify()`/`recover_public_key()` fail whenever any consumed field is altered, closing the gap between "signed" and "validated" for rejection messages.

### Proof of Concept
1. Signer A rejects block `B` (hash `H`) with `reason_code = ValidationFailed(BadTransaction)`, `failed_txid = tx1`, producing `BlockRejection { signer_signature_hash: H, reason_code, response_data, signature: S }` where `S = sign(hash({"block-rejection","1.0.0",chain_id}, H))` per `BlockRejection::hash`/`sign` (`libsigner/src/v0/messages.rs:1802-1814`).
2. An intermediary reconstructs the struct, changing `reason_code` to `RejectCode::ValidationFailed(ProblematicTransaction)` and/or `response_data.failed_txid` to `tx2`, keeping `signer_signature_hash = H` and `signature = S` unchanged.
3. Any receiver calls `rejection.verify(pubkey_A)` / `recover_public_key()` (`libsigner/src/v0/messages.rs:1816-1838`) — both succeed because they only check `hash()`, which never included `reason_code`/`response_data`.
4. The receiving signer's `handle_block_rejection` → `store_and_process_block_rejection` (`stacks-signer/src/v0/signer.rs:2208-2368`) or the node's `stackerdb_listener.rs:486-546` now attribute the forged `reason_code`/`failed_txid` to signer A, feeding the `ReorgNotAllowed` miner-invalidation tally or the txid-exclusion tally with data signer A never actually signed.

### Citations

**File:** libsigner/src/v0/messages.rs (L1713-1730)
```rust
/// A rejection response from a signer for a proposed block
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BlockRejection {
    /// The reason for the rejection
    pub reason: String,
    /// The reason code for the rejection
    pub reason_code: RejectCode,
    /// The signer signature hash of the block that was rejected
    pub signer_signature_hash: Sha512Trunc256Sum,
    /// The signer's signature across the rejection
    pub signature: MessageSignature,
    /// The chain id
    pub chain_id: u32,
    /// Signer message metadata
    pub metadata: SignerMessageMetadata,
    /// Extra versioned block response data
    pub response_data: BlockResponseData,
}
```

**File:** libsigner/src/v0/messages.rs (L1802-1838)
```rust
    /// The signature hash for the block rejection
    pub fn hash(&self) -> Sha256Sum {
        let domain_tuple = make_structured_data_domain("block-rejection", "1.0.0", self.chain_id);
        let data = Value::buff_from(self.signer_signature_hash.as_bytes().into()).unwrap();
        structured_data_message_hash(data, domain_tuple)
    }

    /// Sign the block rejection and set the internal signature field
    fn sign(&mut self, private_key: &StacksPrivateKey) -> Result<(), String> {
        let signature_hash = self.hash();
        self.signature = private_key.sign(signature_hash.as_bytes())?;
        Ok(())
    }

    /// Verify the rejection's signature against the provided signer public key
    pub fn verify(&self, public_key: &StacksPublicKey) -> Result<bool, String> {
        if self.signature == MessageSignature::empty() {
            return Ok(false);
        }
        let signature_hash = self.hash();
        public_key
            .verify(&signature_hash.0, &self.signature)
            .map_err(|e| e.to_string())
    }

    /// Recover the public key from the rejection signature
    pub fn recover_public_key(&self) -> Result<StacksPublicKey, &'static str> {
        if self.signature == MessageSignature::empty() {
            return Err("No signature to recover public key from");
        }
        let signature_hash = self.hash();
        StacksPublicKey::recover_to_pubkey_without_validating_low_s(
            signature_hash.as_bytes(),
            &self.signature,
        )
    }
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

**File:** stacks-signer/src/v0/signer.rs (L2342-2368)
```rust
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
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L486-546)
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
                                    _ => {}
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
