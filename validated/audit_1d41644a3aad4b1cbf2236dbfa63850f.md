### Title
Signer response signatures do not cover `response_data`/`metadata`, allowing forged tenure-extension timestamps under another signer's identity — ([File: libsigner/src/v0/messages.rs])

### Summary
`BlockAccepted`/`BlockRejection` messages carry a signature that is computed and verified **only over the 32-byte `signer_signature_hash`**, never over the accompanying `response_data` (`tenure_extend_timestamp`, `tenure_extend_read_count_timestamp`, reject reason) or `metadata` (`server_version`). Because `(signer_signature_hash, signature)` pairs are broadcast in the clear on StackerDB, any party — including a single, non-majority signer or relay — can copy a legitimate signer's already-published signature and re-wrap it inside a new message with attacker-chosen `response_data`/`metadata`. Both the signer-side (`handle_block_signature`) and node-side (`stackerdb_listener.rs`) consumers authenticate the *sender identity* purely from the signature-over-hash, then blindly trust the unsigned fields riding alongside it. This is the same bug class as CVE-2017-16005 (`http-signature`): the signature covers only part of the message, so fields "outside" the signed scope can be freely rewritten while the signature still verifies.

### Finding Description
`create_block_acceptance` signs only the block's `signer_signature_hash`: [1](#0-0) 

