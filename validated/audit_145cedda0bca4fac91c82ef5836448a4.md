### Title
Signer rejection reason/response data is not covered by the `BlockRejection` signature, allowing a valid rejection signature to be re-minted with a forged reason to invalidate a legitimate miner - ([File: libsigner/src/v0/messages.rs])

### Summary
`BlockRejection::hash()` — the message digest that the signer's rejection signature actually commits to — only covers `signer_signature_hash` (the block hash) plus `chain_id` (via the domain separator). It does **not** cover `reason`, `reason_code`, or `response_data` (which carries `reject_reason: RejectReason`, `failed_txid`, and the tenure/read-count extend timestamps). Since `verify()`/`recover_public_key()` only re-derive this same partial hash, any publicly observed, validly-signed `BlockRejection` for a given block can be re-packaged by anyone with arbitrary `reason_code`/`response_data` while keeping the original signature bytes, and it will still authenticate as coming from that signer. This is analogous to the sha2 bug class: a digest computation that silently omits part of its logical input, so two semantically different messages collapse to the same authenticated value.

### Finding Description
- `BlockRejection::hash()`: [1](#0-0) 
only feeds `self.signer_signature_hash` (and `chain_id` via `make_structured_data_domain`) into `structured_data_message_hash`. The struct itself additionally carries `reason`, `reason_code`, and `response_data`: [2](#0-1) 
none of which are part of the signed preimage.

- `verify()` and `recover_public_key()` both re-derive `self.hash()` and check the signature only against that: [3](#0-2) 

- On receipt, `handle_block_rejection` authenticates purely via `rejection.recover_public_key()` + `is_valid_signer`, then hands the (unauthenticated) `reject_reason` straight into the vote-tally path: [4](#0-3) 

- `store_and_process_block_rejection` records `(&rejection.response_data.reject_reason).into()` — i.e. the forgeable field — as the tallied reason, and once a rejection-weight threshold is reached it computes `total_reorg_reject_weight` filtered specifically on `RejectReasonPrefix::ReorgNotAllowed`. If that crosses `min_weight`, it flips `cur_sortition.miner_status = SortitionMinerStatus::InvalidatedBeforeFirstBlock` for the *current, otherwise-valid* miner: [5](#0-4) 

- Once a sortition's miner status is `InvalidatedBeforeFirstBlock`, every subsequent proposal from that (legitimate) miner in that tenure is rejected by every honest signer via the `ProposedBy::CurrentSortition` check: [6](#0-5) 

Because the rejection reason is never covered by the signature, an attacker (any StackerDB observer — no private key, no majority of signers needed) can take real rejection signatures that already exist for a block (rejected for any mundane reason — malformed proposal, consensus-hash mismatch, stale tip, etc. — which routinely accumulate ≥30% weight whenever any block is globally rejected) and re-broadcast each of them with `response_data.reject_reason` overwritten to `ReorgNotAllowed`, reusing the untouched signature bytes. Every receiving signer will accept these as authentic rejections from the corresponding signers (the signature check only covers the block hash) and tally them under `RejectReasonPrefix::ReorgNotAllowed`. If the relabeled weight crosses `min_weight`, the currently-valid miner is wrongly invalidated across the signer set.

### Impact Explanation
This breaks the "rejection recounted"/"cross-context-valid signature" equality the state machine depends on: the *authenticated* fact is only "signer S rejected block B", but the *consequential* fact acted upon ("signer S rejected block B specifically because of a disallowed reorg") is unauthenticated and forgeable. The result is a state-machine wedge that matches the High-severity bar: the legitimate current miner is marked invalid and can no longer get any of its (valid, canonical) blocks signed for the rest of its tenure — a liveness wedge triggered without a majority of signers' keys, the auth token, or local access; only observation of otherwise-public StackerDB gossip traffic that already exists whenever any block collects normal rejections.

### Likelihood Explanation
Reasonably likely to be exploitable whenever a block is globally rejected for any reason (a routine, frequent event — malformed proposals, stale/duplicate proposals, consensus mismatches all produce ≥30% rejection weight naturally). The attacker does not need to compromise any signer key, does not need first-mover position as the miner, and does not need to coordinate with any signer — they only need to observe already-broadcast `BlockRejection` messages (which are, by design, publicly relayed over StackerDB) and re-emit modified copies with the same signature bytes.

### Recommendation
Include `reason_code` (or the full `response_data`, at minimum `reject_reason`) inside the signed preimage of `BlockRejection::hash()`, e.g. by adding these fields to the `Value` passed into `structured_data_message_hash`, and reject any incoming `BlockRejection` whose signature does not authenticate over the reason/response data it claims to carry. Apply the same audit to `BlockAccepted`, whose signature likewise only covers `signer_signature_hash` and not `response_data` (`tenure_extend_timestamp`, `read_count_extend_timestamp`), since those fields also drive downstream node/coordinator behavior.

### Proof of Concept
1. Wait for (or trigger) a block proposal that a normal minority of signers reject for a mundane reason (e.g. `ConsensusHashMismatch`), producing several real, validly-signed `BlockRejection` messages on StackerDB with `response_data.reject_reason = ConsensusHashMismatch`.
2. Capture these messages (public StackerDB chunks) and construct new `BlockRejection` structs with the same `signer_signature_hash`, `chain_id`, and `signature` bytes, but with `reason_code`/`response_data.reject_reason` overwritten to `RejectReason::ReorgNotAllowed` (`(&reject_reason).into()` at `libsigner/src/v0/messages.rs:1748-1749`).
3. Re-broadcast these forged messages on the `.signers-BlockResponse` StackerDB contract.
4. Every signer's `handle_block_rejection` → `rejection.recover_public_key()`/`verify()` succeeds (the signature only ever covered `signer_signature_hash`), so the forged reason is accepted and stored via `add_block_rejection_signer_addr` with the attacker-chosen `RejectReasonPrefix::ReorgNotAllowed`.
5. Once `total_reorg_reject_weight` (computed at `stacks-signer/src/v0/signer.rs:2354-2358`) crosses `min_weight`, each signer sets `cur_sortition.miner_status = SortitionMinerStatus::InvalidatedBeforeFirstBlock` for the current, legitimate miner, and all further valid proposals from that miner are rejected (`stacks-signer/src/chainstate/v1.rs:288-300`) — a liveness wedge achieved without any signer key or majority collusion.

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

**File:** libsigner/src/v0/messages.rs (L1816-1838)
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

**File:** stacks-signer/src/v0/signer.rs (L2267-2369)
```rust
    // Store the block rejection signature and check if we have reached a consensus decision on the block because of it. If we have, update the block state accordingly.
    fn store_and_process_block_rejection(
        &mut self,
        sortition_state: &mut Option<SortitionsView>,
        block_info: &mut BlockInfo,
        signer_address: &StacksAddress,
        reject_reason: RejectReasonPrefix,
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

**File:** stacks-signer/src/chainstate/v1.rs (L288-300)
```rust
        // check that this miner is the most recent sortition
        match proposed_by {
            ProposedBy::CurrentSortition(sortition) => {
                if sortition.miner_status != SortitionMinerStatus::Valid {
                    warn!(
                        "Current miner behaved improperly, this signer views the miner as invalid.";
                        "proposed_block_consensus_hash" => %block.header.consensus_hash,
                        "signer_signature_hash" => %block.header.signer_signature_hash(),
                        "current_sortition_miner_status" => ?sortition.miner_status,
                    );
                    return Err(RejectReason::InvalidMiner);
                }
            }
```
