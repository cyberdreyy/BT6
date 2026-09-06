### Title
BlockAccepted/BlockRejection signature does not cover `response_data` (tenure/read-count extend timestamps, reject reason, failed txid) — unsigned mutable fields let any relay alter accept/reject semantics without invalidating the signature - ([File: libsigner/src/v0/messages.rs])

### Summary
`BlockAccepted::sign`/`verify` and `BlockRejection::hash`/`sign`/`verify` only ever hash the `signer_signature_hash` field (and, for rejections, the `chain_id` used solely as a domain separator). None of the other payload fields — `response_data.tenure_extend_timestamp`, `response_data.tenure_extend_read_count_timestamp`, `response_data.reject_reason`, `response_data.failed_txid`, `reason`, `reason_code` — are part of the signed digest, yet the node (`stackerdb_listener.rs`) and other signers trust these unsigned fields as if they came verbatim from the signer whose signature was checked.

### Finding Description
`BlockAccepted::new`/`sign` computes: [1](#0-0) 
i.e. `signature_hash = mock_proposal.signer_signature_hash()` pattern is mirrored for `BlockAccepted` — the signature is over the block's `signer_signature_hash` alone, with `response_data` (containing `tenure_extend_timestamp`, `tenure_extend_read_count_timestamp`, `reject_reason`, `failed_txid`) completely excluded from the signed bytes: [2](#0-1) [3](#0-2) 

Similarly, `BlockRejection::hash()` signs only `self.signer_signature_hash` (with `chain_id` used merely as the domain separator of the structured-data hash), leaving `reason`, `reason_code`, and `response_data` unauthenticated: [4](#0-3) 

Because `verify()`/`recover_public_key()` recompute the digest the exact same (narrow) way, a message whose `response_data`/`reason`/`reason_code` bytes have been altered in transit (e.g., by a malicious or compromised StackerDB relay/gossip participant, or by the "one-slot miner" acting as a man-in-the-middle on the .signers contract if it can also write/replay chunks) will still pass `verify()` even though its semantic content — the actual accept/reject reason and the tenure-extension timing hints — no longer matches what the signer actually attested to.

Downstream consumers treat these unsigned fields as authoritative for both liveness-relevant node behavior and signer state-machine decisions:
- The node's `stackerdb_listener.rs` unconditionally feeds the (unsigned) `tenure_extend_timestamp` / `read_count_extend_timestamp` into `update_idle_timestamp`/`update_read_count_timestamp`, which are weighted by the signer's stacking weight and used to decide tenure extension: [5](#0-4) [6](#0-5) 
Nowhere in this handler is `response_data` cross-checked against a signed value — only the block-hash signature is verified (lines 411-426), then the *unsigned* `tenure_extend_timestamp`/`read_count_extend_timestamp` extracted at lines 393-395 are consumed with full trust.
- On the signer side, `handle_block_rejection` uses the unsigned `rejection.response_data.reject_reason` to classify the rejection (e.g., "recoverable/re-evaluable" vs. terminal `RejectedInPriorRound`), which drives the signer's local state machine: [7](#0-6) 
Only `rejection.signer_signature_hash` and the recovered public key are authenticated (lines 2216-2238); the `reject_reason` classification that is stored and acted upon is taken from the unsigned `response_data`.

This is the direct structural analog of the go-mail flaw: a value that downstream logic treats as if it were covered/validated by a cryptographic or protocol guarantee (there, the escaped mail address; here, the accept/reject "reason"/extension-timestamp metadata) is in fact carried in a raw, unauthenticated side-channel of the same message, so tampering with that side-channel changes behavior without breaking the check that is actually performed.

### Impact Explanation
An attacker who can modify in-flight `SignerMessage::BlockResponse` bytes on the StackerDB gossip path (a role reachable by a relaying party, which the analog rules classify alongside "gossip") can:
- Rewrite a legitimate signer's `BlockAccepted.response_data.tenure_extend_timestamp`/`read_count_extend_timestamp` to arbitrary values while the signature still verifies, corrupting the node's weighted tenure-extension calculus — a liveness-affecting wedge (miners could be tricked into extending or not extending tenures against the real intent of the signer set).
- Rewrite a legitimate signer's `BlockRejection.reason_code`/`response_data.reject_reason` (e.g., turning a terminal `RejectedInPriorRound` into a re-evaluable `ValidationFailed(NotFoundError)`, or vice versa) while the signature still verifies, causing another signer's local state machine to mis-classify the rejection and potentially re-process/accept a block it should have permanently rejected, or refuse to re-evaluate a block it should retry — directly touching the "rejection recounted as an accept"/wedge impact category.

Because the signature check (`verify()`/`recover_public_key()`) succeeds regardless of this tampering, no participant currently has a way to detect that the reason/timestamp payload has been altered.

### Likelihood Explanation
Exploitation requires only the ability to intercept/modify a StackerDB chunk in transit or to act as a relaying party before it reaches its final consumers (node or other signers) — it does not require a majority of signers, another signer's private key, or the auth token, satisfying the in-scope threat model of "a one-slot miner (plus gossip)". The bug is structural (present in every `BlockAccepted`/`BlockRejection` message), so any relay with write/observe access to the `.signers-*` StackerDB slots can trigger it.

### Recommendation
Extend the signed digest for both `BlockAccepted` and `BlockRejection` to cover the full semantic payload that downstream logic relies on — at minimum `response_data` (all fields: `tenure_extend_timestamp`, `tenure_extend_read_count_timestamp`, `reject_reason`, `failed_txid`) and, for rejections, `reason_code` (and ideally `reason`). Use a canonical, unambiguous serialization (e.g., include the full `consensus_serialize` bytes of `response_data` in the structured-data hash, or use a domain-tuple that folds in every semantically-relevant field) so that any post-signing modification of these fields is detected by `verify()`.

### Proof of Concept
1. Signer S produces a valid `BlockAccepted { signer_signature_hash: H, signature: Sig, response_data: { tenure_extend_timestamp: T1, ... } }` and signs it — `Sig` is computed only from `H` per `sign()`/`verify()` at `libsigner/src/v0/messages.rs:491-506`.
2. A relaying party intercepts the StackerDB chunk before it reaches the node's `stackerdb_listener` and rewrites `response_data.tenure_extend_timestamp` to `T2` (any attacker-chosen value), leaving `signer_signature_hash` and `signature` untouched.
3. The node's `stackerdb_listener.rs` handler verifies `signer_pubkey.verify(block_sighash.bits(), &signature)` successfully (lines 411-417), then extracts the tampered `tenure_extend_timestamp` from `response_data` (lines 393-395) and feeds it into `update_idle_timestamp` (lines 472-477) as if it were an authentic value from signer S — the check never touches `response_data`.
4. Equivalently, replace a `BlockRejection`'s `reason_code`/`response_data.reject_reason` in transit; `BlockRejection::verify()` (lines 1816-1825) still returns `true` because it only recomputes `hash()` over `signer_signature_hash`, so `handle_block_rejection` in `stacks-signer/src/v0/signer.rs:2208-2265` processes the forged reason classification as genuine.

### Citations

**File:** libsigner/src/v0/messages.rs (L491-496)
```rust
    /// Sign the mock signature and set the internal signature field
    fn sign(&mut self, private_key: &StacksPrivateKey) -> Result<(), String> {
        let signature_hash = self.mock_proposal.signer_signature_hash();
        self.signature = private_key.sign(signature_hash.as_bytes())?;
        Ok(())
    }
```

**File:** libsigner/src/v0/messages.rs (L1655-1666)
```rust
/// A rejection response from a signer for a proposed block
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L386-418)
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
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L472-484)
```rust
                        // Update the idle timestamp for this signer
                        self.update_idle_timestamp(
                            signer_pubkey.clone(),
                            tenure_extend_timestamp,
                            signer_entry.weight,
                        );

                        // Update the read-count timestamp for this signer
                        self.update_read_count_timestamp(
                            signer_pubkey,
                            read_count_extend_timestamp,
                            signer_entry.weight,
                        );
```

**File:** stacks-signer/src/v0/signer.rs (L2251-2265)
```rust
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