The `BlockAccepted` struct bundles that hash+signature together with `metadata` and `response_data`, none of which are part of the signed preimage: [2](#0-1) 

When another signer processes an observed acceptance, it recovers the sender's public key **from the signature over the hash alone**, checks that the recovered address is in the signer set, and then unconditionally trusts the message's `response_data`/`metadata` (server_version, tenure_extend_timestamp, tenure_extend_read_count_timestamp) as if they were authenticated content from that signer: [3](#0-2) 

The node-side listener has the identical gap: it verifies the signature strictly against `block_sighash`, then reads `response_data.tenure_extend_timestamp` / `tenure_extend_read_count_timestamp` straight off the message without any binding to the signature: [4](#0-3) 

Since `(signer_signature_hash, signature)` for any accepted block is inherently public (every peer must see it to count votes / assemble the aggregate signature), an attacker with a single StackerDB signer slot (no majority, no other signer's private key) can:
1. Observe a legitimate `BlockAccepted{signer_signature_hash: H, signature: S, ...}` from victim signer V for block B.
2. Construct a new `BlockAccepted{signer_signature_hash: H, signature: S, metadata: <forged>, response_data: <forged tenure_extend_timestamp = u64::MAX, tenure_extend_read_count_timestamp = u64::MAX>}`.
3. Broadcast it in the attacker's own writable slot on the `.signers-X-BlockResponse` StackerDB contract.

Every consumer of this message — other signers via `handle_block_signature`, and the node via `stackerdb_listener.rs` — recovers pubkey `V` from `(H, S)`, confirms `V` is a valid signer, and accepts the forged `response_data` as if `V` had produced it, because nothing in the verification path re-derives or binds `response_data`/`metadata` to the signature.

### Impact Explanation
`tenure_extend_timestamp` / `tenure_extend_read_count_timestamp` govern when a miner's tenure may be extended (idle/read-count timeout logic on both the signer and node side). Forging these values under a victim signer's identity lets a single, non-majority attacker distort the aggregate tenure-extension deadline the state machine relies on (e.g., extending it far into the future, or otherwise skewing it away from what the "attributed" signer actually intended), without holding majority weight or the victim's key. This is a liveness/state-integrity wedge stemming directly from a cross-context-valid signature: a signature legitimately produced for hash `H` is replayed to authenticate unrelated, attacker-chosen payload data.

### Likelihood Explanation
High. The only prerequisite is possession of one signer's own writable StackerDB slot (no majority, no other signer's key, no auth token) and observation of any publicly broadcast `BlockAccepted`/`BlockRejection` message — which every signer必然 sees in the normal course of consensus. No malformed cryptography or race condition is required; it is a straightforward "copy-signature, mutate-unsigned-fields" forgery enabled purely by the incomplete signing scope.

### Recommendation
Extend the signed preimage for `BlockAccepted`/`BlockRejection` to include `response_data` (and ideally `metadata`), e.g. by hashing `(signer_signature_hash || response_data)` before signing/verifying, so that any change to the extension timestamps or reject reason invalidates the signature. Alternatively, bind `response_data` cryptographically to the same signature domain used for `signer_signature_hash` rather than treating it as free-form accompanying data.

### Proof of Concept
1. Signer `V` legitimately accepts block `B`, publishing `BlockAccepted{signer_signature_hash: H, signature: S, metadata: M0, response_data: {full_extend_ts: t0, read_count_extend_ts: t1}}` on `.signers-X-BlockResponse`.
2. Attacker (any other registered signer with one slot) observes `(H, S)` from the StackerDB gossip stream.
3. Attacker crafts and writes to their own slot: `BlockAccepted{signer_signature_hash: H, signature: S, metadata: M', response_data: {full_extend_ts: u64::MAX, read_count_extend_ts: u64::MAX}}`.
4. Peer signers processing this via `handle_block_signature` (`stacks-signer/src/v0/signer.rs:2371-2432`) recover pubkey from `(H, S)`, confirm it is `V`, and log/act on the forged `response_data` as `V`'s tenure-extend timestamps.
5. The node's `stackerdb_listener.rs` (`386-453`) independently performs the same signature check against `block_sighash` only and likewise consumes the forged `tenure_extend_timestamp`/`tenure_extend_read_count_timestamp` without any tie to `S`. [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** stacks-signer/src/v0/signer.rs (L474-497)
```rust
    pub fn create_block_acceptance(&self, block: &NakamotoBlock) -> BlockAccepted {
        let signature = self
            .private_key
            .sign(block.header.signer_signature_hash().bits())
            .expect("Failed to sign block");
        BlockAccepted::new(
            block.header.signer_signature_hash(),
            signature,
            self.signer_db.calculate_full_extend_timestamp(
                self.proposal_config
                    .tenure_idle_timeout
                    .saturating_add(self.proposal_config.tenure_idle_timeout_buffer),
                block,
                true,
            ),
            self.signer_db.calculate_read_count_extend_timestamp(
                self.proposal_config
                    .read_count_idle_timeout
                    .saturating_add(self.proposal_config.tenure_idle_timeout_buffer),
                block,
                true,
            ),
        )
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2371-2432)
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
```

**File:** libsigner/src/v0/messages.rs (L1657-1689)
```rust
pub struct BlockAccepted {
    /// The signer signature hash of the block that was accepted
    pub signer_signature_hash: Sha512Trunc256Sum,
    /// The signer's signature across the acceptance
    pub signature: MessageSignature,
    /// Signer message metadata
    pub metadata: SignerMessageMetadata,
    /// Extra versioned block response data
    pub response_data: BlockResponseData,
}

impl StacksMessageCodec for BlockAccepted {
    fn consensus_serialize<W: Write>(&self, fd: &mut W) -> Result<(), CodecError> {
        write_next(fd, &self.signer_signature_hash)?;
        write_next(fd, &self.signature)?;
        write_next(fd, &self.metadata)?;
        write_next(fd, &self.response_data)?;
        Ok(())
    }

    fn consensus_deserialize<R: Read>(fd: &mut R) -> Result<Self, CodecError> {
        let signer_signature_hash = read_next::<Sha512Trunc256Sum, _>(fd)?;
        let signature = read_next::<MessageSignature, _>(fd)?;
        let metadata = read_next::<SignerMessageMetadata, _>(fd)?;
        let response_data = read_next::<BlockResponseData, _>(fd)?;
        Ok(Self {
            signer_signature_hash,
            signature,
            metadata,
            response_data,
        })
    }
}
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L386-453)
```rust
                    SignerMessageV0::BlockResponse(BlockResponse::Accepted(accepted)) => {
                        let BlockAccepted {
                            signer_signature_hash: block_sighash,
                            signature,
                            metadata,
                            response_data,
                        } = accepted;
                        let tenure_extend_timestamp = response_data.tenure_extend_timestamp;
                        let read_count_extend_timestamp =
                            response_data.tenure_extend_read_count_timestamp;

                        let (lock, cvar) = &*self.blocks;
                        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");

                        let Some(block) = blocks.get_mut(&block_sighash) else {
                            info!(
                                "StackerDBListener: Received signature for block that we did not request. Ignoring.";
                                "signature" => %signature,
                                "signer_signature_hash" => %block_sighash,
                                "slot_id" => slot_id,
                                "signer_set" => self.signer_set,
                            );
                            continue;
                        };

                        let Ok(valid_sig) = signer_pubkey.verify(block_sighash.bits(), &signature)
                        else {
                            warn!(
                                "StackerDBListener: Got invalid signature from a signer. Ignoring."
                            );
                            continue;
                        };
                        if !valid_sig {
                            warn!(
                                "StackerDBListener: Processed signature but didn't validate over the expected block. Ignoring";
                                "signature" => %signature,
                                "signer_signature_hash" => %block_sighash,
                                "slot_id" => slot_id,
                            );
                            continue;
                        }

                        if Self::fault_injection_ignore_signatures() {
                            warn!("StackerDBListener: fault injection: ignoring well-formed signature for block";
                                "signer_signature_hash" => %block_sighash,
                                "signer_pubkey" => signer_pubkey.to_hex(),
                                "signer_slot_id" => slot_id,
                                "signature" => %signature,
                                "signer_weight" => signer_entry.weight,
                                "total_weight_approved" => block.total_weight_approved,
                                "percent_approved" => block.total_weight_approved as f64 / self.total_weight as f64 * 100.0,
                                "total_weight_rejected" => block.total_weight_rejected,
                                "percent_rejected" => block.total_weight_rejected as f64 / self.total_weight as f64 * 100.0,
                            );
                            continue;
                        }

                        if !block.gathered_signatures.contains_key(&slot_id) {
                            block.total_weight_approved = block
                                .total_weight_approved
                                .saturating_add(signer_entry.weight);

                            info!("StackerDBListener: Signature Added to block";
                                "signer_signature_hash" => %block_sighash,
                                "signer_pubkey" => signer_pubkey.to_hex(),
                                "signer_slot_id" => slot_id,
                                "signature" => %signature,
                                "signer_weight" => signer_entry.weight,
```
