### Title
Unsigned `reason`/`reason_code`/`response_data` fields in `BlockRejection` and `BlockAccepted` allow a cross-context-valid signature to be replayed with forged rejection reasons - (File: `libsigner/src/v0/messages.rs`)

### Summary
`BlockRejection::hash()` and the message that `BlockAccepted` signs commit only to the `signer_signature_hash` field. Every other field in the message — `reason`, `reason_code`, and the entire `response_data` (which carries the versioned `RejectReason`/`failed_txid`/tenure-extend timestamps) — is left outside the signed payload. This is the same bug class as the reported "prettyPrint" issue: content that materially changes what a recipient believes and acts on is not part of what the signature actually authenticates, so it can be substituted while the signature still verifies.

### Finding Description
`BlockRejection::hash()` builds the signed digest from only the block hash: [1](#0-0) 

`verify()`/`recover_public_key()` recompute this same narrow digest, so a `BlockRejection` with identical `signer_signature_hash` and `signature` verifies successfully **regardless of what `reason`, `reason_code`, or `response_data.reject_reason`/`failed_txid` say**: [2](#0-1) 

Likewise, `BlockAccepted`'s signature is recovered purely from `block_hash.bits()`, never touching `response_data` (tenure/read-count extend timestamps): [3](#0-2) 

The un-signed `reject_reason` is exactly what drives a safety-relevant decision downstream: `store_and_process_block_rejection` tallies `RejectReasonPrefix::ReorgNotAllowed` weight specifically to flip the miner's status to invalid once 30% weight is reached: [4](#0-3) 

and the miner-side coordinator likewise keys its handling of `failed_txid`/`reason_code` (temporarily/permanently excluding transactions) off these same unauthenticated fields: [5](#0-4) 

Because the cryptographic commitment covers only `signer_signature_hash`, a genuinely-signed rejection for a given block can be re-delivered with a different `reason_code`/`response_data` (e.g. swapping a benign `InvalidBitvec` rejection into `ReorgNotAllowed`, or vice-versa) and every consumer that calls `.verify()`/`.recover_public_key()` will accept it as an authentic statement from that signer — this is a cross-context-valid signature: the same `(signer_signature_hash, signature)` pair is valid for arbitrarily different accompanying "reasons".

### Impact Explanation
This breaks the equality between "what the signer actually decided/signed" and "what other signers/the miner record as having been decided":
- `ReorgNotAllowed` weight is the sole trigger (pre-global-state versions) for marking a miner `InvalidatedBeforeFirstBlock` at only 30% weight — inflating or suppressing this tally via a forged reason on an otherwise-valid signature can wrongly invalidate a legitimate miner (liveness wedge) or hide a real reorg-rejection signal that should have invalidated a malicious miner (safety break).
- `failed_txid`/`reason_code` drive the miner's temporary/permanent transaction-exclusion logic in `signer_coordinator.rs`/`stackerdb_listener.rs`, so forged reason codes can manipulate which transactions get excluded from future blocks.
- For `BlockAccepted`, the unsigned `tenure_extend_timestamp`/`read_count_extend_timestamp` gate idle/read-count timeout extension, so they can be manipulated independently of the signer's actual signature intent.

This falls under the "cross-context-valid signature" and "signer wedged/acting on incorrect state" impact categories.

### Likelihood Explanation
Exploitation requires only the ability to re-deliver or re-serve a previously-observed, validly-signed `BlockRejection`/`BlockAccepted` chunk with substituted non-signed fields — no majority of signers, no other signer's private key, and no auth token are needed. Any single relaying party in the message's path (a gossip peer or a node re-serving stored StackerDB content) can perform the substitution and still pass every check in `handle_block_rejection`/`handle_block_signature`.

### Recommendation
Include `reason`, `reason_code`, and the full `response_data` (or at minimum a hash of them) in the structured data that `BlockRejection::hash()` signs, and extend `BlockAccepted`'s signed message to cover `response_data` as well, so the signature authenticates the complete decision content rather than only the block hash.

### Proof of Concept
1. Signer S validly rejects block B with `reason_code = ValidationFailed(InvalidBitvec)` and signs it, producing `(signer_signature_hash=H, signature=Sig)`.
2. An intermediary reconstructs a `BlockRejection` with the same `H`/`Sig` but `reason_code = ReorgNotAllowed` (or any other `RejectReasonPrefix`) and `response_data.reject_reason` changed accordingly, per [6](#0-5) .
3. Any receiving signer's `handle_block_rejection` calls `rejection.recover_public_key()`/is later validated only via `hash()` (which ignores the changed fields), so the forged message is accepted as authentically from S and its `ReorgNotAllowed` weight is counted toward the 30% miner-invalidation threshold in `store_and_process_block_rejection`, per [7](#0-6) .

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

**File:** libsigner/src/v0/messages.rs (L1802-1807)
```rust
    /// The signature hash for the block rejection
    pub fn hash(&self) -> Sha256Sum {
        let domain_tuple = make_structured_data_domain("block-rejection", "1.0.0", self.chain_id);
        let data = Value::buff_from(self.signer_signature_hash.as_bytes().into()).unwrap();
        structured_data_message_hash(data, domain_tuple)
    }
```

**File:** libsigner/src/v0/messages.rs (L1816-1838)
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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-546)
```rust
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
