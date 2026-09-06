### Title
Rejection metadata (`reason`, `reason_code`, `response_data`/`reject_reason`, `failed_txid`) is not covered by the signer's signature, allowing any relaying party to forge these fields under a valid signer signature - ([File: libsigner/src/v0/messages.rs])

### Summary
`BlockRejection::hash()` only commits to `signer_signature_hash` (plus `chain_id` via the structured-data domain), not to `reason`, `reason_code`, or `response_data` (which carries `reject_reason` and `failed_txid`). `verify()`/`recover_public_key()` operate on that same narrow hash. This is a direct structural analog of the TaffyDB bug: a single "index-like" field (`signer_signature_hash`) is authenticated, while other attacker/gossip-forgeable "properties" attached to the record (`reason_code`, `response_data`) are trusted downstream as if they came from the signer, without being covered by the signature.

### Finding Description
`BlockRejection::hash()`:
```rust
pub fn hash(&self) -> Sha256Sum {
    let domain_tuple = make_structured_data_domain("block-rejection", "1.0.0", self.chain_id);
    let data = Value::buff_from(self.signer_signature_hash.as_bytes().into()).unwrap();
    structured_data_message_hash(data, domain_tuple)
}
``` [1](#0-0) 

only feeds `signer_signature_hash` into the signed payload. `sign()` and `verify()`/`recover_public_key()` all operate over this same hash: [2](#0-1) 

Meanwhile the full `BlockRejection` struct carries `reason: String`, `reason_code: RejectCode`, and `response_data: BlockResponseData` (with `reject_reason` and `failed_txid`), none of which are part of the signed hash: [3](#0-2) 

Any party relaying or reconstructing a `BlockRejection` (any gossip participant, or even the receiving node/signer itself) can take a validly-signed rejection for a given `signer_signature_hash` and substitute a different `reason`, `reason_code`, or `response_data.reject_reason`/`failed_txid` while keeping the original `signature` — `verify()` will still return `true` and `recover_public_key()` will still recover the legitimate signer's address, because those fields never entered the hash. Consumers treat the recovered pubkey as proof that the *entire* message, including reason/response_data, originated from that signer:

- `stacks-signer/src/v0/signer.rs::handle_block_rejection` authenticates via `rejection.recover_public_key()` then immediately trusts `rejection.response_data.reject_reason` for downstream processing (`store_and_process_block_rejection`) — the reject reason drives conflict/re-evaluation bookkeeping in `signerdb.rs` (`RejectReasonPrefix`, `should_reevaluate_reject_reason`). [4](#0-3) 
- `stacks-node/src/nakamoto_node/stackerdb_listener.rs` authenticates via `rejected_data.recover_public_key()` and compares it to the expected signer pubkey, then uses `rejected_data.reason_code`/`response_data.failed_txid` to attribute per-signer weight toward "problematic transaction" tracking (`info.total_weight`, `info.problematic_weight`), which can influence whether the miner treats a transaction as bad/problematic: [5](#0-4) 

### Impact Explanation
This breaks the equality between "what the signer actually signed" and "what is validated/acted upon" — the same class of bug as TaffyDB, where trusting an unauthenticated/uncovered field lets an attacker steer behavior attributed to a legitimate party. Concretely, a miner or relaying signer node can rewrite a legitimate signer's rejection to:
- Attribute a different `reason_code`/`reject_reason` to that signer (e.g., swap `ValidationFailed(BadTransaction)` for `ValidationFailed(ProblematicTransaction)` or vice versa), affecting the coordinator's `failed_txids` problematic-weight tally used to decide whether to drop/flag a transaction — a miscounted response scenario.
- Attach/alter `failed_txid` to blame an arbitrary transaction under a real signer's signature.
- Influence a signer's own `should_reevaluate_reject_reason` bookkeeping about a peer's rejection, potentially nudging state-machine behavior (re-evaluation eligibility) based on forged content while the signature still checks out.

This does not directly forge a *block signature* (that's governed by `verify_signer_signatures` over `signer_signature_hash` with full reward-set matching), so it does not by itself produce an invalid/non-canonical block getting signed. Its impact is narrower: miscounted/misattributed rejection metadata under a valid-looking signer signature, which affects transaction-blacklisting bookkeeping and per-signer reject-reason state tracked in `signerdb.rs`.

### Likelihood Explanation
No majority is required — a single relaying party (the node itself sits between signer and its StackerDB peers, or any observer replaying a chunk) can strip/replace the un-signed fields of an already-published `BlockRejection` chunk and re-publish it; `verify()` still passes. This requires only that a legitimate rejection with the target `signer_signature_hash`/`chain_id` was ever produced by that signer — a routine occurrence.

### Recommendation
Include `reason_code`/`reason` and the material fields of `response_data` (`reject_reason`, `failed_txid`, and ideally `tenure_extend_timestamp`/`tenure_extend_read_count_timestamp`) in the structured-data payload that is hashed and signed in `BlockRejection::hash()`, so `verify()`/`recover_public_key()` authenticate the full semantic content of the rejection, not just the block hash and chain id.

### Proof of Concept
1. Signer S produces `BlockRejection::new(hash, RejectReason::ValidationFailed(BadTransaction), sk_S, ...)`, publishing it to its StackerDB slot; `signature = sign(hash(signer_signature_hash, chain_id))`.
2. A relaying party intercepts/reads this chunk, constructs a new `BlockRejection` with the same `signer_signature_hash`, `chain_id`, and `signature`, but `reason_code = RejectCode::ValidationFailed(ProblematicTransaction)` and a different `response_data.failed_txid`.
3. Any consumer calling `rejection.verify(&pubkey_S)` or `rejection.recover_public_key()` (e.g. `stackerdb_listener.rs` lines 501-546, or `stacks-signer/src/v0/signer.rs::handle_block_rejection` lines 2216-2238) accepts the forged message as authentically signed by S, because `hash()` never covered the mutated fields — feeding the wrong reason/txid into weight/problematic-tx bookkeeping attributed to signer S.

Note: I was unable to fully trace `should_reevaluate_reject_reason`'s exact downstream consequences within the remaining tool budget; this is flagged as an area needing deeper confirmation via a full Devin session against `stacks-signer/src/signerdb.rs` and `stacks-signer/src/v0/signer.rs`.

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

**File:** libsigner/src/v0/messages.rs (L1809-1837)
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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L486-546)
```rust
                    SignerMessageV0::BlockResponse(BlockResponse::Rejected(rejected_data)) => {
                        let (lock, cvar) = &*self.blocks;
                        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");

                        let Some(block) = blocks.get_mut(&rejected_data.signer_signature_hash)
                        else {
                            info!(
                                "StackerDBListener: Received rejection for block that we did not request. Ignoring.";
                                "signer_signature_hash" => %rejected_data.signer_signature_hash,
                                "slot_id" => slot_id,
                                "signer_set" => self.signer_set,
                            );
                            continue;
                        };

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
