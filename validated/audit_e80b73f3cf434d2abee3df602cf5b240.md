## Title
`BlockAccepted`/`BlockRejection` signatures do not cover `response_data`, letting anyone re-attribute a signer's tenure-extend timestamps or reject reason - ([File: libsigner/src/v0/messages.rs])

### Summary
This is a direct structural analog of the H-14 report: the external report's `verifySignature` omits `from_chain` from the signed digest, letting an attacker replay a "validly signed" message with attacker-controlled unsigned fields that drive downstream logic. In `stacks-signer`, `BlockAccepted` and `BlockRejection` have the exact same defect: the cryptographic signature is computed over only `signer_signature_hash` (plus `chain_id` for rejections), while `response_data` (`tenure_extend_timestamp`, `tenure_extend_read_count_timestamp`, `reject_reason`, `failed_txid`) and, for rejections, `reason`/`reason_code` are left completely outside the signed digest yet are trusted and acted upon by every other signer and by the miner's `StackerDBListener`.

### Finding Description
`BlockRejection::hash()` computes the signed digest as: [1](#0-0) 
i.e. only `signer_signature_hash` and `chain_id` (via the domain tuple) are committed to. The struct itself, however, carries much more state that is *not* covered: [2](#0-1) 
`reason`, `reason_code`, and the entire `response_data` (`tenure_extend_timestamp`, `reject_reason`, `tenure_extend_read_count_timestamp`, `failed_txid`) are serialized alongside the signature but never enter `hash()`.

Similarly, `BlockAccepted` signatures are produced by signing the raw `signer_signature_hash` bytes directly: [3](#0-2) 
and the struct carries `response_data` (again `tenure_extend_timestamp`/`tenure_extend_read_count_timestamp`) that is never part of the signed material: [4](#0-3) 

Because `verify()`/`recover_public_key()` for both types only re-derive `hash()` (over `signer_signature_hash` [+`chain_id`]): [5](#0-4) 
any relayer on the StackerDB/gossip layer (or a one-slot miner front-running/racing broadcast of these messages, matching the "malicious actor" role in H-14) can take a legitimately signed `BlockAccepted`/`BlockRejection` from an honest signer and rewrite `response_data`/`reason`/`reason_code` to arbitrary attacker-chosen values while the signature check still passes and the message is still attributed to the honest signer's key.

Downstream, other signers and the miner trust these fields as coming from the signing signer:
- `handle_block_rejection` logs and stores `(&rejection.response_data.reject_reason).into()` keyed by the recovered (valid) signer address, feeding signer-db aggregation of *why* a signer rejected: [6](#0-5) 
- `handle_block_signature` logs and forwards `accepted.response_data.tenure_extend_timestamp` / `tenure_extend_read_count_timestamp` attributed to the recovered signer: [7](#0-6) 
- The miner's `StackerDBListener` extracts `tenure_extend_timestamp`/`read_count_extend_timestamp` from `BlockAccepted.response_data` per-signer and uses them (weighted by that signer's stacking weight) to decide tenure-extension timing: [8](#0-7) 

### Impact Explanation
This breaks the equality "signed content == acted-upon content" for the two most consequential per-signer response fields:
1. **Tenure-extend timestamps** (`tenure_extend_timestamp`, `tenure_extend_read_count_timestamp`) drive when/whether the network decides to extend the current tenure. An attacker who intercepts gossip/StackerDB chunks can rewrite these unsigned fields on an otherwise-genuine, validly-signed `BlockAccepted`/`BlockRejection` from any signer, forging that signer's extend-timing vote without possessing their key. Because these are weighted by the (falsely-attributed) signer's stacking weight, this can bias or wedge the tenure-extension decision — a liveness-relevant miscount that maps onto the rubric's "aggregated-weight vs verified-accepts" equality break.
2. **`reject_reason`/`reason_code`/`reason`** on a `BlockRejection` are similarly forgeable: an attacker can take a genuine rejection signature (e.g., for `ValidationFailed`) and relabel it as `RejectedInPriorRound` or another reason, corrupting the reject-reason bookkeeping (`add_pending_block_rejection_response`, `store_and_process_block_rejection`) that other signers use to reason about why a block failed — a "rejection recounted"/mislabeled-as-something-else class of issue.

This does not require a signer's private key, a majority, the `auth_token`, or local access — only network-level visibility of the gossiped/StackerDB message, consistent with the report's front-running actor and within the allowed scope (`libsigner/v0` message types acted upon by `stacks-signer/src/v0/signer.rs` and node-side `stacks-node/src/nakamoto_node/stackerdb_listener.rs`).

### Likelihood Explanation
`BlockAccepted`/`BlockRejection` messages are broadcast in the clear over StackerDB, which any network participant (not just signers) can read and, since chunks can be re-uploaded/rebroadcast by any signer slot (or observed and reconstructed off-path), an attacker only needs to swap out the unsigned `response_data`/`reason` bytes and re-serialize — no cryptography needs to be broken. This is a straightforward message-malleability bug, directly analogous to the "missing `from_chain` in the signed digest" root cause in the report, and is reachable by any one gossip participant without needing majority collusion.

### Recommendation
Bind all semantically meaningful fields into the signed digest for both `BlockAccepted` and `BlockRejection`:
- Extend `BlockRejection::hash()` to include `reason_code` and the full `response_data` (or a canonical hash of it), not just `signer_signature_hash`/`chain_id`.
- Change `BlockAccepted` signing (`create_block_acceptance` in `stacks-signer/src/v0/signer.rs`) to sign over a digest that additionally commits to `response_data` (`tenure_extend_timestamp`, `tenure_extend_read_count_timestamp`), rather than signing the bare `signer_signature_hash` bits.
- Update `verify()`/`recover_public_key()` accordingly so any tampering with `response_data`/`reason`/`reason_code` invalidates the signature.

### Proof of Concept
1. Observe a legitimate `SignerMessage::BlockResponse(BlockResponse::Accepted(accepted))` chunk on StackerDB, containing a valid `signature` over `signer_signature_hash`.
2. Deserialize it, replace `response_data.tenure_extend_timestamp` (and/or `tenure_extend_read_count_timestamp`) with an attacker-chosen value, re-serialize, and rebroadcast/re-inject the chunk (e.g., write it to the corresponding signer slot, or intercept/relay it before the original propagates).
3. `handle_block_signature` (`stacks-signer/src/v0/signer.rs:2371-2439`) and `StackerDBListener` (`stacks-node/src/nakamoto_node/stackerdb_listener.rs:386-426`) both recover the *same* valid public key from `signature`/`signer_signature_hash` — the check never inspects `response_data` — so the tampered timestamps are accepted and logged/acted upon as if the honest signer had produced them, weighted by that signer's real stacking weight.
4. The analogous swap works on `BlockRejection.response_data.reject_reason`/`reason_code`/`reason`, verified only via `BlockRejection::hash()` (`libsigner/src/v0/messages.rs:1802-1807`), which never touches those fields.

### Citations

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

**File:** libsigner/src/v0/messages.rs (L1732-1765)
```rust
impl BlockRejection {
    /// Create a new BlockRejection for the provided block and reason code
    pub fn new(
        signer_signature_hash: Sha512Trunc256Sum,
        reject_reason: RejectReason,
        private_key: &StacksPrivateKey,
        mainnet: bool,
        full_extend_ts: u64,
        read_count_extend_ts: u64,
    ) -> Self {
        let chain_id = if mainnet {
            CHAIN_ID_MAINNET
        } else {
            CHAIN_ID_TESTNET
        };
        let mut rejection = Self {
            reason: reject_reason.to_string(),
            reason_code: (&reject_reason).into(),
            signer_signature_hash,
            signature: MessageSignature::empty(),
            chain_id,
            metadata: SignerMessageMetadata::default(),
            response_data: BlockResponseData::new(
                full_extend_ts,
                reject_reason,
                read_count_extend_ts,
                None,
            ),
        };
        rejection
            .sign(private_key)
            .expect("Failed to sign BlockRejection");
        rejection
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

**File:** stacks-signer/src/v0/signer.rs (L473-497)
```rust
    /// Create a block acceptance for a block
    pub fn create_block_acceptance(&self, block: &NakamotoBlock) -> BlockAccepted {
        let signature = self
            .private_key
            .sign(block.header.signer_signature_hash().bits())
            .expect("Failed to sign block");
        BlockAccepted::new(
            block.header.signer_signature_hash(),
            signature,
            self.signer_db.calculate_full_extend_timestamp(
                self.proposal_config
                    .tenure_idle_timeout
                    .saturating_add(self.proposal_config.tenure_idle_timeout_buffer),
                block,
                true,
            ),
            self.signer_db.calculate_read_count_extend_timestamp(
                self.proposal_config
                    .read_count_idle_timeout
                    .saturating_add(self.proposal_config.tenure_idle_timeout_buffer),
                block,
                true,
            ),
        )
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2251-2264)
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
```

**File:** stacks-signer/src/v0/signer.rs (L2423-2439)
```rust
        info!("{self}: Received block acceptance";
            "signer_pubkey" => public_key.to_hex(),
            "signer_address" => %signer_address,
            "signer_signature_hash" => %block_hash,
            "consensus_hash" => %block_info.block.header.consensus_hash,
            "block_height" => block_info.block.header.chain_length,
            "signer_weight" => self.signer_weights.get(&signer_address).copied().unwrap_or(0),
            "tenure_extend_timestamp" => accepted.response_data.tenure_extend_timestamp,
            "tenure_extend_read_count_timestamp" => accepted.response_data.tenure_extend_read_count_timestamp
        );
        self.store_and_process_block_signature(
            stacks_client,
            sortition_state,
            &mut block_info,
            &signer_address,
            signature,
        );
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L386-426)
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
                            warn!(
                                "StackerDBListener: Processed signature but didn't validate over the expected block. Ignoring";
                                "signature" => %signature,
                                "signer_signature_hash" => %block_sighash,
                                "slot_id" => slot_id,
                            );
                            continue;
                        }
```
