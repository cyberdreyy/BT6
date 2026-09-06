## Cross-Context-Valid Signature in `BlockRejection` — Signed Hash Omits `reason_code`/`response_data`

### Title
Cross-context-valid signer signature in `BlockRejection` lets a single malicious signer relabel any other signer's rejection reason/vote weight — ([File: libsigner/src/v0/messages.rs])

### Summary
`BlockRejection::hash()` computes the SIP-018 message digest that the signer's ECDSA signature actually covers, but that digest only commits to `signer_signature_hash` and `chain_id` — not to `reason`, `reason_code`, or `response_data` (which carries `reject_reason`, `failed_txid`, and the tenure-extend timestamps). Because `handle_block_rejection` attributes a rejection to whichever address the *embedded* signature recovers to (independent of the StackerDB chunk writer), a single malicious signer can take any legitimately-broadcast `BlockRejection` signature it has observed on the wire and repackage it with attacker-chosen `reason_code`/`response_data`, and the forged message will still pass `verify()`/`recover_public_key()` and be attributed to the honest signer who produced the original signature.

### Finding Description
`BlockRejection::hash()` is: [1](#0-0) 

Only `self.signer_signature_hash` and `self.chain_id` (via the SIP-018 domain tuple) are ever hashed and signed; `reason`, `reason_code`, and `response_data` are excluded entirely, even though they are struct fields on `BlockRejection`: [2](#0-1) 

`verify()`/`recover_public_key()` check exactly that same restricted digest: [3](#0-2) 

On the receiving side, `handle_event_match` first filters `SignerEvent::SignerMessages` by the StackerDB *chunk writer's* address (`is_valid_signer(&signer_address)` using `signer_public_key`) purely to decide whether to process the message at all: [4](#0-3) 

But the actual identity used to *attribute* the rejection (for storage and weight accounting) is recovered independently from the embedded signature inside `handle_block_rejection`, with no cross-check against the chunk writer: [5](#0-4) 

That recovered address is then used to persist and weight the rejection by whatever `reject_reason` is present in the (unsigned) `response_data`: [6](#0-5) 

`store_and_process_block_rejection` uses this attacker-controllable `reject_reason` to compute the `ReorgNotAllowed` weight, and if the weighted total exceeds threshold, marks the miner as invalid for the sortition — a direct, consensus-relevant action taken purely off unsigned data: [7](#0-6) 

Since all `BlockRejection` traffic is broadcast on the public `.signers-0-X`/`.signers-1-X` StackerDB contracts, any participant (including a malicious signer holding just one slot) can observe every legitimate signer's `(signer_signature_hash, chain_id, signature)` triple for a given block rejection. Because the signature never commits to `reason_code`/`response_data`, the attacker can construct a brand-new `BlockRejection` struct that copies a victim's real `signer_signature_hash`, `chain_id`, and `signature`, but substitutes an arbitrary `reason`, `reason_code` (e.g. `RejectCode::RejectedInPriorRound` mapped to `RejectReasonPrefix::ReorgNotAllowed`), and `response_data` (arbitrary `tenure_extend_timestamp`/`tenure_extend_read_count_timestamp`/`failed_txid`). This forged message still passes `recover_public_key()`/`verify()` as if the victim itself produced it, and the attacker writes it into their own StackerDB slot (which the outer chunk-writer check happily accepts, since it's the attacker's own valid slot).

This is a textbook "cross-context-valid signature": a signature meant to authenticate one specific rejection payload is valid across an unbounded set of semantically different payloads, letting an unauthorized party relabel another signer's vote.

### Impact Explanation
This directly matches the Critical impact category "a cross-context-valid signature." Concretely, a single malicious signer can:
- Reattribute honest signers' rejections to the `ReorgNotAllowed` reason code and push `total_reorg_reject_weight` over `min_weight`, causing peers to mark a legitimate miner as `SortitionMinerStatus::InvalidatedBeforeFirstBlock` even though no real quorum of signers voted `ReorgNotAllowed` — a liveness attack that can stall an honest miner's tenure using votes it never actually cast.
- Rewrite `tenure_extend_timestamp` / `tenure_extend_read_count_timestamp` embedded in a victim's genuinely-signed rejection, corrupting the idle-timeout bookkeeping (`update_idle_timestamp`, `update_read_count_timestamp`) that other signers derive from that (falsely-attributed) message: [8](#0-7) 
- Corrupt the `block_rejection_signer_addrs` table's per-address `reject_code`, since `add_block_rejection_signer_addr` simply overwrites the stored reason if a new code is seen for that `(signer_signature_hash, signer_addr)` pair: [9](#0-8) 

### Likelihood Explanation
No majority of signers and no victim's private key is required — only observation of publicly-broadcast StackerDB traffic (which every network participant, including a one-slot signer, can read) plus the attacker's own slot-writing key. The forged struct is trivially constructed by copying three fields (`signer_signature_hash`, `chain_id`, `signature`) from an observed rejection and substituting the rest.

### Recommendation
Include `reason_code` (or a canonical serialization of it) and the semantically load-bearing parts of `response_data` (`reject_reason`, `failed_txid`, timestamps) inside the data hashed by `BlockRejection::hash()`, e.g. by hashing a canonical, length-prefixed encoding of the full response payload rather than just `signer_signature_hash`. Alternatively, bind the recovered signer identity to the outer StackerDB chunk-writer identity so that a message cannot be attributed to any address other than the one that actually wrote the chunk.

### Proof of Concept
1. Attacker controls one signer slot and observes on `.signers-*` StackerDB that honest Signer V broadcasts `BlockRejection { reason: "InvalidBlock", reason_code: ValidationFailed(InvalidBlock), signer_signature_hash: H, chain_id: C, signature: S, response_data: { reject_reason: InvalidParentBlock, ... } }`.
2. Attacker constructs `BlockRejection { reason: "reorg", reason_code: RejectedInPriorRound, signer_signature_hash: H, chain_id: C, signature: S, response_data: { reject_reason: ReorgNotAllowed, ... } }` — same `H`/`C`/`S`, different reason fields.
3. `rejection.verify(V_pubkey)` / `recover_public_key()` succeed because `hash()` only depends on `H` and `C`, both unchanged.
4. Attacker wraps this in `SignerMessage::BlockResponse(BlockResponse::Rejected(...))` and writes it via their own StackerDB slot.
5. Every peer signer's `handle_block_rejection` accepts it as coming from V (`is_valid_signer` passes on the recovered address) and records `reject_reason = ReorgNotAllowed` for V in `block_rejection_signer_addrs`, contributing V's real signing weight toward `compute_reject_code_signing_weight(..., ReorgNotAllowed)` even though V never voted that reason.

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

**File:** libsigner/src/v0/messages.rs (L1802-1814)
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
```

**File:** libsigner/src/v0/messages.rs (L1816-1837)
```rust
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
```

**File:** stacks-signer/src/v0/signer.rs (L519-550)
```rust
            SignerEvent::SignerMessages {
                received_time,
                messages,
                ..
            } => {
                debug!(
                    "{self}: Received {} messages from the other signers",
                    messages.len()
                );
                // try and gather signatures
                for (_slot_id, signer_public_key, message) in messages {
                    let signer_address = StacksAddress::p2pkh(self.mainnet, signer_public_key);
                    if !self.is_valid_signer(&signer_address) {
                        debug!("{self}: Received a message from an unknown signer. Ignoring...";
                            "signer_public_key" => ?signer_public_key,
                            "signer_address" => %signer_address,
                            "message" => ?message,
                        );
                        continue;
                    }
                    match message {
                        SignerMessage::BlockResponse(block_response) => {
                            #[cfg(any(test, feature = "testing"))]
                            if self.test_ignore_all_block_responses(block_response) {
                                continue;
                            }
                            self.handle_block_response(
                                stacks_client,
                                block_response,
                                sortition_state,
                            )
                        }
```

**File:** stacks-signer/src/v0/signer.rs (L2208-2238)
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
```

**File:** stacks-signer/src/v0/signer.rs (L2259-2264)
```rust
        self.store_and_process_block_rejection(
            sortition_state,
            &mut block_info,
            &signer_address,
            (&rejection.response_data.reject_reason).into(),
        );
```

**File:** stacks-signer/src/v0/signer.rs (L2338-2368)
```rust
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
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L576-591)
```rust
                        // Update the idle timestamp for this signer
                        self.update_idle_timestamp(
                            signer_pubkey.clone(),
                            rejected_data.response_data.tenure_extend_timestamp,
                            signer_entry.weight,
                        );

                        // Update the read-count timestamp for this signer
                        self.update_read_count_timestamp(
                            signer_pubkey,
                            rejected_data
                                .response_data
                                .tenure_extend_read_count_timestamp,
                            signer_entry.weight,
                        );
                    }
```

**File:** stacks-signer/src/signerdb.rs (L1922-1971)
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

        // Check if a row exists for this sighash/signer combo
        let qry = "SELECT reject_code FROM block_rejection_signer_addrs WHERE signer_signature_hash = ?1 AND signer_addr = ?2 LIMIT 1";
        let args = params![block_sighash, addr.to_string()];
        let existing_code: Option<i64> =
            self.db.query_row(qry, args, |row| row.get(0)).optional()?;

        let reject_code = reject_reason as i64;

        match existing_code {
            Some(code) if code == reject_code => {
                // Row exists with same reject_reason, do nothing
                debug!("Duplicate block rejection.";
                    "signer_signature_hash" => %block_sighash,
                    "signer_address" => %addr,
                    "reject_reason" => ?reject_reason
                );
                Ok(false)
            }
            Some(_) => {
                // Row exists but with different reject_reason, update it
                let update_qry = "UPDATE block_rejection_signer_addrs SET reject_code = ?1 WHERE signer_signature_hash = ?2 AND signer_addr = ?3";
                let update_args = params![reject_code, block_sighash, addr.to_string()];
                self.db.execute(update_qry, update_args)?;
                debug!("Updated block rejection reason.";
                    "signer_signature_hash" => %block_sighash,
                    "signer_address" => %addr,
                    "reject_reason" => ?reject_reason
                );
                Ok(true)
            }
```
