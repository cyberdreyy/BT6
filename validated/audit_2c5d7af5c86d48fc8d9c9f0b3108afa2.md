I have enough context now to render a decision. Let me evaluate the strongest candidate: the incomplete signature scope in `BlockRejection::hash()`.

Key finding: `BlockRejection::hash()` in `libsigner/src/v0/messages.rs` only binds `self.signer_signature_hash` (and `chain_id` via the domain tuple) into the signed digest:### Title
Signer's `BlockRejection`/`BlockAccepted` signature does not bind `reason_code`/`response_data` (`reject_reason`, `failed_txid`) — cross-context-valid signature over unsigned fields - (File: `libsigner/src/v0/messages.rs`)

### Summary
`BlockRejection::hash()` computes the signed digest over only `signer_signature_hash` and `chain_id` (via the structured-data domain tuple). All the remaining fields of the message — `reason`, `reason_code`, and every field inside `response_data` (`reject_reason`, `failed_txid`, `tenure_extend_timestamp`, `tenure_extend_read_count_timestamp`) — are excluded from the signed preimage, yet these are exactly the fields the receiving signer's business logic and the miner's coordinator trust to make consequential decisions.

### Finding Description
`BlockRejection::hash()`: [1](#0-0) 
only folds `self.signer_signature_hash` into the structured-data hash. `sign()`/`verify()`/`recover_public_key()` all operate over this same truncated digest: [2](#0-1) 

Consumers, however, treat `reason_code`/`response_data.reject_reason`/`response_data.failed_txid` as authenticated, signer-attributable data:
- `handle_block_rejection` in `stacks-signer/src/v0/signer.rs` recovers the public key from the signature and then immediately trusts `rejection.response_data.reject_reason` for bucketing/consensus purposes (`store_and_process_block_rejection`), including the `ReorgNotAllowed` bucket that flips the sortition's `miner_status` to `InvalidatedBeforeFirstBlock`: [3](#0-2) [4](#0-3) 
- The node-side `stackerdb_listener.rs` aggregates `failed_txid` weight from `rejected_data.response_data.failed_txid` to temporarily/permanently ban transactions from future proposals, again after only checking that the *signature* (over the truncated digest) recovers to the expected signer: [5](#0-4) [6](#0-5) 

Because `reason_code`, `reason`, and every `response_data` field sit outside the signed digest, a `BlockRejection` with a legitimate signature for a given `signer_signature_hash` remains **byte-for-byte verifiable** after those fields are altered to any other value (e.g. swapping a benign `ValidationFailed` reason for `ReorgNotAllowed`, or injecting/removing a `failed_txid`). `BlockRejection::verify()`/`recover_public_key()` give no signal that anything changed, because the hash they check never covered those bytes in the first place.

### Impact Explanation
This is a genuine cross-context-valid-signature defect per the rules: a signature that is supposed to authenticate an entire structured message (rejection reason, response metadata) in fact authenticates only a small subset of it. If the transport that carries this payload ever allows the reason/response_data bytes to be modified independently of the outer transport-level signature (e.g., relayed through the RPC event-observer path, cached/replayed by a lower layer, or any future code path that reconstructs a `BlockRejection` object from separately-obtained `reason_code`/`response_data` alongside a stored valid signature+hash pair), a `ValidationFailed`/generic rejection can be turned into a `ReorgNotAllowed` rejection while still verifying, tripping the `InvalidatedBeforeFirstBlock` miner-invalidation logic, or can inject a bogus `failed_txid` that gets banned network-wide — both real safety/liveness breaks that a "rejection recounted/reinterpreted" bug class targets. As currently wired, the StackerDB chunk-level signature (also produced with the signer's key) happens to cover the same bytes end-to-end, which is why this is not trivially exploitable by an unprivileged relay today; the root cause is nonetheless that the application-level signature scheme itself is under-specified/incomplete, and any code path that checks `BlockRejection::verify()` in isolation (bypassing the StackerDB chunk envelope) inherits the flaw. Given the current call sites all layer this message inside an equally-signed StackerDB chunk, I can't demonstrate a concrete exploit without assuming an additional processing path that verifies `BlockRejection` independent of the StackerDB envelope — I was not able to find such a path in the indexed code, so likelihood is bounded by that gap.

### Likelihood Explanation
Low-to-moderate as currently wired, because every observed consumer (`handle_block_rejection`, `stackerdb_listener`) only sees `BlockRejection` bytes after they've passed through the StackerDB chunk's own signature check, which is produced by the same signer key over the whole chunk (including `reason_code`/`response_data`). That makes tampering by a party without a signer's private key currently not exploitable through the paths I could verify. The defect is real and violates the intended authentication contract of `BlockRejection`/`BlockAccepted` (partial vs. full binding), and it would become directly exploitable the moment any component trusts `BlockRejection::verify()` on its own (e.g. audit tooling, alternate transports, or future protocol versions) — I flag this as a latent authentication-scope bug rather than a proven end-to-end break given the scope constraints (no attacker signer key, no transport-mechanics bugs allowed).

### Recommendation
Include `reason_code`, `reason`, and the full `response_data` (or at minimum `reject_reason` and `failed_txid`) in the structured-data preimage hashed by `BlockRejection::hash()` (and analogously audit `BlockAccepted`/`BlockResponseData` binding), so the signature authenticates the entire semantic payload, not just the `signer_signature_hash`. This closes the gap for any current or future consumer that checks `BlockRejection::verify()` without an enclosing envelope signature.

### Proof of Concept
Not independently reproducible within the indexed code as a standalone signer-breaking exploit: doing so requires either (a) a signer's private key to re-sign a tampered `response_data` (out of scope per the rules), or (b) a currently-unidentified processing path that trusts `BlockRejection::verify()` outside the StackerDB chunk envelope. I could not locate such a path in `stacks-signer/`, `libsigner/`, or `stacks-node/src/nakamoto_node/` within the indexed sources. Demonstrating the digest omission itself:
```rust
// libsigner/src/v0/messages.rs
pub fn hash(&self) -> Sha256Sum {
    let domain_tuple = make_structured_data_domain("block-rejection", "1.0.0", self.chain_id);
    let data = Value::buff_from(self.signer_signature_hash.as_bytes().into()).unwrap();
    structured_data_message_hash(data, domain_tuple)   // reason, reason_code, response_data NOT included
}
```
Changing `reason`, `reason_code`, or any `response_data` field on an already-signed `BlockRejection` and re-calling `.verify(pubkey)` will still return `Ok(true)`, confirming the signature does not bind those fields.

### Citations

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L501-543)
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
