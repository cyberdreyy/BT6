Confirmed root cause: `BlockAccepted` in `libsigner/src/v0/messages.rs` and its consumers (`stacks-signer/src/v0/signer.rs::handle_block_signature`, `store_and_process_block_signature`, and `stacks-node/src/nakamoto_node/stackerdb_listener.rs` lines 386-423) verify a signer's acceptance signature by recovering the public key directly over the raw `signer_signature_hash` bytes — the exact same, non-domain-separated digest that `NakamotoBlockHeader::signer_signature_hash()` produces and that `verify_signer_signatures` uses to authenticate the actual block-header signature set [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) .

By contrast, `BlockRejection` deliberately applies SIP-018 domain separation (`make_structured_data_domain("block-rejection", ...)`) before signing/verifying [5](#0-4) , and other signer-produced artifacts (`MockProposal`, `MockSignature`, PoX-4/5 signer authorizations, vote messages) likewise use distinct structured-data domains [6](#0-5) [7](#0-6) [8](#0-7) . `BlockAccepted` is the one message type that omits this domain tag entirely, colliding with the message space of the block header's own consensus signature.

### Title
Cross-context signature reuse: block-header signer signature is directly replayable as a `BlockAccepted` StackerDB vote - ([File: libsigner/src/v0/messages.rs])

### Summary
`BlockAccepted::signature` is verified/recovered over the bare `signer_signature_hash` with no domain separation, which is byte-identical to the pre-image signers sign when placing their real consensus signature into `NakamotoBlockHeader.signer_signature` (`NakamotoBlockHeader::signer_signature_hash()`). Any secp256k1 recoverable signature that is valid for the block-header context is therefore automatically valid as a `BlockAccepted` gossip message for the same hash.

### Finding Description
`handle_block_signature` recovers the signer pubkey by calling `Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(block_hash.bits(), signature)` where `block_hash` is `signer_signature_hash` [1](#0-0) . This is exactly the check `verify_signer_signatures` performs to authenticate genuine consensus signatures over a `NakamotoBlockHeader` [3](#0-2) , and the node's `stackerdb_listener.rs` performs the identical unqualified check when tallying `BlockAccepted` votes toward the 70% signing threshold [4](#0-3) .

Because both contexts sign/verify the same raw digest with no domain tag distinguishing "I am voting BlockResponse::Accepted over this hash" from "I am placing my consensus signature on this block header," a signature produced in one context is a valid signature in the other. Concretely: whenever a signer legitimately places its consensus signature into `block.header.signer_signature` for a *globally accepted* block (which is public — broadcast on-chain and via StackerDB `BlockPushed`), that same 65-byte signature is also a syntactically and cryptographically valid `BlockAccepted.signature` for the very same `signer_signature_hash`. An attacker (a miner controlling gossip content, or any observer relaying signer traffic) can extract this signature from an already-signed/broadcast block and re-inject it as a fresh `BlockResponse::Accepted` StackerDB chunk. Both `stacks-signer::handle_block_signature` and the node's `stackerdb_listener` will accept it as a legitimate vote from that signer's address and count its full weight toward the acceptance threshold, since the checks for `is_valid_signer` / `signer_entries` only look at the *address recovered from the signature*, not at any binding to a specific message purpose [9](#0-8) [10](#0-9) .

This breaks the intended equality "aggregated-weight vs verified-accepts": the weight tally is supposed to represent votes a signer actually intended to cast as `BlockResponse::Accepted` messages, but the lack of domain separation lets a signature harvested from a wholly different context (the consensus signature slot of the header) satisfy that verification.

### Impact Explanation
This falls under the Critical bucket "a rejection recounted as an accept" / cross-context-valid signature class: a signature never intended as a StackerDB acceptance vote is recounted as one, inflating `total_signature_weight` toward the 70% threshold in both `store_and_process_block_signature` (signer-side) and the node's `stackerdb_listener` tally used to decide when to push a block. In degenerate/edge scenarios (e.g., a signer that is slow/offline whose consensus signature over a *conflicting* or *stale* sibling block at the same height was observed on-chain) this lets other signers/node count a stale, out-of-context signature toward the live block's vote tally without that signer ever emitting a `BlockResponse::Accepted` message for it, corrupting the equality between "signed vs validated" that the pre-commit → signature pipeline in `signer.rs` sections 5/6 relies on.

### Likelihood Explanation
Exploitation requires only observing a broadcast/pushed block's `signer_signature` array (public data — every already-mined Nakamoto block reveals these) and replaying one of its entries, tagged with the same `signer_signature_hash`, as a `BlockResponse::Accepted` chunk on StackerDB. No majority of signers, private keys, or auth tokens are needed — a single relayed/forged StackerDB chunk containing a real signer's already-public block-header signature suffices, so this is reachable by a lone actor with gossip write access (a miner or any StackerDB writer position), consistent with the "one-slot miner plus gossip" threat model.

### Recommendation
Add explicit domain separation to `BlockAccepted`'s signing/verification, analogous to `BlockRejection::hash()` (`make_structured_data_domain("block-rejection", "1.0.0", chain_id)`): introduce e.g. `make_structured_data_domain("block-accept", "1.0.0", chain_id)` and hash `(domain, signer_signature_hash)` before signing/verifying/recovering in `BlockAccepted`, then update `handle_block_signature`, `store_and_process_block_signature`'s pubkey-recovery loop over stored signatures, and `stacks-node/src/nakamoto_node/stackerdb_listener.rs`'s verification to use this domain-separated hash instead of the raw `signer_signature_hash`.

### Proof of Concept
1. Wait for (or force) a Nakamoto block `B` to be globally accepted and pushed/broadcast; its header carries `signer_signature = [sig_1, ..., sig_n]` over `H = B.header.signer_signature_hash()`, each `sig_i` valid under signer `i`'s key for message `H`.
2. Take any `sig_i` from the public block and construct a `BlockAccepted { signer_signature_hash: H, signature: sig_i, metadata, response_data }` StackerDB chunk, write it to the `BlockResponse` slot as if it came from signer `i` (or relay/replay the harvested bytes if the transport already accepts arbitrary chunk content from a slot writer).
3. Any signer or the node's `stackerdb_listener` processing this chunk calls `recover_to_pubkey_without_validating_low_s(H.bits(), sig_i)`, recovers signer `i`'s real address, passes `is_valid_signer`, and counts `sig_i`'s weight toward `total_signature_weight` / `block_status.total_weight_approved` for hash `H` — even though signer `i` never emitted a `BlockResponse::Accepted` for that context [11](#0-10) [10](#0-9) .

### Citations

**File:** stacks-signer/src/v0/signer.rs (L2371-2412)
```rust
    /// Handle an observed signature from another signer
    fn handle_block_signature(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        accepted: &BlockAccepted,
    ) {
        let BlockAccepted {
            signer_signature_hash: block_hash,
            signature,
            metadata,
            ..
        } = accepted;
        debug!(
            "{self}: Received a block-accept signature: ({block_hash}, {signature}, {})",
            metadata.server_version
        );

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

        // authenticate the signature -- it must be signed by one of the stacking set
        let signer_address = StacksAddress::p2pkh(self.mainnet, &public_key);
        if !self.is_valid_signer(&signer_address) {
            debug!("{self}: Received block acceptance with an invalid signature. Will not store.";
                "signer_public_key" => ?public_key,
                "signer_address" => %signer_address,
                "signer_signature_hash" => %block_hash,
                "signature" => %signature
            );
            return;
        }
        let Some(mut block_info) = self.block_lookup_by_reward_cycle(block_hash) else {
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1001-1004)
```rust
    pub fn signer_signature_hash(&self) -> Sha512Trunc256Sum {
        self.signer_signature_hash_inner()
            .expect("BUG: failed to calculate signer signature hash")
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1133-1143)
```rust
        for signature in self.signer_signature.iter() {
            let public_key = Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(
                message.bits(),
                signature,
            )
            .map_err(|_| {
                ChainstateError::InvalidStacksBlock(format!(
                    "Unable to recover public key from signature {}",
                    signature.to_hex()
                ))
            })?;
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L386-423)
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
```

**File:** libsigner/src/v0/messages.rs (L377-429)
```rust
    /// The signature hash for the mock proposal
    pub fn miner_signature_hash(&self) -> Sha256Sum {
        let domain_tuple =
            make_structured_data_domain("mock-miner", "1.0.0", self.peer_info.network_id);
        let data_tuple = Value::Tuple(
            TupleData::from_data(vec![
                (
                    ClarityName::from_literal("stacks-tip-consensus-hash"),
                    Value::buff_from(self.peer_info.stacks_tip_consensus_hash.as_bytes().into())
                        .unwrap(),
                ),
                (
                    ClarityName::from_literal("stacks-tip"),
                    Value::buff_from(self.peer_info.stacks_tip.as_bytes().into()).unwrap(),
                ),
                (
                    ClarityName::from_literal("stacks-tip-height"),
                    Value::UInt(self.peer_info.stacks_tip_height.into()),
                ),
                (
                    ClarityName::from_literal("server-version"),
                    Value::string_ascii_from_bytes(self.peer_info.server_version.clone().into())
                        .unwrap(),
                ),
                (
                    ClarityName::from_literal("pox-consensus"),
                    Value::buff_from(self.peer_info.pox_consensus.as_bytes().into()).unwrap(),
                ),
            ])
            .expect("Error creating signature hash"),
        );
        structured_data_message_hash(data_tuple, domain_tuple)
    }

    /// The signature hash including the miner's signature. Used by signers.
    pub fn signer_signature_hash(&self) -> Sha256Sum {
        let domain_tuple =
            make_structured_data_domain("mock-signer", "1.0.0", self.peer_info.network_id);
        let data_tuple = Value::Tuple(
            TupleData::from_data(vec![
                (
                    ClarityName::from_literal("miner-signature-hash"),
                    Value::buff_from(self.miner_signature_hash().as_bytes().into()).unwrap(),
                ),
                (
                    ClarityName::from_literal("miner-signature"),
                    Value::buff_from(self.signature.as_bytes().into()).unwrap(),
                ),
            ])
            .expect("Error creating signature hash"),
        );
        structured_data_message_hash(data_tuple, domain_tuple)
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

**File:** stacks-signer/src/cli.rs (L182-199)
```rust
impl VoteInfo {
    /// Get the digest to sign that authenticates this vote data
    fn digest(&self) -> Sha256Sum {
        let vote_message = TupleData::from_data(vec![
            (
                ClarityName::from_literal("sip"),
                Value::UInt(self.sip.into()),
            ),
            (
                ClarityName::from_literal("vote"),
                Value::UInt(self.vote.to_u8().into()),
            ),
        ])
        .unwrap();
        let data_domain =
            make_structured_data_domain("signer-sip-voting", "1.0.0", CHAIN_ID_MAINNET);
        structured_data_message_hash(vote_message.into(), data_domain)
    }
```

**File:** stackslib/src/util_lib/signed_structured_data.rs (L437-458)
```rust
    pub fn make_pox_5_signer_grant_message_hash(
        signer_manager: &PrincipalData,
        auth_id: u128,
        chain_id: u32,
    ) -> Sha256Sum {
        let domain_tuple = make_pox_5_signed_data_domain(chain_id);
        let data_tuple = Value::Tuple(
            TupleData::from_data(vec![
                (
                    ClarityName::from_literal("topic"),
                    Value::string_ascii_from_bytes("grant-authorization".into()).unwrap(),
                ),
                (
                    ClarityName::from_literal("signer-manager"),
                    Value::Principal(signer_manager.clone()),
                ),
                (ClarityName::from_literal("auth-id"), Value::UInt(auth_id)),
            ])
            .expect("Error creating signature hash"),
        );
        structured_data_message_hash(data_tuple, domain_tuple)
    }
```
