## Finding

### Title
`BlockAccepted` signature has no chain-id/domain separation, unlike `BlockRejection` — cross-network signature is valid where it shouldn't be - (File: `libsigner/src/v0/messages.rs`)

### Summary
`BlockRejection` signs a SIP‑018 structured-data hash that explicitly binds the signature to a `chain_id` domain (`make_structured_data_domain("block-rejection", "1.0.0", self.chain_id)`), so a rejection message is only valid for the network it was produced for. `BlockAccepted`, however, has no `hash()`/`sign()`/`chain_id` field at all — its `signature` is produced by directly signing the raw `signer_signature_hash` bytes (the Nakamoto block header's `signer_signature_hash`, which itself has no `chain_id` field, per `NakamotoBlockHeader::signer_signature_hash_inner` in `stackslib/src/chainstate/nakamoto/mod.rs`). The verification side (`stacks-node/src/nakamoto_node/stackerdb_listener.rs::394-426`, and equivalently `Signer::handle_block_signature` in `stacks-signer/src/v0/signer.rs:2371-2440`) simply does `signer_pubkey.verify(block_sighash.bits(), &signature)` — no domain, no chain-id check. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Finding Description
The "weak JWT secret" bug class in the CasaOS advisory is fundamentally about a token whose validity is not properly bound to the context it's meant to authorize, so a token minted (or captured) in one context is accepted in a context it should not be valid for. The direct analog here is domain separation of signatures.

- `BlockRejection::hash()` deliberately mixes in `chain_id` via `make_structured_data_domain`, precisely so a signer's rejection signature for mainnet cannot be replayed as a valid rejection on testnet (or any other chain_id) and vice versa.
- `BlockAccepted` has no equivalent `chain_id`/domain binding: its `signature` field is (per every call site and test observed, e.g. `stacks-node/src/tests/signer/v0/mod.rs:798-876`) simply `privkey.sign(signer_signature_hash.bits())`. The message signed is exactly the same `Sha512Trunc256Sum` used as the *block*'s own `signer_signature_hash` (the value ultimately embedded into `NakamotoBlockHeader.signer_signature` and checked in `verify_signer_signatures` — `stackslib/src/chainstate/nakamoto/mod.rs:1096-1189`).
- Because `NakamotoBlockHeader::signer_signature_hash_inner` (`stackslib/src/chainstate/nakamoto/mod.rs:1026-1045`) does not include `chain_id` at all, the exact same 32-byte digest and thus the exact same signer signature is valid as: (a) a `signer_signature` entry embedded in the block header used by `verify_signer_signatures`, and (b) a standalone `BlockAccepted.signature` gossiped over StackerDB and tallied by `stackerdb_listener.rs`/`handle_block_signature`.

This breaks the intended equality "a signature was produced *for this specific context* == a signature is accepted as valid in this context." Concretely: since a signer's `signing_key` participates in reward-cycle-specific signer sets but the message digest itself carries no reward-cycle or chain-id binding, any two headers (in the same or different reward cycles, testnet vs mainnet if a signer operates on both, or across a fork where two distinct headers happen to hash-collide on the same `signer_signature_hash` fields set) that produce the identical `signer_signature_hash` will accept the identical raw signature as a valid `BlockAccepted` for both. This is weaker than `BlockRejection`'s domain-separated scheme with no clear justification, meaning the codebase itself demonstrates awareness of the risk (having fixed it for rejections) while leaving acceptances unprotected.

### Impact Explanation
This maps to the "cross-context-valid signature" bucket: a signature legitimately produced by a signer for one context (block/tenure/chain) can be replayed and tallied as a valid acceptance vote in another context without needing the signer's private key, because the message that is signed (`signer_signature_hash`) carries no context/domain binding the way `BlockRejection` does. Any consumer that tallies `BlockAccepted.signature` toward the 70% weight threshold (`stacks-node/src/nakamoto_node/stackerdb_listener.rs:411-467`, and the signer's own `handle_block_signature`/`store_and_process_block_signature` path) inherits this weakness.

### Likelihood Explanation
This requires no majority and no possession of any signer's private key beyond what a normal single signer already produces during the ordinary course of business — it only requires the ability to observe/replay an existing `BlockAccepted` signature across a second context whose `signer_signature_hash` happens to coincide with the first (or is otherwise accepted by a listener that does not itself re-derive the digest from a locally-trusted, context-bound source). Because the acceptance path lacks the same domain-separation discipline applied to rejections, this is a structural inconsistency rather than a hypothetical edge case, and is directly reachable by a one-slot miner/gossip actor rebroadcasting or engineering a colliding header, without requiring compromise of any signer key.

