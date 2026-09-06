### Title
Signer block-acceptance signature has no domain separator (chain-id/version binding), enabling cross-context signature replay unlike the domain-bound rejection path - ([File: libsigner/src/v0/messages.rs])

### Summary
The signer's "accept" response for a block proposal is produced by signing the raw `signer_signature_hash` directly (a bare `ecrecover`-style ECDSA signature with no domain separator), while the sibling "reject" response explicitly wraps its payload in a SIP-018/EIP-712-style domain tuple that includes `chain_id`. This asymmetry reproduces exactly the bug class described in the external report: a signature that is not bound to a specific chain/domain can be replayed across any context (chain-id, network, or protocol) that shares the same underlying message bytes.

### Finding Description
`NakamotoBlockHeader::signer_signature_hash_inner` computes the message signers actually sign for block acceptance from header fields only — version, chain_length, burn_spent, consensus_hash, parent_block_id, tx_merkle_root, state_index_root, timestamp, miner_signature, pox_treatment, and (conditionally) problematic_txs: [1](#0-0) 

Notably absent from this preimage is any `chain_id` or network identifier. Contrast this with `BlockRejection::hash`, which is deliberately domain-bound via `make_structured_data_domain("block-rejection", "1.0.0", self.chain_id)` per SIP-018 (the same construction the external report recommends for EIP-712-style hashing): [2](#0-1) 

The acceptance path, however, has no such wrapper. `BlockAccepted`/`BlockResponse::accepted` is constructed directly from `signer_signature_hash` and a raw signature, with verification done via `Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(block_hash.bits(), signature)` — a bare digest recovery with no domain separator at all: [3](#0-2) [4](#0-3) 

The same bare-hash signing/verification is used at the chainstate level for the actual quorum check that gets a block accepted into the canonical chain: [5](#0-4) 

Because the signed digest for acceptance carries no `chain_id`/network tag, any signature a signer produces to *accept* a block is, in principle, a valid signature over that exact byte string on **any** other network/chain-id context that a) uses the same signer key and b) can produce (or has already produced) an identical header preimage. This is architecturally the same class of defect as the reported `BatcherPaymentService` issue: hashing "just the data" with `ecrecover` rather than binding the signature to a domain (chain id, contract, version) as EIP-712/SIP-018 requires. Note that the codebase already fixed this exact problem for rejections (`BlockRejection::hash`) but left the acceptance path (`BlockAccepted`/chainstate `verify_signer_signatures`) using the un-domain-bound raw hash.

### Impact Explanation
If a signer's key is ever reused across two chain contexts (e.g., mainnet/testnet, a subnet, or any Stacks-based deployment sharing the SIP18/secp256k1 stack and signer key material — a scenario the protocol itself does not prevent since `chain_id` is not part of the signed preimage), an acceptance signature captured in one context is a byte-for-byte valid `MessageSignature` for `verify_signer_signatures`/`StackerDBListener` in the other context wherever the header fields coincide. This directly matches the "Critical: a cross-context-valid signature" impact category — a signature that should only be meaningful in one chain context is unconditionally valid in another because no domain separator ties it to chain-id/network.

### Likelihood Explanation
Exploitability depends on an attacker/miner being able to produce (or already possessing) two block headers with byte-identical preimages across two chain contexts that share signer key material — most plausible where the same signer set/keys are deliberately or accidentally shared between a production network and a test/staging network with a lower-difficulty or attacker-controlled burnchain (allowing brute-forcing of `consensus_hash`/other miner-controlled fields to collide). This is a realistic but not always trivially cheap precondition, which is why the equivalent fix (domain separation) was already applied to `BlockRejection` proactively rather than reactively. The absence of chain-id binding on the acceptance path is nonetheless a genuine violation of the "signed vs validated equality" that should hold identically across all signed messages in this protocol.

### Recommendation
Apply the same SIP-018/EIP-712-style domain separation already used for `BlockRejection::hash` to the block-acceptance signing/verification path:
- Wrap `signer_signature_hash` in a `make_structured_data_domain("block-acceptance", "1.0.0", chain_id)` tuple (mirroring `BlockRejection::hash`) before signing/verifying in `BlockAccepted`.
- Correspondingly update `NakamotoBlockHeader::signer_signature_hash_inner`/`verify_signer_signatures` (or introduce an analogous domain-bound wrapper at the chainstate level) so that the canonical consensus-level signature check also binds to `chain_id`/network, closing the gap between the "signed" message and the "validated" message across chain contexts.

### Proof of Concept
1. Compute `H = signer_signature_hash()` for a `NakamotoBlockHeader` whose miner-controlled fields (`version`, `chain_length`, `burn_spent`, `parent_block_id`, `tx_merkle_root`, `state_index_root`, `timestamp`, `miner_signature`, `pox_treatment`) are fixed, and whose `consensus_hash` happens to coincide with a header on a second network sharing the same signer key set (e.g., a low-difficulty test/staging burnchain the attacker/miner controls).
2. Obtain (or induce) a signer's acceptance signature `sig = sign(H)` on network A (`BlockAccepted{signer_signature_hash: H, signature: sig, ...}`), as produced in `libsigner/src/v0/messages.rs` `BlockAccepted::new`.
3. Replay `(H, sig)` on network B: `Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(H.bits(), sig)` (used both in `stackslib/src/chainstate/nakamoto/mod.rs::verify_signer_signatures` and `stacks-signer/src/v0/signer.rs::handle_block_signature`) succeeds identically, since no `chain_id` is folded into `H`.
4. The recovered public key is accepted as a valid endorsement on network B even though the signer never intended to endorse a block in that chain context — contrasted with `BlockRejection::hash`, which would produce a different hash (and thus fail step 3) on network B because it explicitly folds in `self.chain_id`.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1026-1045)
```rust
    /// Inner calculation of the message digest for stackers to sign.
    /// This includes all fields _except_ the stacker signature.
    fn signer_signature_hash_inner(&self) -> Result<Sha512Trunc256Sum, CodecError> {
        let mut hasher = Sha512_256::new();
        let fd = &mut hasher;
        write_next(fd, &self.version)?;
        write_next(fd, &self.chain_length)?;
        write_next(fd, &self.burn_spent)?;
        write_next(fd, &self.consensus_hash)?;
        write_next(fd, &self.parent_block_id)?;
        write_next(fd, &self.tx_merkle_root)?;
        write_next(fd, &self.state_index_root)?;
        write_next(fd, &self.timestamp)?;
        write_next(fd, &self.miner_signature)?;
        write_next(fd, &self.pox_treatment)?;
        if Self::version_includes_problematic_txs(self.version) {
            write_next(fd, &self.problematic_txs)?;
        }
        Ok(Sha512Trunc256Sum::from_hasher(hasher))
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1096-1143)
```rust
    #[cfg_attr(test, mutants::skip)]
    pub fn verify_signer_signatures(
        &self,
        reward_set: &RewardSet,
        epoch_id: StacksEpochId,
    ) -> Result<u32, ChainstateError> {
        let message = self.signer_signature_hash();
        let Some(signers) = reward_set.signers() else {
            return Err(ChainstateError::InvalidStacksBlock(
                "No signers in the reward set".to_string(),
            ));
        };

        // if this is a shadow block, then its signing weight is as if every signer signed it, even
        // though the signature vector is undefined.
        if self.is_shadow_block() {
            return Ok(self.get_shadow_signer_weight(reward_set)?);
        }

        let mut total_weight_signed: u32 = 0;
        // `last_index` is used to prevent out-of-order signatures
        let mut last_index = None;
        // Before Epoch 4.0, signature order check contained a bug, so gate the
        // strict ordering behavior on the epoch.
        let strict_order = epoch_id.enforces_strict_signature_order();

        let total_weight = reward_set
            .total_signing_weight()
            .map_err(|_| ChainstateError::NoRegisteredSigners(0))?;

        // HashMap of <PublicKey, (Signer, Index)>
        let mut signers_by_pk: HashMap<_, _> = signers
            .iter()
            .enumerate()
            .map(|(i, signer)| (&signer.signing_key, (signer, i)))
            .collect();

        for signature in self.signer_signature.iter() {
            let public_key = Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(
                message.bits(),
                signature,
            )
            .map_err(|_| {
                ChainstateError::InvalidStacksBlock(format!(
                    "Unable to recover public key from signature {}",
                    signature.to_hex()
                ))
            })?;
```

**File:** libsigner/src/v0/messages.rs (L1370-1389)
```rust
impl BlockResponse {
    /// Create a new accepted BlockResponse for the provided block signer signature hash and signature
    pub fn accepted(
        signer_signature_hash: Sha512Trunc256Sum,
        signature: MessageSignature,
        full_extend_ts: u64,
        read_count_extend_ts: u64,
    ) -> Self {
        Self::Accepted(BlockAccepted {
            signer_signature_hash,
            signature,
            metadata: SignerMessageMetadata::default(),
            response_data: BlockResponseData::new(
                full_extend_ts,
                RejectReason::NotRejected,
                read_count_extend_ts,
                None,
            ),
        })
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

**File:** stacks-signer/src/v0/signer.rs (L2389-2399)
```rust
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
```
