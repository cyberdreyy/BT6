## Finding

The signer's inner `BlockResponse` authentication only binds the ECDSA signature to the block's `signer_signature_hash` (plus a domain tag) and never to the accompanying `reason_code`/`response_data`/`metadata`, and the receiving signer never checks that the recovered address matches the StackerDB slot the message physically arrived on. This lets any single signer relay another signer's genuine signature while substituting the unauthenticated payload — the exact "rogue extension negotiation" bug class from the AsyncSSH report (an authenticated token used to smuggle unauthenticated content).

### Title
Unsigned `reason_code`/`response_data`/`metadata` in `BlockResponse` + missing sender-binding lets any signer forge another signer's rejection/acceptance details - (File: `libsigner/src/v0/messages.rs`, `stacks-signer/src/v0/signer.rs`)

### Summary
`BlockRejection::hash()` commits only to `signer_signature_hash` and `chain_id`, never to `reason`, `reason_code`, or `response_data` <cite repo="EzraCole/stacks-core--017" path="libsigner/src/v0/messages.rs" start="1802="1802" end="1814" />. `verify()`/`recover_public_key()` re-derive that same narrow hash [1](#0-0) . `BlockAccepted`'s signature is likewise only over the block hash, not `metadata`/`response_data` [2](#0-1) .

