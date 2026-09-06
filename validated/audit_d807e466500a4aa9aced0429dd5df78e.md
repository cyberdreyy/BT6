### Title
BlockRejection's signature does not commit to `reason_code`/`response_data`, letting the reject reason be rebound after signing - ([File: libsigner/src/v0/messages.rs])

### Summary
`BlockRejection::hash()` computes the structured-data digest that the signer signs (and that peers use to `recover_public_key`) from only `signer_signature_hash` and `chain_id`: [1](#0-0) 

Every other field on the struct — `reason`, `reason_code: RejectCode`, and the whole `response_data: BlockResponseData` (which itself carries `reject_reason: RejectReason`, `failed_txid`, `tenure_extend_timestamp`, `tenure_extend_read_count_timestamp`) — is excluded from the signed digest: [2](#0-1) 

This is the same bug *class* as GHSA-6hfq-h8hq-87mf: the field that is supposed to delimit/authenticate the payload (`Transfer-Encoding` for hyper, the ECDSA signature for the signer) does not actually cover all the content that downstream consumers treat as part of that authenticated message. Two observers of the "same" signed object (the block-height/consensus-hash equality the coordinator/other signers rely on) can end up interpreting different `reason_code`/`response_data` as the signer's authenticated intent.

### Finding Description
`BlockRejection::sign`/`verify`/`recover_public_key` all hash over `self.hash()`, and `hash()` only feeds `signer_signature_hash` (buffer) through `structured_data_message_hash` with a domain tuple keyed by `chain_id`: [1](#0-0) 

Because of this, for a fixed block (`signer_signature_hash`) and `chain_id`, a signer's single ECDSA signature is valid for *every* possible value of `reason`, `reason_code`, and `response_data` (any `RejectReason`, any `failed_txid`, any `tenure_extend_timestamp`/`tenure_extend_read_count_timestamp`). Anyone in possession of one genuine `BlockRejection` message from signer S for block X (which is visible on StackerDB / broadcast to all signers and to the miner's coordinator) can construct a *different* `BlockRejection` struct — same `signer_signature_hash`, same `chain_id`, same `signature` bytes, but a different `reason_code`/`response_data` — and it will still `verify()` successfully and `recover_public_key()` to S's real address, since the signed digest is unchanged.

Downstream logic treats these unauthenticated fields as if they were verified attestations from the signer:
- `store_and_process_block_rejection` uses `rejection.response_data.reject_reason` (mapped to `RejectReasonPrefix`) to bucket weight per reason and to decide whether the 30%+ `ReorgNotAllowed` threshold is met, which flips `sortition_state.cur_sortition.miner_status = SortitionMinerStatus::InvalidatedBeforeFirstBlock` — an action with direct chain-liveness impact: [3](#0-2) 
- `handle_block_rejection` derives `signer_address` purely from `rejection.recover_public_key()` before ever looking at chunk-level provenance, and stores/forwards `(&rejection.response_data.reject_reason).into()` as that signer's authenticated verdict: [4](#0-3) 
- On the node/coordinator side, `stackerdb_listener.rs` aggregates `rejected_data.response_data.failed_txid` and `rejected_data.reason_code` into `failed_txids`/`problematic_weight`, which gates permanent/temporary txid exclusion for future block templates: [5](#0-4) 

On the node/coordinator path there is a mitigating cross-check: the recovered inner pubkey is compared against the StackerDB chunk's slot-owner pubkey before any weight is counted: [6](#0-5) 
That check constrains this specific class of forgery to require control of the *targeted* signer's own StackerDB slot key to actually publish the mutated fields through the coordinator's ingest path — which the rules explicitly place out of scope ("requiring... another signer's key"). I was not able to confirm, within the available index, whether the signer-to-signer path (`handle_block_rejection` invoked from the signer's own StackerDB event dispatch loop in `stacks-signer/src/v0/signer.rs`) performs an equivalent chunk-owner-vs-recovered-pubkey cross-check before calling into `handle_block_rejection`; the relevant dispatch/event-loop code (`process_event`/`SignerEvent::SignerMessages` handling) was not returned by search in a way I could inspect in this session. If that cross-check is *not* present on the signer side (unlike the confirmed node-side check), then any entity that can already write *a* valid `BlockRejection` for the block under its own slot (i.e. a single malicious/compromised signer, no majority needed) could, for a *given* honest signer's already-broadcast rejection, mutate the payload downstream between signer peers via any relay path that reconstructs/forwards `BlockRejection` objects without re-verifying byte-identity against the original chunk (e.g. `add_pending_block_rejection_response`/pending-response replay paths in `signerdb.rs`), since those code paths trust `recover_public_key()` alone.

### Impact Explanation
If reachable without the chunk-owner cross-check, this breaks the "aggregated-weight vs verified-accepts" equality: rejection weight can be attributed to a `RejectReasonPrefix` (in particular `ReorgNotAllowed`) that the named signer never actually asserted, letting a single attacker manufacture the appearance of consensus needed to mark a miner invalid (`InvalidatedBeforeFirstBlock`), or to corrupt the node coordinator's per-txid failed/problematic weight bookkeeping, both of which are liveness-relevant, consensus-adjacent decisions gated on this unauthenticated content. Because the node-side ingestion path (`stackerdb_listener.rs`) does enforce the chunk-owner check, the highest-confidence, unambiguously reachable consequence is scoped to the signer-to-signer path, whose exact enforcement I could not fully verify with the tools available in this session.

### Likelihood Explanation
Low-to-Medium: exploiting this to actual effect (miner invalidation, txid banning) still needs either (a) confirmation that a code path exists that accepts a `BlockRejection` object's fields without tying them to the exact signed byte stream/StackerDB chunk of the honest signer, or (b) control of the target signer's own slot key (out of scope per the rules). The underlying signing defect itself, however, is unconditionally present and trivially demonstrable: any two `BlockRejection` values sharing `signer_signature_hash`/`chain_id` but differing in `reason`/`reason_code`/`response_data` pass `.verify()` with the identical `.signature`.

### Recommendation
Include `reason_code`, `reason`, and the full `response_data` (or at minimum a hash/commitment of them) inside the structured-data message that `BlockRejection::hash()` produces, so the signature binds to the entire semantic content of the rejection, not just the target block hash. Additionally, explicitly verify that any `BlockRejection`/`BlockAccepted` processed by signer-to-signer handlers is bound to the same StackerDB chunk bytes/slot-owner identity used for delivery (mirroring the `rejected_pubkey != signer_pubkey` check already done in `stacks-node/src/nakamoto_node/stackerdb_listener.rs`), so that the recovered pubkey can never be decoupled from the actual message provenance.

### Proof of Concept
1. Signer S genuinely rejects block X: `BlockRejection::new(X_hash, RejectReason::ValidationFailed(ValidateRejectCode::BadTransaction), S_privkey, mainnet, ts1, ts2)` with `response_data.failed_txid = Some(txid_A)`. This produces `signature = Sig`.
2. An observer builds a new struct: same `signer_signature_hash = X_hash`, same `chain_id`, same `signature = Sig`, but `reason_code = RejectCode::ValidationFailed(ValidateRejectCode::ReorgNotAllowed)` (or any other code) and `response_data.failed_txid = Some(txid_B)`.
3. Call `.verify(&S_pubkey)` / `.recover_public_key()` on the forged struct — both succeed and return S's real address, because `hash()` ( [7](#0-6) ) never reads `reason_code`/`response_data`.
4. Feed the forged struct into `store_and_process_block_rejection` / `compute_reject_code_signing_weight(..., RejectReasonPrefix::ReorgNotAllowed)` ( [8](#0-7) ) to demonstrate S's weight now counting toward a reason S never asserted.

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

**File:** stacks-signer/src/v0/signer.rs (L2208-2265)
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
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2274-2369)
```rust
    ) {
        let block_hash = &block_info.signer_signature_hash();
        // We should still store signatures even on consensus reached blocks for auditing purposes.
        // signature is valid! store it
        match self.signer_db.add_block_rejection_signer_addr(
            block_hash,
            signer_address,
            reject_reason,
        ) {
            Err(e) => {
                warn!("{self}: Failed to save block rejection signature: {e:?}",);
            }
            Ok(false) => return, // We already have this signature, do not process it again.
            Ok(true) => (),
        }

        if block_info.has_reached_consensus() {
            // Checking the rejection signatures is pointless. We have already reached consensus on this block.
            return;
        }

        // do we have enough signatures to mark a block a globally rejected?
        // i.e. is (set-size) - (threshold) + 1 reached.
        let rejection_addrs = match self.signer_db.get_block_rejection_signer_addrs(block_hash) {
            Ok(addrs) => addrs,
            Err(e) => {
                warn!("{self}: Failed to load block rejection addresses: {e:?}.",);
                return;
            }
        };
        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
        let total_reject_weight =
            self.compute_signature_signing_weight(rejection_addrs.iter().map(|(addr, _)| addr));
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
        if total_reject_weight.saturating_add(min_weight) <= total_weight {
            // Not enough rejection signatures to make a decision
            info!("{self}: Have not yet received enough block rejections to reach a consensus decision on this block";
                "signer_signature_hash" => %block_hash,
                "signature_weight" => signature_weight,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "block_height" => block_info.block.header.chain_length,
                "total_weight_rejected" => total_reject_weight,
                "total_weight" => total_weight,
                "percent_rejected" => (total_reject_weight as f64 / total_weight as f64 * 100.0),
            );
            return;
        }
        info!("{self}: have reached the block rejection threshold";
            "signer_signature_hash" => %block_hash,
            "signature_weight" => signature_weight,
            "consensus_hash" => %block_info.block.header.consensus_hash,
            "block_height" => block_info.block.header.chain_length,
            "total_weight_rejected" => total_reject_weight,
            "total_weight" => total_weight,
            "percent_rejected" => (total_reject_weight as f64 / total_weight as f64 * 100.0),
        );
        if let Err(e) = block_info.mark_globally_rejected() {
            warn!("{self}: Failed to mark block as globally rejected: {e:?}",);
        }
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
