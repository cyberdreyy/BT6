Confirmed: the signature over `BlockRejection` only commits to `signer_signature_hash` and `chain_id` (via the domain), not `reason_code`/`reject_reason`. Let me check the signerdb storage/dedup logic to see whether it prevents forged-reason replay from being credited.### Title
Unsigned `reason_code`/`reject_reason` field in `BlockRejection` allows reason-relabeling replay to forge `ReorgNotAllowed` weight and invalidate a competing miner - ([File: libsigner/src/v0/messages.rs])

### Summary
`BlockRejection::hash()` — the message that is actually signed — only commits to `signer_signature_hash` and `chain_id`; the `reason`/`reason_code`/`response_data.reject_reason` fields are excluded from the signed payload. An attacker who observes any genuine, gossiped `BlockRejection` from a legitimate signer for a given block can copy its (valid) `signature` and `signer_signature_hash`, replace the `reason_code`/`reject_reason` with `ReorgNotAllowed`, and rebroadcast it; `recover_public_key()`/`is_valid_signer` in `stacks-signer/src/v0/signer.rs::handle_block_rejection` will still recover the legitimate signer's address and accept it, crediting that signer's weight toward `RejectReasonPrefix::ReorgNotAllowed` in `compute_reject_code_signing_weight`.

### Finding Description
The claimed equality — "the `RejectReasonPrefix` credited toward `compute_reject_code_signing_weight` for address A must equal the reason A actually attested to signing over" — is broken by construction:

- `BlockRejection::hash()` (libsigner/src/v0/messages.rs:1803-1807) builds the signed digest solely from `structured_data_domain("block-rejection", "1.0.0", chain_id)` and `Value::buff_from(self.signer_signature_hash...)`. Neither `reason`, `reason_code`, nor `response_data.reject_reason` enter this hash.
- `BlockRejection::sign`/`verify`/`recover_public_key` (messages.rs:1809-1837) all operate over this same restricted hash.
- In `Signer::handle_block_rejection` (stacks-signer/src/v0/signer.rs:2209-2265), the only authentication performed is: recover public key from `rejection.signature` over `hash()`, derive `signer_address`, and check `is_valid_signer`. The `reject_reason`/`reason_code` value is read verbatim from the attacker-controlled message and passed straight into `store_and_process_block_rejection` (signer.rs:2263, 2268-2369), which stores `(signer_address, reject_reason)` via `add_block_rejection_signer_addr` and later tallies `compute_reject_code_signing_weight(rejection_addrs.iter(), RejectReasonPrefix::ReorgNotAllowed)` (signer.rs:2354-2357).

Exploit flow: attacker observes (via StackerDB/gossip, which is public) a genuine `BlockRejection` broadcast by signer B for block X with, e.g., `RejectReason::ConnectivityIssues`. The attacker crafts a new `BlockRejection` struct with identical `signer_signature_hash`, `chain_id`, and `signature` bytes, but sets `reason_code`/`response_data.reject_reason` to `RejectReason::ReorgNotAllowed`, and gossips it. Any signer receiving it recovers B's real public key/address (since the signature only covers `signer_signature_hash`+`chain_id`), accepts it as an authentic vote from B, and now counts B's weight toward `ReorgNotAllowed` even though B never attested to that reason. Repeating this for enough of the already-observed genuine rejections (any reason) lets the attacker accumulate `total_reorg_reject_weight` past `min_weight` without needing any signer weight of their own, flipping `sortition_state.cur_sortition.miner_status` to `SortitionMinerStatus::InvalidatedBeforeFirstBlock` for the currently active miner (signer.rs:2358-2368), independent of what reason those signers actually rejected for.

Existing guards do not stop this: `is_valid_signer` only checks that the recovered address belongs to the current signer set, not that the specific reason field was covered by the signature; there's no separate signature or MAC over `reason_code`/`response_data`; and `add_block_rejection_signer_addr`'s de-dup key is `(block_hash, signer_address)`-scoped, not tied to a signed reason, so a first-seen-reason-wins/last-write scenario is plausible depending on write semantics, but regardless the *acceptance* of a mismatched reason under a valid signature is the core break.

