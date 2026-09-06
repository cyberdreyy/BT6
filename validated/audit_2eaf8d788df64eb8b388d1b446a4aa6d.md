### Title
`BlockRejection` Signatures Do Not Bind `reason_code`/`response_data`/`metadata`, Enabling Cross-Context Signature Reuse - (File: `libsigner/src/v0/messages.rs`)

### Summary
`BlockRejection::hash()` computes the signed digest from only the `signer_signature_hash` and `chain_id`, while `verify()`/`recover_public_key()` check the signature against that same narrow digest. However, both signer-side (`stacks-signer/src/v0/signer.rs`) and node-side (`stacks-node/src/nakamoto_node/stackerdb_listener.rs`) consensus logic treat `reason_code`, `response_data` (e.g. `failed_txid`, `tenure_extend_timestamp`), and `metadata` as if they were authenticated parts of the signer's statement — using them to decide per-txid exclusion weight and to flip a miner to `InvalidatedBeforeFirstBlock` on a 30% `ReorgNotAllowed` threshold. Because these fields sit outside the signed digest, the same valid signature bytes remain valid for a `BlockRejection` object with arbitrary substituted `reason_code`/`response_data`/`metadata`, so a signer's cryptographically-authenticated rejection can be "recontextualized" to claim a different reason than the signer actually attested to.

### Finding Description
`BlockRejection::hash()` is:
```rust
pub fn hash(&self) -> Sha256Sum {
    let domain_tuple = make_structured_data_domain("block-rejection", "1.0.0", self.chain_id);
    let data = Value::buff_from(self.signer_signature_hash.as_bytes().into()).unwrap();
    structured_data_message_hash(data, domain_tuple)
}
``` [1](#0-0) 

`verify()` and `recover_public_key()` both operate over exactly this digest: [2](#0-1) 

Only `signer_signature_hash` and `chain_id` are covered. Everything else on the struct — `reason_code`, `response_data` (`failed_txid`, `tenure_extend_timestamp`, `tenure_extend_read_count_timestamp`), and `metadata` (`server_version`) — is excluded from the signed content, yet these fields drive safety-relevant logic downstream:

- On the signer side, `handle_block_rejection` recovers the pubkey via `rejection.recover_public_key()` (which only checks the hash+chain_id digest) and then feeds the *unauthenticated* `reject_reason` into `store_and_process_block_rejection`, which persists it and later uses the aggregate `ReorgNotAllowed`-tagged weight to flip `sortition_state.cur_sortition.miner_status` to `InvalidatedBeforeFirstBlock`: [3](#0-2) [4](#0-3) 

- On the node side, `stackerdb_listener.rs` verifies the recovered pubkey against the slot's expected signer pubkey, but then trusts the unauthenticated `rejected_data.reason_code` and `response_data.failed_txid` to accumulate per-transaction "problematic" weight that later drives permanent/temporary txid exclusion: [5](#0-4) [6](#0-5) 

This is the same bug class as the PoolTogether `PermitAndMulticall` finding: the field that downstream logic *relies on for authorization/attribution* (`_from` there, `reason_code`/`response_data` here) is disjoint from what the cryptographic check actually authenticates (`msg.sender` there, `signer_signature_hash`+`chain_id` here). Anywhere a `BlockRejection` with a given signature can be observed and re-emitted with substituted `reason_code`/`response_data`, both signer and node consensus code will treat the substituted content as coming from the original signer, because `verify()`/`recover_public_key()` pass.

### Impact Explanation
If such a re-emitted/relayed `BlockRejection` reaches other signers or the node (e.g. via StackerDB gossip/relay paths that do not independently re-derive trust from the original transport-layer chunk signature but instead re-derive signer identity purely from `recover_public_key()` on the embedded message, as `stackerdb_listener.rs` does at lines 501-513), an attacker can:
1. Misattribute a rejection's `reason_code` to `RejectReasonPrefix::ReorgNotAllowed` to accelerate/force the 30% threshold that marks the current miner invalid (`InvalidatedBeforeFirstBlock`), a liveness-impacting wedge on an honest miner even though fewer signers genuinely voted `ReorgNotAllowed`.
2. Misattribute `failed_txid`/`response_data` to tip a legitimate signer's rejection into being counted against a different transaction, corrupting the `permanently_excluded_txids`/`temporarily_excluded_txids` sets computed in `signer_coordinator.rs`.

Both are safety/liveness-relevant miscounts stemming from an equality (verified signature ⇒ trusted content) that the code silently narrows without downstream logic accounting for it.

### Likelihood Explanation
Exploitation needs only the ability to observe a genuinely-broadcast `BlockRejection` (publicly readable, since all signer StackerDB slots are public) and re-inject a modified copy into the same processing pipeline the node/signers use to interpret peer responses — it does not require another signer's private key, since the signature bytes themselves are reused verbatim. The main uncertainty is whether the StackerDB replication/gossip path re-validates the transport-level chunk signature on every hop before a receiving node processes the embedded `SignerMessage`; the `stackerdb_listener.rs` code shown above discards the chunk-level recovered pubkey (`_pk`) and relies solely on the embedded message signature for identity, which is consistent with this being reachable through relay/gossip. Likelihood is assessed as moderate given this dependency on delivery-path details, but the root-cause defect in the signed digest itself is clear and unconditional.

### Recommendation
Include `reason_code`, `response_data`, and `metadata` (or a hash of the full serialized message) inside the structured data hashed and signed by `BlockRejection::sign()`/`hash()`, mirroring what should also be checked for `BlockAccepted`'s `response_data`/`metadata`. Verifiers should reject any response whose accompanying non-hash fields are not covered by the signature, so that a signature over one context cannot be replayed with attacker-chosen reason/response metadata.

### Proof of Concept
```rust
// Attacker observes a genuine, validly-signed BlockRejection from signer X:
// BlockRejection { signer_signature_hash: H, signature: S, reason: "bad tx",
//                   reason_code: ValidationFailed(BadTransaction), response_data: D1, metadata: M1 }
//
// Because BlockRejection::hash() = domain || H  (chain_id, signer_signature_hash only),
// the SAME signature S verifies for a crafted message:
let forged = BlockRejection {
    signer_signature_hash: H,       // unchanged - required for verify() to still pass
    signature: S,                   // reused verbatim from the genuine message
    reason: "reorg not allowed".into(),
    reason_code: RejectCode::ReorgNotAllowed,     // <-- attacker-chosen, unauthenticated
    response_data: BlockResponseData { failed_txid: Some(victim_txid), .. }, // <-- attacker-chosen
    metadata: M2,                                  // <-- attacker-chosen
};
assert!(forged.verify(&signer_x_pubkey).unwrap()); // passes: hash() ignores reason_code/response_data/metadata
```
`store_and_process_block_rejection` and `stackerdb_listener.rs`'s rejection-handling branch will process `forged` exactly as if signer X had genuinely voted `ReorgNotAllowed` against `victim_txid`. [7](#0-6)

### Citations

**File:** libsigner/src/v0/messages.rs (L1802-1837)
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
```

**File:** stacks-signer/src/v0/signer.rs (L2216-2264)
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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L521-540)
```rust
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
