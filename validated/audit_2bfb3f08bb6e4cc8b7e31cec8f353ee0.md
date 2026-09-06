### Title
BlockRejection/BlockAccepted signature covers only `signer_signature_hash`, letting `reason`/`reason_code`/`response_data` be smuggled unsigned - ([File: libsigner/src/v0/messages.rs])

### Summary
`BlockRejection::hash()` and the signature covering `BlockAccepted` bind the signer's Secp256k1 signature only to the target block's `signer_signature_hash`. All the other fields that drive protocol decisions — `reason`, `reason_code` (and the whole `RejectReason`), `response_data` (which carries `RejectReason`, `tenure_extend_timestamp`, `read_count_extend_timestamp`, `failed_txid`), and `metadata` — are transmitted alongside the signature but are **not** part of the signed payload. This is structurally the same class of bug as the SAML "attribute smuggling" advisory: a validly signed assertion (here, "signer X signed over hash H") is trusted, while unsigned attributes riding along with it (here, the rejection reason/response data) are consumed as if they were equally authenticated.

### Finding Description
`BlockRejection::hash()`:
```rust
pub fn hash(&self) -> Sha256Sum {
    let domain_tuple = make_structured_data_domain("block-rejection", "1.0.0", self.chain_id);
    let data = Value::buff_from(self.signer_signature_hash.as_bytes().into()).unwrap();
    structured_data_message_hash(data, domain_tuple)
}
``` [1](#0-0) 

only the `signer_signature_hash` (32 bytes identifying which block) plus `chain_id` (as domain) is signed. `reason`, `reason_code`, `response_data` (including `RejectReason`, `failed_txid`, timestamps) are excluded from `hash()` and thus from `verify()`/`recover_public_key()`: [2](#0-1) [3](#0-2) 

The consumer sides trust these unsigned fields for consensus-relevant decisions without any re-derivation from the signature:

- In `stacks-signer/src/v0/signer.rs::handle_block_rejection`, after recovering/authenticating only the pubkey (which authenticates the signer identity and the hash, not the reason), the code stores and acts on `(&rejection.response_data.reject_reason).into()`: [4](#0-3) 
This reason feeds `store_and_process_block_rejection`, which uses `RejectReasonPrefix::ReorgNotAllowed` weight to decide whether to mark the current miner invalid: [5](#0-4) 

- On the node side, `stackerdb_listener.rs` accumulates `failed_txid`/`reason_code` from `rejected_data.response_data` and `rejected_data.reason_code` to build lists of transactions to permanently/temporarily exclude from future block templates, after validating only that the signature recovers to the claimed signer: [6](#0-5) 


Because `reason`, `reason_code`, and `response_data` sit outside the signed hash, a network relay or the miner's own StackerDB fan-out — which the miner controls as the message's transport (in scope: "a one-slot miner (plus gossip)") — can take a signer's genuinely signed rejection for block H and swap its `reason_code`/`response_data`/`reason` string to a different value while the signature (over `signer_signature_hash` only) still verifies successfully. This lets an attacker relabel a signer's real rejection as, e.g., a different reject-reason class or forge/omit a `failed_txid`, or (for `BlockAccepted`, whose `metadata`/`response_data` are also outside the signed portion if the accept-hash follows the same narrow-hash pattern) tamper with `tenure_extend_timestamp`/`read_count_extend_timestamp` used for idle-timeout bookkeeping in `update_idle_timestamp`/`update_read_count_timestamp` — all without invalidating the cryptographic signature check.

### Impact Explanation
This does not let an attacker forge a fresh signature or flip Accepted↔Rejected (the *kind* of message and `signer_signature_hash` binding is enforced elsewhere by codec framing and message type). But it does let a party controlling message relay smuggle attacker-chosen `reason_code`/`response_data` values into an otherwise-authentic, signature-verified rejection. Concretely:
- It can bias the `ReorgNotAllowed` weight tally used to flip `SortitionMinerStatus::InvalidatedBeforeFirstBlock` [7](#0-6) , i.e. a rejection recounted under a different reason bucket than the signer actually meant, corrupting the "rejection recounted as X" invariant for miner-invalidation logic.
- It can poison the node's `failed_txids` bookkeeping used to permanently/temporarily ban transactions from future proposals , without any signer having actually voted that way.

This falls short of the "Critical" bar (no invalid/non-canonical block gets signed, no cross-context signature reuse, and the core Accepted/Rejected weight tally — which is keyed off signature recovery per slot, not off the mutable fields — is unaffected), so I assess it as a **narrower integrity gap** rather than a full safety break as defined by the rules (a signer signing an invalid block, a rejection recounted as an acceptance, or a wedge). The primary consensus quantities (approve/reject weight totals) key off the *signature* and *slot*, not off the unsigned fields, so the block accept/reject outcome itself is not directly forgeable this way.

### Likelihood Explanation
Reachable by a one-slot participant that controls or can influence the StackerDB relay/gossip path for its own message (the message it signed is genuine; only the unsigned trailer is mutated in transit or re-published). No majority collusion, no other signer's key, and no auth_token/local access are required — only the ability to alter bytes of an already-signed StackerDB chunk it authored (or that transits through it) before final consumers parse it. This matches the "one-slot miner plus gossip" threat model in scope.

### Recommendation
Include `reason_code`, `response_data` (and `metadata` fields that affect protocol logic, e.g. `tenure_extend_timestamp`/`read_count_extend_timestamp`/`failed_txid`) inside the structured-data hash that is signed for both `BlockRejection` and `BlockAccepted`, so that `verify()`/`recover_public_key()` fail if any of these fields are altered after signing.

### Proof of Concept
Conceptual (no execution environment available):
1. Signer S validates a proposal and genuinely rejects it with `RejectReason::SortitionViewMismatch`, producing `BlockRejection { reason_code, response_data, signer_signature_hash, signature, chain_id, metadata }`, where `signature` is computed only over `signer_signature_hash` via `BlockRejection::hash()`.
2. A relay (or the miner delivering StackerDB chunks) intercepts the serialized chunk before other signers/the node consume it and rewrites `reason`, `reason_code`, and `response_data.reject_reason` to `RejectReason::ReorgNotAllowed` (or injects a `failed_txid`), leaving `signer_signature_hash` and `signature` untouched.
3. `handle_block_rejection`/`stackerdb_listener` call `recover_public_key()`/signature verification, which only checks the hash of `signer_signature_hash` + `chain_id`, so verification succeeds despite the tampered reason fields [3](#0-2) .
4. The tampered `reject_reason`/`failed_txid` is now recorded as if S had actually voted that way, feeding into `store_and_process_block_rejection`'s `ReorgNotAllowed` weight tally [7](#0-6)  and the node's `failed_txids` exclusion bookkeeping .

I was unable to inspect the exact hash-construction function for `BlockAccepted`'s signature (analogous to `BlockRejection::hash()`) within the available index snippets, so I cannot fully confirm whether `metadata`/`response_data` are excluded from the `BlockAccepted` signature in the same way; this should be verified directly in `libsigner/src/v0/messages.rs` in a full checkout.

### Citations

**File:** libsigner/src/v0/messages.rs (L1715-1730)
```rust
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