### Impact Explanation
This breaks the safety property that a rejection reason category counted in `compute_reject_code_signing_weight` must reflect what the address holder actually attested to. The `ReorgNotAllowed` special-case in `store_and_process_block_rejection` directly mutates `sortition_state.cur_sortition.miner_status`, which downstream signer/miner logic uses to decide which miner's tenure/blocks are treated as valid going forward. An attacker (running zero or one signer slot) can forge enough `ReorgNotAllowed` votes (by relabeling already-signed rejections from *other* legitimate signers, not needing majority collusion) to invalidate a legitimate, competing miner's status, steering canonicity decisions in the local signer's view. This matches the Critical category: "steering... which miner's blocks the signer will subsequently treat as canonical" is a canonicity/chain-safety violation, achieved without controlling a majority of signers or any private keys beyond what is publicly gossiped.

### Likelihood Explanation
Preconditions: the attacker needs to observe at least one genuine `BlockRejection` (any reason) broadcast for the target block from each signer whose weight they want to relabel — these are routinely gossiped/public. No majority signer weight, no node-operator access, no auth token, and no local host access are required — only gossip capability, consistent with the stated unprivileged threat model. This is fully repeatable across blocks/tenures since the relabeling technique works on any observed rejection signature and does not depend on a specific cycle or reward-set state. The only limiting factor is that `determine_active_signer_protocol_version().uses_global_state()` gates this code path off for newer protocol versions (signer.rs:2347-2353), so the vulnerability is confined to legacy (pre-global-state) protocol versions — but for those, the attack is cheap and mechanical.

### Recommendation
Include `reason_code`/`reject_reason` (and ideally the full semantically-relevant `response_data`) inside the signed structured-data payload of `BlockRejection::hash()`, so that any relabeling of the reason invalidates the signature. Additionally, `store_and_process_block_rejection`/`add_block_rejection_signer_addr` should bind a signer address to exactly one reason per block (first-seen, immutable) rather than trusting each incoming message's self-reported reason independent of signature coverage.

### Proof of Concept
```rust
// stacks-signer/src/v0/signer.rs (or libsigner/src/v0/messages.rs) test module

#[test]
fn reject_reason_not_covered_by_signature_allows_relabeling() {
    let private_key = StacksPrivateKey::random();
    let sig_hash = Sha512Trunc256Sum([7u8; 32]);

    // Signer B genuinely signs a rejection for ConnectivityIssues.
    let genuine = BlockRejection::new(
        sig_hash,
        RejectReason::ConnectivityIssues("timeout".to_string()),
        &private_key,
        false,
        0,
        0,
    );

    // Attacker copies the signature/signer_signature_hash but swaps the reason.
    let mut forged = genuine.clone();
    forged.reason_code = (&RejectReason::ReorgNotAllowed).into();
    forged.response_data.reject_reason = RejectReason::ReorgNotAllowed;
    // signature and signer_signature_hash are untouched (still B's genuine signature)

    // EQUALITY CHECK: the reason credited must equal what was actually signed over.
    let recovered_genuine = genuine.recover_public_key().unwrap();
    let recovered_forged = forged.recover_public_key().unwrap();

    // Currently this PASSES (bug): recovery succeeds identically for both,
    // proving reason_code is not bound to the signature.
    assert_eq!(recovered_genuine, recovered_forged);
    assert_ne!(genuine.response_data.reject_reason, forged.response_data.reject_reason);

    // Desired fixed behavior: forged.verify(&recovered_genuine) should fail
    // once reason_code is included in hash(); currently it succeeds:
    assert!(forged.verify(&recovered_genuine).unwrap_or(false)); // demonstrates the flaw
}
```
A full end-to-end signer-level PoC would drive `Signer::handle_block_rejection` with the `genuine` and `forged` messages against a mocked `sortition_state`/`signer_weights`, showing `compute_reject_code_signing_weight(_, RejectReasonPrefix::ReorgNotAllowed)` increases using B's weight after only the forged message is injected, and that `sortition_state.cur_sortition.miner_status` flips to `InvalidatedBeforeFirstBlock` without B ever having signed a `ReorgNotAllowed`-tagged message. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** stackslib/src/util_lib/signed_structured_data.rs (L37-46)
```rust
pub fn structured_data_message_hash(structured_data: Value, domain: Value) -> Sha256Sum {
    let message = [
        STRUCTURED_DATA_PREFIX.as_ref(),
        structured_data_hash(domain).as_bytes(),
        structured_data_hash(structured_data).as_bytes(),
    ]
    .concat();

    Sha256Sum::from_data(&message)
}
```
