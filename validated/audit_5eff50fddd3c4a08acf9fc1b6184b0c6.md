## Title
Unsigned `BlockRejection` fields let a single relayer splice a valid signer signature onto a forged rejection reason / failed-txid, enabling miscounted rejection tallies and forged transaction bans — (File: `libsigner/src/v0/messages.rs`)

### Summary
`BlockRejection`'s signature only commits to the block's `signer_signature_hash` (plus `chain_id` via domain separation). The `reason`, `reason_code`, and `response_data` (including per-transaction `failed_txid` and the `reject_reason` used for weighted miner-invalidation logic) are **not** part of the signed payload. Anyone who observes one authentic, signed `BlockRejection` from a signer for a given block (these are broadcast in the clear over StackerDB) can re-wrap the same `signature`/`signer_signature_hash` with an arbitrary `reason`, `reason_code`, and `response_data`, and it will still pass signature verification and be attributed to that signer. This is the header/content decoupling from the reference SSRF/cache-poisoning bug class applied to gossip messages: the authenticated field (`signer_signature_hash`) is independent of the unauthenticated, attacker-controlled payload that downstream logic actually acts on.

### Finding Description
`BlockRejection::hash()` builds the signed digest from only the `signer_signature_hash` buffer and a domain tuple derived from `chain_id`: [1](#0-0) 

`recover_public_key()` recomputes this same restricted hash to recover the signer's key: [2](#0-1) 

None of `reason`, `reason_code`, or `response_data` (which carries `failed_txid`, `tenure_extend_timestamp`, and the `reject_reason` enum used for weighted decisions) is included in the signed digest, even though they are all part of the wire struct: [3](#0-2) 

On the signer side, `handle_block_rejection` only checks that the signature recovers to a known signer address, then unconditionally trusts `rejection.response_data.reject_reason` for downstream accounting: [4](#0-3) 

That `reject_reason` (its `RejectReasonPrefix`) is stored and later used to compute `total_reorg_reject_weight`, which — if it crosses the blocking-minority threshold — flips `sortition_state.cur_sortition.miner_status` to `InvalidatedBeforeFirstBlock`, directly influencing the local state machine used to accept/reject future proposals from that miner: [5](#0-4) 

The same unauthenticated fields are trusted node-side in the mining coordinator's StackerDB listener, which increments per-txid weight straight from `rejected_data.response_data.failed_txid` and `rejected_data.reason_code`: [6](#0-5) 

When a blocking minority (>30% weight) of such (forgeable) per-txid rejections accumulates, the coordinator permanently or temporarily bans the txid from all future blocks in the tenure: [7](#0-6) 

Because the signature never binds `reason_code`/`response_data`, an attacker who merely relays traffic (no signer key required) can:
1. Observe any one legitimately signed `BlockRejection` from signer S for block hash H (e.g., a routine `ConnectivityIssues` rejection).
2. Construct a new `BlockRejection` with the same `signer_signature_hash = H`, same `chain_id`, same `signature`, but an attacker-chosen `reason_code` (e.g., `ValidationFailed(ProblematicTransaction)`), `response_data.failed_txid = <victim txid>`, and `response_data.reject_reason = ReorgNotAllowed`.
3. Rebroadcast it. It passes `recover_public_key`/`is_valid_signer` because those only re-derive the hash over `H` and `chain_id`, exactly as originally signed.

This lets a single relayer (no majority, no signer key, no auth token) inject falsely-attributed rejection reasons for real signers, corrupting the weighted tallies both in the signer's local reorg-permit/miner-invalidation bookkeeping and in the node's mining-coordinator censorship (txid-ban) logic — an "aggregated-weight vs verified-accepts" equality break: the weight is counted against a reason category the signer never actually asserted.

### Impact Explanation
This breaks the guarantee that weighted rejection tallies (used to invalidate a miner's sortition status and to permanently/temporarily censor transactions from block templates) reflect what real signers actually voted for. A relayer with no signing key can:
- Forge attribution of `ReorgNotAllowed` rejections to real signers, pushing the weighted reorg-reject count over threshold and causing signers to mark a legitimate miner as invalid (liveness harm / miner censorship), and
- Forge `failed_txid`/`ProblematicTransaction` attributions to ban arbitrary transactions from all future blocks in a tenure once a blocking minority of forged weight accumulates (targeted transaction censorship / liveness harm for specific senders).

Both fall under the "signer wedged" / "miscounted response" impact categories: a rejection is recounted under a different, unverified classification than what the signer actually signed.

### Likelihood Explanation
StackerDB traffic (including `BlockResponse::Rejected` chunks) is public/observable by design, so the attacker only needs to see one legitimate rejection for the target block from each signer whose attribution they want to forge, which is routine during any contested proposal (rejections are common in this protocol's normal operation, e.g. `ConnectivityIssues`, `ProposalTooOld`, etc., as seen throughout the test suite). No majority collusion, no signer private key, and no node auth token are needed — only relay/re-publish capability, well within the described "one-slot miner (plus gossip)" threat model.

### Recommendation
Include `reason`, `reason_code`, and `response_data` (or at minimum a hash/commitment of them) in the digest signed and verified in `BlockRejection::hash()`/`sign()`/`verify()`/`recover_public_key()`, so that the signature authenticates the entire semantic content of the rejection, not just the block hash. Apply the same fix to any other message type (e.g. `BlockAccepted`) whose signature currently omits fields that downstream logic treats as authenticated.

### Proof of Concept
1. Capture a real `SignerMessage::BlockResponse(BlockResponse::Rejected(rejection))` chunk for block hash `H` signed by signer `S` (any reason, e.g. `RejectCode::ConnectivityIssues`), as constructed via `BlockRejection::new`: [8](#0-7) 
2. Build a new `BlockRejection` struct with the same `signer_signature_hash`, `chain_id`, and `signature` bytes, but set `reason_code = RejectCode::ValidationFailed(ValidateRejectCode::ProblematicTransaction)`, `response_data.failed_txid = Some(<target txid>)`, and `response_data.reject_reason = RejectReason::ProblematicTransactions` (or `ReorgNotAllowed`).
3. Serialize and push this forged chunk into the `BlockResponse` StackerDB slot (as done via `StackerDBSession::put_chunk` in existing tests, e.g. the manual-injection pattern used in `failed_txs.rs`): [9](#0-8) 
4. Observe that other signers' `handle_block_rejection` accept it (`recover_public_key`/`is_valid_signer` succeed because the hash only covers `H`/`chain_id`) and the node's `stackerdb_listener` tallies the forged `failed_txid`/`reason_code` toward `permanently_excluded_txids`/`temporarily_excluded_txids`, or the signer tallies it toward `RejectReasonPrefix::ReorgNotAllowed` weight — despite signer `S` never having asserted that reason for that block.

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

**File:** libsigner/src/v0/messages.rs (L1802-1814)
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
```

**File:** libsigner/src/v0/messages.rs (L1827-1838)
```rust
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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-540)
```rust
            if block_status
                .total_weight_rejected
                .saturating_add(self.weight_threshold)
                > self.total_weight
            {
                info!(
                    "{}/{} signer weight votes to reject block",
                    block_status.total_weight_rejected, self.total_weight;
                    "signer_signature_hash" => %block_signer_sighash,
                );
                counters.bump_naka_rejected_blocks();

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

**File:** stacks-node/src/tests/signer/v0/failed_txs.rs (L181-224)
```rust
    for (i, signer_private_key) in signer_test.signer_stacks_private_keys.iter().enumerate() {
        let mut rejection = BlockRejection::new(
            proposed_sighash.clone(),
            RejectReason::ValidationFailed(reject_code),
            signer_private_key,
            false, // testnet
            get_epoch_time_secs().saturating_add(u64::MAX),
            get_epoch_time_secs().saturating_add(u64::MAX),
        );
        rejection.response_data.failed_txid = Some(txid_a0.clone());

        let message = SignerMessage::BlockResponse(BlockResponse::Rejected(rejection));

        let signers_contract_id =
            MessageSlotID::BlockResponse.stacker_db_contract(false, reward_cycle);
        let mut session = StackerDBSession::new(
            &signer_test.running_nodes.conf.node.rpc_bind,
            signers_contract_id,
            signer_test.running_nodes.conf.miner.stackerdb_timeout,
        );

        let signer_addr = to_addr(signer_private_key);
        let slot_id = signer_slots
            .iter()
            .position(|(addr, _)| addr == &signer_addr)
            .expect("Signer not found in slot list") as u32;

        info!("------------------------- Manually submitting signer {i} (slot {slot_id}) block rejection -------------------------");
        let mut accepted = false;
        let mut version = 0;
        let start = Instant::now();
        while !accepted {
            let mut chunk = StackerDBChunkData::new(slot_id, version, message.serialize_to_vec());
            chunk
                .sign(signer_private_key)
                .expect("Failed to sign message chunk");
            let result = session.put_chunk(&chunk).expect("Failed to put chunk");
            accepted = result.accepted;
            version += 1;
            assert!(
                start.elapsed() < Duration::from_secs(30),
                "Timed out waiting for signer {i} rejection to be accepted"
            );
        }
```
