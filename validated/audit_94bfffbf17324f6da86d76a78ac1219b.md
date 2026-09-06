### Title
`BlockRejection` signature does not bind to `reason_code`/`response_data`, allowing forged rejection reasons to be attributed to a legitimate signer - (File: `libsigner/src/v0/messages.rs`)

### Summary
`BlockRejection::hash()` in `libsigner/src/v0/messages.rs` computes the signed digest over only `signer_signature_hash` and `chain_id`, but the message also carries unsigned `reason`, `reason_code`, and `response_data` fields (including `failed_txid` and reject-reason detail) that downstream consumers treat as authenticated, signer-attributed data. This is the same class of bug as the external report: the code correctly authenticates *who* signed (msg.sender-equivalent: the recovered pubkey), but fails to bind that authentication to the actual content being acted upon (the vault's receiver/controller equivalent: the reject reason/txid payload). A gossip-relaying party can therefore take a genuine, validly-signed rejection from any signer and substitute a different `reason_code`/`response_data`/`failed_txid`, and the signature will still verify.

### Finding Description
`BlockRejection::hash()`:
```rust
pub fn hash(&self) -> Sha256Sum {
    let domain_tuple = make_structured_data_domain("block-rejection", "1.0.0", self.chain_id);
    let data = Value::buff_from(self.signer_signature_hash.as_bytes().into()).unwrap();
    structured_data_message_hash(data, domain_tuple)
}
``` [1](#0-0) 

Only `signer_signature_hash` (in the message body) and `chain_id` (in the domain) feed the hash that `sign()`/`verify()`/`recover_public_key()` operate over: [2](#0-1) 

Meanwhile, `reason`, `reason_code`, and `response_data` (which embeds `failed_txid` and the specific `RejectReason`/`ValidateRejectCode`) are struct fields of `BlockRejection` that are never included in the signed payload: [3](#0-2) 

These unauthenticated fields are treated as trustworthy, signer-attributed data on both the signer and node side:

- In `stacks-signer/src/v0/signer.rs::handle_block_rejection`, only the identity (`public_key`/`signer_address`) is verified against `is_valid_signer`; the `reject_reason` taken from `rejection.response_data.reject_reason` is stored and propagated as-is: [4](#0-3) 

- On the node side, `stackerdb_listener.rs` verifies the recovered pubkey matches the expected signer for the slot, then directly aggregates per-signer weight against `rejected_data.reason_code` and `rejected_data.response_data.failed_txid` to build up `total_weight`/`problematic_weight` for a transaction: [5](#0-4) 

Because none of `reason_code`/`response_data`/`failed_txid` are covered by the signature, a gossip-level attacker who observes any single valid `BlockRejection` from a signer (whatever the real reason for that signer's rejection was) can rewrite those fields — e.g., turn a `ConnectivityIssues` rejection into `RejectCode::ValidationFailed(ValidateRejectCode::ProblematicTransaction)` for an arbitrary `failed_txid` — and the message still passes signature verification and slot/pubkey checks, because those checks only authenticate `signer_signature_hash` + `chain_id`, not the tampered fields.

### Impact Explanation
This lets an attacker misattribute arbitrary rejection reasons/problematic-transaction votes to signers who never cast them, by replaying and mutating messages that were legitimately signed for a different purpose. Since the node accumulates `problematic_weight` per txid from these unauthenticated fields to decide whether a transaction should be treated as "problematic" (and thus excluded from future blocks), an attacker can forge enough weight to get a legitimate transaction censored/excluded from block templates network-wide without any of the claimed signers actually voting for it. This is a safety-relevant miscounting of signer responses (the "reason" a rejection is counted toward is forgeable independent of what the signer actually authorized), which can be used to manipulate the block-building/censorship logic that downstream tooling trusts as consensus-adjacent signer input.

### Likelihood Explanation
Any StackerDB gossip participant that can observe a genuine `BlockRejection` chunk from a target signer (which is broadcast openly to all participants of the signer set) can perform this attack — no majority of signers, no private key, and no auth token is required, matching the allowed "one-slot miner (plus gossip)" threat model. The only requirement is capturing one authentic (but arbitrary-reason) rejection from the targeted signer for the same `signer_signature_hash`/`chain_id`, which is routinely produced during normal signer operation.

### Recommendation
Include `reason_code`, `response_data` (and `reason`) in the structured-data hash that `BlockRejection::sign()`/`verify()`/`recover_public_key()` operate over, so the signature commits to the full semantic content of the rejection, not just the block hash and chain id. This should mirror how `BlockAccepted`/block-acceptance signatures are meant to authenticate the specific claim being made, not merely the block identifier.

### Proof of Concept
1. Capture any `SignerMessage::BlockResponse(BlockResponse::Rejected(rejection))` chunk broadcast by signer S for block sighash `H` on chain `C` (e.g., a mundane `RejectCode::ConnectivityIssues` rejection).
2. Recompute nothing — reuse `rejection.signature` unchanged, since it only signs over `(H, C)` per `BlockRejection::hash()` (`libsigner/src/v0/messages.rs:1802-1807`).
3. Replace `rejection.reason`, `rejection.reason_code` with `RejectCode::ValidationFailed(ValidateRejectCode::ProblematicTransaction)`, and set `rejection.response_data.failed_txid` to an arbitrary target txid.
4. Rebroadcast the mutated chunk under signer S's own StackerDB slot/signature (still valid, since verification only checks `recover_public_key(rejection) == S`'s pubkey against `(H, C)`).
5. `stackerdb_listener.rs` (`check_nakamoto_block_signer_signature`/rejection-handling path, lines 501-546) will attribute `signer_entry.weight` of S to `problematic_weight` for the forged `failed_txid`, contributing toward marking that transaction "problematic" even though S never rejected on that basis.

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

**File:** libsigner/src/v0/messages.rs (L1809-1838)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L2208-2264)
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
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L501-546)
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