On the signer side, `handle_event_match` validates that the *sender's slot* corresponds to a legitimate signer, then dispatches `BlockResponse` without ever passing that verified sender identity down: [3](#0-2) . Both `handle_block_rejection` and `handle_block_signature` re-derive the signer's address purely from the embedded, narrowly-scoped signature via `recover_public_key()`/`recover_to_pubkey_without_validating_low_s`, and only check that this recovered address is *some* valid signer — never that it matches the slot the message came from: [4](#0-3) [5](#0-4) .

By contrast, the node-side `StackerDBListener` performs the correct check for acceptances (`signer_pubkey.verify(...)` against the *known slot's* key) and for rejections (`if rejected_pubkey != signer_pubkey { continue; }`), enforcing that the recovered identity matches the transmitting slot [6](#0-5) . That binding is absent in the signer-to-signer path.

### Finding Description
Because the cryptographic commitment is only `(signer_signature_hash, chain_id)` for rejections and only the block hash for acceptances, `reason`, `reason_code`, `response_data` (including `failed_txid`, `tenure_extend_timestamp`, `read_count_extend_timestamp`) and `metadata` travel as unauthenticated companions to a valid signature — analogous to the AsyncSSH extension-info message being injected alongside an already-negotiated but unauthenticated channel. Combined with the missing slot-to-recovered-address binding in `handle_block_rejection`/`handle_block_signature`, any single signer who has ever observed a peer's genuine `BlockRejection`/`BlockAccepted` for a given block (trivial — it is broadcast to the whole set) can:

1. Keep the peer's untouched `(signer_signature_hash, signature)` pair, which still recovers to the peer's real address and still passes `verify()`/`is_valid_signer()`.
2. Replace `reason`, `reason_code`, and `response_data` with arbitrary attacker-chosen values (e.g. `RejectCode::ValidationFailed(ValidateRejectCode::ProblematicTransaction)` with a `failed_txid` pointing at a victim transaction, or a different `RejectReason` altogether).
3. Wrap the forged `BlockResponse` in `SignerMessage`, sign the outer StackerDB chunk with their own key, and put it in their own slot.
4. Every recipient's `handle_block_rejection` accepts the forgery as an authentic statement from the impersonated peer, because only the signature-over-hash is checked, not the metadata, and not slot provenance.

This is a cross-context-valid-signature style flaw: a signature valid for "peer P rejected block H" is treated as also authenticating an arbitrary attacker-chosen `reason_code`/`response_data` that P never produced.

### Impact Explanation
The forged `reason_code`/`response_data` feed directly into consensus-relevant bookkeeping:
- `store_and_process_block_rejection` converts the (forgeable) `reason_code` into a `RejectReasonPrefix` used by `add_block_rejection_signer_addr` for weight bucketing and downstream `should_reevaluate_reject_reason` decisions.
- The node's `StackerDBListener` accumulates per-`failed_txid` weight and a `problematic_weight` counter straight from `rejected_data.response_data.failed_txid`/`reason_code` [7](#0-6) , letting an attacker falsely attribute "problematic transaction" votes to honest, uninvolved signers and skew that tally without their participation or knowledge — a miscounted/mis-attributed response, matching the required "rejection recounted"/cross-context-signature impact class.
- For acceptances, forged `tenure_extend_timestamp`/`read_count_extend_timestamp` attributed to a peer distort `update_idle_timestamp`/`update_read_count_timestamp` weight-weighted extension calculations, which can bias liveness-relevant tenure-extension decisions.

No majority, no other signer's private key, and no StackerDB transport compromise is required — only a single attacker-controlled slot and an already-public broadcast to copy from.

### Likelihood Explanation
Any signer's own genuine `BlockRejection`/`BlockAccepted` broadcasts (needed as raw material) are, by design, sent to the entire signer set over StackerDB, so the prerequisite "observe one signature" is met on essentially every rejected/accepted block. Constructing the forged struct and re-signing the outer StackerDB chunk with the attacker's own key requires no cryptographic breaks — only standard struct construction and use of the attacker's own signing key, which is well within reach of a single misbehaving signer.

### Recommendation
- Extend the structured-data hash committed to by `BlockRejection`/`BlockAccepted` signatures to cover `reason_code`, `response_data`, and `metadata` (not just `signer_signature_hash`/block hash and `chain_id`), so tampering with any field invalidates the signature.
- In `handle_block_rejection` and `handle_block_signature`, thread through and check that the address recovered from the inner signature matches the `signer_public_key` of the StackerDB slot that delivered the message (mirroring the check already present in `stacks-node/src/nakamoto_node/stackerdb_listener.rs`), rejecting mismatches outright.

### Proof of Concept
1. Signer A legitimately rejects block `H` with `reason_code = ConnectivityIssues`, producing `BlockRejection { signer_signature_hash: H, signature: sigA, reason_code: ConnectivityIssues, response_data: {...} }`, broadcast to all signers via StackerDB.
2. Attacker (any other signer, weight 1) reads this broadcast and extracts `(H, sigA)`.
3. Attacker builds `forged = BlockRejection { signer_signature_hash: H, signature: sigA (unchanged), reason: "problematic tx", reason_code: RejectCode::ValidationFailed(ValidateRejectCode::ProblematicTransaction), response_data: BlockResponseData { failed_txid: Some(victim_txid), .. } }`.
4. `forged.verify()`/`forged.recover_public_key()` still succeed and recover Signer A's address, per `hash()` only covering `signer_signature_hash`/`chain_id` [8](#0-7) .
5. Attacker wraps `forged` in `SignerMessage::BlockResponse(BlockResponse::Rejected(forged))`, signs the StackerDB chunk with their own key, and writes it to their own slot.
6. Every peer's `handle_block_rejection` recovers Signer A's address, passes `is_valid_signer`, and records that Signer A flagged `victim_txid` as problematic [4](#0-3)  — a statement Signer A never made — and the node-side listener accumulates `problematic_weight` against `victim_txid` accordingly [7](#0-6) .

### Citations

**File:** libsigner/src/v0/messages.rs (L1691-1711)
```rust
impl BlockAccepted {
    /// Create a new BlockAccepted for the provided block signer signature hash and signature
    pub fn new(
        signer_signature_hash: Sha512Trunc256Sum,
        signature: MessageSignature,
        full_extend_ts: u64,
        read_count_extend_ts: u64,
    ) -> Self {
        Self {
            signer_signature_hash,
            signature,
            metadata: SignerMessageMetadata::default(),
            response_data: BlockResponseData::new(
                full_extend_ts,
                RejectReason::NotRejected,
                read_count_extend_ts,
                None,
            ),
        }
    }
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

**File:** stacks-signer/src/v0/signer.rs (L539-550)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L2216-2238)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L2389-2411)
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
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L501-513)
```rust
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
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L520-543)
```rust
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
