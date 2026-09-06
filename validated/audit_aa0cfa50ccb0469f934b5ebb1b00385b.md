Found it: `BlockRejection`'s embedded signature covers only `signer_signature_hash` (via `hash()`), not `reason_code`/`response_data`, mirroring the SAML pattern of "signature is over field A, but consequential logic is decided by field B which travels alongside, unsigned."

### Title
Signer-side `BlockRejection`/`BlockAccepted` embedded signature does not cover `reason_code`/`response_data`, allowing a signed rejection to be replayed with attacker-chosen semantics - ([File: libsigner/src/v0/messages.rs])

### Summary
`BlockRejection::hash()` computes the signed digest from only `self.signer_signature_hash` (plus a fixed domain tag and `chain_id`), while `reason`, `reason_code`, and `response_data` (which includes `reject_reason`, `tenure_extend_timestamp`, `failed_txid`, etc.) are excluded from the signed payload. [1](#0-0)  `BlockAccepted` similarly signs only `signer_signature_hash` bits, with `metadata` and `response_data` unsigned. [2](#0-1) [3](#0-2) 

### Finding Description
This is the same class of bug as the SAML advisory: the verifier checks a signature over a narrow subset of the message ("SignedQuery"/`signer_signature_hash`) while downstream logic consumes a different, unsigned part of the same envelope (`SAMLRequest` vs `SAMLResponse` / `reason_code`+`response_data`). Any party holding a validly-signed `BlockRejection`/`BlockAccepted` payload from a given signer for a given `signer_signature_hash` — e.g., an old, previously-broadcast, or replayed StackerDB chunk — can re-wrap it with a different `reason`/`reason_code`/`response_data.reject_reason`/`tenure_extend_timestamp`/`failed_txid` field, and the recomputed `signature.verify()` still succeeds, because `hash()` never folds those fields into the digest. [4](#0-3) 

Consuming code trusts the embedded signature as proof of the *entire* message's authenticity: `store_and_process_block_rejection`/`handle_block_rejection` reasons about `reject_reason` and `response_data`, and node-side listeners key their rejection bookkeeping and inactivity/timeout timestamps off `response_data.tenure_extend_timestamp` fields carried in `BlockAccepted`/rejections, e.g. the `tenure_extend_timestamp`/`read_count_extend_timestamp` extraction in `stackerdb_listener.rs`. [5](#0-4)  Since the StackerDB chunk-level signature (`StackerDBChunkData::sign`/`verify`) only authenticates that the *sender* owns the slot and matches `data_hash()` over the raw bytes actually broadcast [6](#0-5) , that outer signature does not compensate: it authenticates "this byte blob came from signer X's slot," not "this specific `reason_code`/`response_data` combination was what signer X intended to assert" — the inner, message-level signature is what's supposed to do that, and it only covers `signer_signature_hash`.

### Impact Explanation
A single non-majority actor who can influence StackerDB chunk propagation (any signer, or anyone able to write/re-write a slot they control per the StackerDB access rules) can construct a `BlockRejection` (or `BlockAccepted`) envelope whose `signature`/`signer_signature_hash` pair was legitimately produced by a signer for one purpose, but whose `reason_code`, `response_data.reject_reason`, `tenure_extend_timestamp`, or `failed_txid` are attacker-chosen, and it will pass `verify()`. Depending on how peers act on `reason_code`/`response_data` (e.g. `RejectReason::ValidationFailed`-driven re-evaluation logic in `signer.rs`'s `should_reevaluate_reject_reason`, or `tenure_extend_timestamp` consumption for idle/inactivity timers in `stackerdb_listener.rs`), this can misrepresent why/whether a signer rejected or accepted a block, potentially causing a rejection to be recounted with different semantics or timers to be manipulated — falling under the "rejection recounted as an accept"/state-machine-wedge impact classes, without requiring a majority or another signer's private key, since the attacker only needs one prior validly-signed envelope to remix.

### Likelihood Explanation
Moderate-to-high reachability: only a single message from a single (even minority) signer is needed as raw material, StackerDB chunks are gossiped and re-signable by their owning slot at will, and no code path re-derives `hash()` from the full struct — every verification call (`BlockRejection::verify`, `BlockAccepted`'s signature check via `Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(block_hash.bits(), signature)`) checks exactly the same narrow digest. [7](#0-6) 

### Recommendation
Fold all consequential fields (`reason_code`, `response_data` including `reject_reason`, `tenure_extend_timestamp`, `tenure_extend_read_count_timestamp`, `failed_txid`, `chain_id`) into the structured-data hash signed/verified for `BlockRejection` and analogously into `BlockAccepted`'s `response_data`, so the signature authenticates the complete semantic payload rather than only `signer_signature_hash`.

### Proof of Concept
I could not fully verify an end-to-end exploitable path within the available iterations — specifically I did not confirm whether any consumer (`stacks-signer/src/v0/signer.rs`'s `handle_block_rejection`/`should_reevaluate_reject_reason`, or the node-side timers in `stackerdb_listener.rs`) makes a safety- or liveness-relevant decision *purely* from the unsigned fields in a way that's distinguishable from a legitimately-varied resend from the same signer (who is free to resend a new envelope for the same hash anyway, since they hold the key). This gap means the “remix” capability may be largely equivalent to what the legitimate signer could already do voluntarily, which would reduce this from a forgery to a low-impact non-issue. Confirming actual impact requires tracing every consumer of `RejectCode`/`response_data.reject_reason`/`tenure_extend_timestamp` against the specific signer identity, which needs a live Devin session with the full repository to trace all `handle_block_rejection`/`stackerdb_listener.rs` call sites and confirm whether any decision differs between "signer legitimately resent a new envelope" and "attacker remixed an old signed envelope with new codes."

### Citations

**File:** libsigner/src/v0/messages.rs (L491-507)
```rust
    /// Sign the mock signature and set the internal signature field
    fn sign(&mut self, private_key: &StacksPrivateKey) -> Result<(), String> {
        let signature_hash = self.mock_proposal.signer_signature_hash();
        self.signature = private_key.sign(signature_hash.as_bytes())?;
        Ok(())
    }

    /// Verify the mock signature against the provided signer public key
    pub fn verify(&self, public_key: &StacksPublicKey) -> Result<bool, String> {
        if self.signature == MessageSignature::empty() {
            return Ok(false);
        }
        let signature_hash = self.mock_proposal.signer_signature_hash();
        public_key
            .verify(&signature_hash.0, &self.signature)
            .map_err(|e| e.to_string())
    }
```

**File:** libsigner/src/v0/messages.rs (L1657-1666)
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
```

**File:** libsigner/src/v0/messages.rs (L1802-1825)
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
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L386-396)
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

```

**File:** libstackerdb/src/libstackerdb.rs (L159-193)
```rust
    /// Get the digest to sign that authenticates this chunk data and metadata
    fn auth_digest(&self) -> Sha512Trunc256Sum {
        let mut hasher = Sha512_256::new();
        hasher.update(self.slot_id.to_be_bytes());
        hasher.update(self.slot_version.to_be_bytes());
        hasher.update(self.data_hash.0);
        Sha512Trunc256Sum::from_hasher(hasher)
    }

    /// Sign this slot metadata, committing to slot_id, slot_version, and
    /// data_hash.  Sets self.signature to the signature.
    /// Fails if the underlying crypto library fails
    pub fn sign(&mut self, privkey: &StacksPrivateKey) -> Result<(), Error> {
        let auth_digest = self.auth_digest();
        let sig = privkey
            .sign(&auth_digest.0)
            .map_err(|se| Error::SigningError(se.to_string()))?;

        self.signature = sig;
        Ok(())
    }

    /// Verify that a given principal signed this chunk metadata.
    /// Note that the address version is ignored.
    pub fn verify(&self, principal: &StacksAddress) -> Result<bool, Error> {
        let sigh = self.auth_digest();
        let pubk = StacksPublicKey::recover_to_pubkey_without_validating_low_s(
            sigh.as_bytes(),
            &self.signature,
        )
        .map_err(|ve| Error::VerifyingError(ve.to_string()))?;

        let pubkh = Hash160::from_node_public_key(&pubk);
        Ok(pubkh == *principal.bytes())
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