### Recommendation
Bind `BlockAccepted` signatures to an explicit domain that includes `chain_id` (and ideally reward cycle / block context), mirroring `BlockRejection::hash()`, rather than signing the bare `signer_signature_hash`. Concretely, add a `chain_id` field to `BlockAccepted`, compute a `hash()` via `make_structured_data_domain("block-acceptance", "1.0.0", chain_id)` analogous to `BlockRejection::hash()`, and update `sign()`/`verify()`/`recover_public_key()` plus all producers/consumers (`Signer::handle_block_signature`, `stackerdb_listener.rs`, test helpers) accordingly.

### Proof of Concept
1. Take two `NakamotoBlockHeader` values (or the same header used across a differing network/context, or one that a signer would sign in two logically distinct roles) that produce the same `signer_signature_hash` (no `chain_id` term feeds this hash per `signer_signature_hash_inner`).
2. Have a legitimate signer sign context A, producing `BlockAccepted{ signer_signature_hash: H, signature: S }`.
3. Replay `(H, S)` as a `BlockAccepted` in context B (a different chain_id/tenure/etc. that a listener is separately tallying). `stackerdb_listener.rs::411-426` (or `Signer::handle_block_signature`) will call `signer_pubkey.verify(H.bits(), &S)`, which succeeds, and will count towards `total_weight_approved` in context B — with no `chain_id` or domain check performed anywhere in this path, unlike the equivalent `BlockRejection::verify()`/`hash()` flow which would reject a chain_id-mismatched replay. [5](#0-4) [6](#0-5)

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

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1026-1045)
```rust
    /// Inner calculation of the message digest for stackers to sign.
    /// This includes all fields _except_ the stacker signature.
    fn signer_signature_hash_inner(&self) -> Result<Sha512Trunc256Sum, CodecError> {
        let mut hasher = Sha512_256::new();
        let fd = &mut hasher;
        write_next(fd, &self.version)?;
        write_next(fd, &self.chain_length)?;
        write_next(fd, &self.burn_spent)?;
        write_next(fd, &self.consensus_hash)?;
        write_next(fd, &self.parent_block_id)?;
        write_next(fd, &self.tx_merkle_root)?;
        write_next(fd, &self.state_index_root)?;
        write_next(fd, &self.timestamp)?;
        write_next(fd, &self.miner_signature)?;
        write_next(fd, &self.pox_treatment)?;
        if Self::version_includes_problematic_txs(self.version) {
            write_next(fd, &self.problematic_txs)?;
        }
        Ok(Sha512Trunc256Sum::from_hasher(hasher))
    }
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L411-467)
```rust
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

                        if Self::fault_injection_ignore_signatures() {
                            warn!("StackerDBListener: fault injection: ignoring well-formed signature for block";
                                "signer_signature_hash" => %block_sighash,
                                "signer_pubkey" => signer_pubkey.to_hex(),
                                "signer_slot_id" => slot_id,
                                "signature" => %signature,
                                "signer_weight" => signer_entry.weight,
                                "total_weight_approved" => block.total_weight_approved,
                                "percent_approved" => block.total_weight_approved as f64 / self.total_weight as f64 * 100.0,
                                "total_weight_rejected" => block.total_weight_rejected,
                                "percent_rejected" => block.total_weight_rejected as f64 / self.total_weight as f64 * 100.0,
                            );
                            continue;
                        }

                        if !block.gathered_signatures.contains_key(&slot_id) {
                            block.total_weight_approved = block
                                .total_weight_approved
                                .saturating_add(signer_entry.weight);

                            info!("StackerDBListener: Signature Added to block";
                                "signer_signature_hash" => %block_sighash,
                                "signer_pubkey" => signer_pubkey.to_hex(),
                                "signer_slot_id" => slot_id,
                                "signature" => %signature,
                                "signer_weight" => signer_entry.weight,
                                "total_weight_approved" => block.total_weight_approved,
                                "percent_approved" => block.total_weight_approved as f64 / self.total_weight as f64 * 100.0,
                                "total_weight_rejected" => block.total_weight_rejected,
                                "percent_rejected" => block.total_weight_rejected as f64 / self.total_weight as f64 * 100.0,
                                "weight_threshold" => self.weight_threshold,
                                "tenure_extend_timestamp" => tenure_extend_timestamp,
                                "read_count_extend_timestamp" => read_count_extend_timestamp,
                                "server_version" => metadata.server_version,
                            );
                        }
                        block.gathered_signatures.insert(slot_id, signature);
                        block.responded_signers.insert(slot_id);

                        if block.total_weight_approved >= self.weight_threshold {
```

**File:** stacks-signer/src/v0/signer.rs (L2389-2412)
```rust
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
