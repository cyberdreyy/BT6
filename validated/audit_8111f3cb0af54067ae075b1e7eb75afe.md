### Title
Unsigned rejection metadata (`reason`, `reason_code`, `response_data.reject_reason`) allows re-labeling of any valid `BlockRejection` signature, forging `ReorgNotAllowed` weight and triggering false miner invalidation - ([File: libsigner/src/v0/messages.rs])

### Summary
`BlockRejection::hash()` in `libsigner/src/v0/messages.rs` computes the signed digest over only `self.signer_signature_hash` (plus chain-id domain separation); it does not include `reason`, `reason_code`, or `response_data` (which carries `reject_reason: RejectReason`). Because `verify()`/`recover_public_key()` re-derive the same restricted hash, an attacker with only StackerDB read/relay access can take *any* validly signed `BlockRejection` gossiped by an honest signer and re-encode it with a different `reason_code`/`response_data.reject_reason` while the original signature remains valid, causing the receiver in `stacks-signer/src/v0/signer.rs` to attribute an attacker-chosen rejection *reason* to the honest signer's authenticated vote. Note: the field is a plain `String`, not `StacksString`, and the receiving logic lives in `stacks-signer/src/v0/signer.rs`, not a file named `signer_coordinator.rs` (no such file exists in this repo aside from `stacks-node/src/nakamoto_node/signer_coordinator.rs`, which is unrelated to this path).

### Finding Description
The claimed equality — "bytes covered by the signature == all bytes the receiver treats as attested" — does **not** hold. `BlockRejection::hash()` builds the signed message solely from `signer_signature_hash` [1](#0-0) , and `verify`/`recover_public_key` use exactly that same restricted hash [2](#0-1) . Meanwhile `BlockRejection` also carries `reason: String`, `reason_code: RejectCode`, and `response_data: BlockResponseData` (which embeds `RejectReason`) [3](#0-2)  — none of these fields are part of the signed payload, yet they are serialized alongside the signature and consumed on receipt.

On receipt, `handle_block_rejection` only checks that the signature recovers to a known signer address; it never re-validates `reason_code`/`response_data` against anything committed by the signature [4](#0-3) . The recovered `reject_reason` (derived from the unsigned `response_data.reject_reason`) is then passed into `store_and_process_block_rejection`, which stores it keyed by signer address and reason prefix, and separately tallies weight per specific reason code via `compute_reject_code_signing_weight` [5](#0-4) . That per-reason tally is used, once the global rejection threshold is reached, to decide whether `RejectReasonPrefix::ReorgNotAllowed` weight also crosses threshold, which directly flips `sortition_state.cur_sortition.miner_status` to `InvalidatedBeforeFirstBlock` [6](#0-5) .

Exploit flow: an attacker with StackerDB relay access observes an honestly signed `BlockRejection` for a given block (any reason, e.g. `ValidationFailed`). Because `reason`/`reason_code`/`response_data` are not covered by the signature, the attacker re-serializes the same message with `reason_code`/`response_data.reject_reason` overwritten to `ReorgNotAllowed` and re-gossips it (or gossips a second, competing copy). The signature stays byte-identical and valid because it never depended on those fields. Any receiving signer's `handle_block_rejection` accepts it as an authentic vote from the original signer, now counted toward the `ReorgNotAllowed` weight bucket used to invalidate the miner.

This is scoped, however, to the legacy (pre-global-state) signer protocol path: the reorg-based miner invalidation check is explicitly skipped once `determine_active_signer_protocol_version()` indicates the "global state" protocol is active [7](#0-6) .

### Impact Explanation
If enough re-labeled rejections (drawn from honest signers' legitimately signed but unrelated-reason rejections) accumulate `ReorgNotAllowed` weight past threshold, the receiving signer marks the current miner as `InvalidatedBeforeFirstBlock` even though no such quorum of signers actually observed/asserted a disallowed reorg. This can cause that signer to refuse to sign otherwise-valid blocks from a legitimate miner — a liveness break matching the "High" category ("a signer wedged into never signing valid blocks"). It does not by itself let an attacker get an invalid block *signed*, nor does it flip a rejection into an acceptance or forge cross-epoch signatures, so it does not reach the "Critical" bar as framed in the question (rejection recounted as acceptance / invalid block signed). This is limited to the legacy, non-global-state signer protocol code path.

### Likelihood Explanation
Preconditions are modest and match the stated attacker model: only StackerDB gossip read/relay access is needed, no signer key or majority collusion. The attacker needs (a) a block that is already trending toward the global rejection threshold for *some* reason, and (b) enough honestly-signed rejection messages (any reason) for that block whose weight, when relabeled to `ReorgNotAllowed`, also crosses the (same) threshold. Since the same set of already-collected rejections can be repackaged, this is directly repeatable per proposed/rejected block, but is gated by the legacy (non-global-state) protocol branch remaining active, and requires that overall rejection threshold has already been reached (which requires enough real signer weight rejecting the block for legitimate reasons).

### Recommendation
Include `reason_code` and the serialized `response_data` (or at least `RejectReason`) inside the structured-data hash signed in `BlockRejection::hash()`, so any mutation of these fields invalidates the signature. Alternatively, bind the reason/response_data via a nested commitment (e.g., hash them into the domain buffer passed to `structured_data_message_hash`) so `verify`/`recover_public_key` fail for any tampered reason field.

### Proof of Concept
```rust
// libsigner/src/v0/messages.rs (or a new signer test module)
#[test]
fn mutated_reason_code_keeps_valid_signature() {
    let sk = StacksPrivateKey::random();
    let hash = Sha512Trunc256Sum([7u8; 32]);
    let mut rejection = BlockRejection::new(
        hash,
        RejectReason::ValidationFailed(ValidateRejectCode::InvalidBlock),
        &sk,
        false,
        0,
        0,
    );
    let pubkey = StacksPublicKey::from_private(&sk);
    assert!(rejection.verify(&pubkey).unwrap());

    // Attacker relabels the reason/reason_code/response_data without
    // touching signer_signature_hash or signature.
    rejection.reason_code = RejectCode::ReorgNotAllowedTestingOnly; // placeholder for RejectCode::from(RejectReason::ReorgNotAllowed)
    rejection.response_data.reject_reason = RejectReason::ReorgNotAllowed;
    rejection.reason = "forged".to_string();

    // Signature still verifies -- proves reason fields are unsigned.
    assert!(rejection.verify(&pubkey).unwrap(), "signature should verify unless reason is bound to signature (this assertion currently PASSES, demonstrating the bug)");
}
```
Companion assertion (conceptual, in `stacks-signer/src/v0/signer.rs` test harness): drive `handle_block_rejection` with the mutated message and assert that `store_and_process_block_rejection` records `RejectReasonPrefix::ReorgNotAllowed` for that signer address, then assert `compute_reject_code_signing_weight(..., RejectReasonPrefix::ReorgNotAllowed)` increases — demonstrating the unsigned field drives the miner-invalidation tally.

### Citations

**File:** libsigner/src/v0/messages.rs (L1714-1730)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L2186-2199)
```rust
    /// Compute the rejection weight for the given reject code, given a list of signatures
    fn compute_reject_code_signing_weight<'a>(
        &self,
        addrs: impl Iterator<Item = &'a (StacksAddress, RejectReasonPrefix)>,
        reject_code: RejectReasonPrefix,
    ) -> u32 {
        addrs.filter(|(_, code)| *code == reject_code).fold(
            0u32,
            |signing_weight, (stacker_address, _)| {
                let stacker_weight = self.signer_weights.get(stacker_address).unwrap_or(&0);
                signing_weight.saturating_add(*stacker_weight)
            },
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
