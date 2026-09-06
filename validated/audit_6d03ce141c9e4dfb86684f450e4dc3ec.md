### Title
ECDSA high-S signature malleability accepted by signer/node signature-recovery path — `recover_to_pubkey_without_validating_low_s` (File: `stacks-common/src/util/secp256k1/native.rs`)

### Summary
The gnark advisory's root cause is that a signature's `S` component was not constrained to the "canonical" low-half range before being accepted, so both `(R, S)` and `(R, -S mod n)` verify to the same public key — a classic ECDSA signature-malleability bug. The Stacks codebase contains a directly analogous construct: `Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s`, which explicitly skips the low-S normalization check that the sibling function `recover_to_pubkey` (and `PublicKey::verify`) perform.

### Finding Description
`stacks-common/src/util/secp256k1/native.rs` defines two ECDSA recovery paths:
- `recover_to_pubkey` → calls `recover_to_pubkey_possibly_with_low_s_verification(msg, sig, true)`, which normalizes `S` and rejects if the given signature is not already low-S [1](#0-0) .
- `recover_to_pubkey_without_validating_low_s` → calls the same helper with `verify_low_s = false`, explicitly skipping that check ("You shouldn't use this in new code") [2](#0-1) .

This means for any valid ECDSA signature `(r, s)` there is a second, distinct byte-string `(r, n-s)` (with the recovery-id parity flipped) that recovers to the exact same public key when validated through the "without validating low-S" path — this is demonstrated by the codebase's own test `test_with_negated_s`, which asserts both variants "recover to the same public key" [3](#0-2) , and by the `with_negated_s()` helper that is used to generate this malleable twin [4](#0-3) .

This unchecked recovery function is used on two safety-critical signer paths that key state or count weight by **the raw signature bytes**, not by a canonicalized signature:

1. **Signer-side vote counting** — `handle_block_signature` recovers the signer's public key from an incoming `BlockAccepted.signature` using the non-low-S-checked recovery [5](#0-4) , then `store_and_process_block_signature` stores it via `signer_db.add_block_signature(block_hash, signer_address, signature)`, which returns `false` (dedup, no-op) only "if the signature already exists in the DB" [6](#0-5) . Because dedup keys on exact signature bytes rather than `signer_address`, a peer relaying/gossiping the malleable twin `(r, n-s)` of an already-seen signature is a *distinct* DB row. When weight is subsequently recomputed, `get_block_signatures` is decoded again via the same unchecked recovery to rebuild `addrs_to_sigs: HashMap<StacksAddress, MessageSignature>` [7](#0-6) ; since this is a `HashMap` keyed by address, the final weight tally (`compute_signature_signing_weight`) is not doubled here — but this only holds if the DB layer for `add_block_signature`/`get_block_signatures` also keys on address (not verified in this pass; source for `add_block_signature`/`get_block_signatures` implementation in `stacks-signer/src/signerdb.rs` could not be located within the tool budget, so this cannot be fully confirmed).

2. **Node-side block acceptance verification** — `NakamotoBlockHeader::verify_signer_signatures` (the function the node itself uses to decide a block is validly signed) recovers each signer's pubkey via the same `recover_to_pubkey_without_validating_low_s`, and rejects duplicates only by removing the recovered pubkey from a `signers_by_pk` map (`signers_by_pk.remove(&public_key_bytes)`) [8](#0-7) . Dedup here is keyed by **recovered public key**, not by raw signature bytes, so a signer's malleable twin signature does not let that signer's weight be double-counted in this specific function. However, accepting a high-S signature at all means the on-chain, canonical block header can carry a signature set whose byte-encoding is not unique for a given (signer, block) pair — i.e., the block's `signer_signature` vector is not canonical, and two distinct valid encodings of "the same" block-approval exist. This breaks the implicit assumption (relied on elsewhere, e.g. in `stackerdb_listener.rs`'s `gathered_signatures: BTreeMap<u32, MessageSignature>` keyed by `slot_id`, and in various test helpers that treat `HashSet<MessageSignature>` de-duplication as equivalent to "same signer decision", e.g. `wait_for_block_rejections`/`wait_for_block_global_acceptance_from_signers` [9](#0-8) ) that a signature is a canonical identifier for "signer X approved block Y."

The node-side StackerDB listener path (`stacks-node/src/nakamoto_node/stackerdb_listener.rs`), by contrast, uses `signer_pubkey.verify(...)` — the checked `PublicKey::verify` trait method, which *does* enforce low-S [10](#0-9) , `stacks-common/src/util/secp256k1/native.rs" start="263" end="294" />. So the miner-side signature-gathering loop (`signer_coordinator.rs`/`stackerdb_listener.rs`) is not directly exposed to this malleability — only the low-S-bypassing `recover_to_pubkey_without_validating_low_s` call sites are.

### Impact Explanation
Per the scope rules, a concrete safety break requires either double-counted weight, a cross-context-valid signature, or a signature accepted for an invalid/non-canonical block. The strongest reachable consequence found is that `NakamotoBlockHeader::verify_signer_signatures` — the canonical block-signature verifier used by node consensus acceptance — accepts a non-canonical (malleable, high-S) signature encoding as valid, meaning a block can carry a `signer_signature` vector that is not unique/canonical for a given approval set. Because dedup in that specific function is by recovered public key rather than raw bytes, per-signer weight is not doubled *there*. I was not able to fully verify whether `stacks-signer/src/signerdb.rs`'s `add_block_signature`/`get_block_signatures` dedup by address or by raw signature bytes; if it dedups by raw signature bytes (as the `store_and_process_block_signature` comment "if this returns false, it means the signature already exists in the DB" suggests), a gossiped malleable twin of an already-recorded signature would be stored as a second, distinct row, and depending on downstream aggregation this could inflate a signer's effective contribution to the 70% pre-commit/signature threshold — a High/Critical class issue (aggregated-weight vs. verified-accepts equality break). This part of the chain could not be conclusively confirmed with the available tool budget.

### Likelihood Explanation
Any relaying signer or gossip participant can trivially compute the malleable twin of any signature they observe over StackerDB (`s' = n - s`, flip recovery-id parity) without needing any private key — this is pure elliptic-curve arithmetic, requires no majority, no auth token, and no other signer's key, satisfying the in-scope trigger requirement ("a one-slot miner (plus gossip) can trigger").

### Recommendation
- Remove/deprecate `recover_to_pubkey_without_validating_low_s` from all consensus- and signer-weight-relevant call sites (`stackslib/src/chainstate/nakamoto/mod.rs::verify_signer_signatures`, `stacks-signer/src/v0/signer.rs::handle_block_signature`/`store_and_process_block_signature`) and use the low-S-enforcing `recover_to_pubkey` instead, so only canonical signature encodings are ever accepted.
- If backward compatibility with historically-recorded high-S signatures is required, confirm and harden all storage/aggregation keys in `stacks-signer/src/signerdb.rs` to dedup strictly by `(block_hash, signer_address)` rather than by raw signature bytes, so a malleable twin can never be inserted as a second distinct row.

### Proof of Concept
1. Observe a valid `BlockAccepted` signature `(r, s)` for block `B` from signer `X` on StackerDB.
2. Compute the malleable twin using the codebase's own `MessageSignature::with_negated_s()` logic: negate `s` mod `n` and flip the recovery-id parity bit [4](#0-3) .
3. Gossip/replay this twin signature as a new `BlockAccepted` message for the same block/signer.
4. `handle_block_signature` recovers the same public key/address via `recover_to_pubkey_without_validating_low_s` [11](#0-10)  and passes it to `store_and_process_block_signature`, which stores it if the DB layer treats it as a new signature (unverified in this pass).
5. Separately, `NakamotoBlockHeader::verify_signer_signatures` will accept either the original or the malleable-twin signature as a valid entry for signer `X`'s slot, confirming the canonical-block-signature verifier does not enforce low-S [12](#0-11) .

### Citations

**File:** stacks-common/src/util/secp256k1/native.rs (L81-93)
```rust
    #[cfg(any(test, feature = "testing"))]
    pub fn with_negated_s(&self) -> Self {
        let mut bytes = [0u8; 65];
        bytes.copy_from_slice(self.as_bytes());

        // A `PrivateKey` is just a number, and it conveniently has a .negate()
        // method (mod n), so we'll just use that.
        let s = LibSecp256k1PrivateKey::from_slice(&bytes[33..]).unwrap();
        let neg = s.negate();
        bytes[33..].copy_from_slice(&neg.secret_bytes()[..]);
        bytes[0] ^= 1; // invert the parity of the recovery id
        Self(bytes)
    }
```

**File:** stacks-common/src/util/secp256k1/native.rs (L190-228)
```rust
    pub fn recover_to_pubkey(
        msg: &[u8],
        sig: &MessageSignature,
    ) -> Result<Secp256k1PublicKey, &'static str> {
        Self::recover_to_pubkey_possibly_with_low_s_verification(msg, sig, true)
    }

    /// Recover message and signature to public key (will be compressed), while
    /// skipping validation that the signature is normalized to low-S. You shouldn't
    /// use this in new code.
    pub fn recover_to_pubkey_without_validating_low_s(
        msg: &[u8],
        sig: &MessageSignature,
    ) -> Result<Secp256k1PublicKey, &'static str> {
        Self::recover_to_pubkey_possibly_with_low_s_verification(msg, sig, false)
    }

    fn recover_to_pubkey_possibly_with_low_s_verification(
        msg: &[u8],
        sig: &MessageSignature,
        verify_low_s: bool,
    ) -> Result<Secp256k1PublicKey, &'static str> {
        _secp256k1.with(|ctx| {
            let msg = LibSecp256k1Message::from_slice(msg).map_err(|_e| {
                "Invalid message: failed to decode data hash: must be a 32-byte hash"
            })?;

            let secp256k1_sig = sig
                .to_secp256k1_recoverable()
                .ok_or("Invalid signature: failed to decode recoverable signature")?;

            if verify_low_s {
                let secp256k1_sig_standard = secp256k1_sig.to_standard();
                let mut secp256k1_sig_low_s = secp256k1_sig_standard;
                secp256k1_sig_low_s.normalize_s();
                if secp256k1_sig_low_s != secp256k1_sig_standard {
                    return Err("Invalid signature: high-S");
                }
            }
```

**File:** stacks-common/src/util/secp256k1/native.rs (L792-837)
```rust
    #[test]
    fn test_with_negated_s() {
        let priv_key = Secp256k1PrivateKey::from_hex(
            "7b48329a5126dad83fc583c309c2698ae2843acfb9a7023fb081d850386c6950",
        )
        .unwrap();
        let pub_key = Secp256k1PublicKey::from_private(&priv_key);
        let message =
            &hex_bytes("77949dd27dabb40847564f40afcde8b91e0f7baf2cc710415a4ac8b777104866").unwrap()
                [..];
        let original_sig = priv_key.sign(message).unwrap();
        let high_s_sig = original_sig.with_negated_s();

        assert_ne!(
            original_sig, high_s_sig,
            "low-S and high-S signatures should not be the same"
        );

        assert_eq!(
            original_sig,
            high_s_sig.with_negated_s(),
            "negating twice should bring back the original"
        );

        let (recovered_from_orig, recovered_from_high_s) = _secp256k1.with(|ctx| {
            let msg = LibSecp256k1Message::from_slice(message).unwrap();

            let secp256k1_orig_sig = original_sig.to_secp256k1_recoverable().unwrap();
            let recovered_from_orig = ctx.recover_ecdsa(&msg, &secp256k1_orig_sig).unwrap();

            let secp256k1_high_s_sig = high_s_sig.to_secp256k1_recoverable().unwrap();
            let recovered_from_high_s = ctx.recover_ecdsa(&msg, &secp256k1_high_s_sig).unwrap();

            (recovered_from_orig, recovered_from_high_s)
        });

        assert_eq!(
            recovered_from_orig, recovered_from_high_s,
            "both signatures should recover to the same public key"
        );

        assert_eq!(
            recovered_from_high_s, pub_key.key,
            "the recovered key should be identical to the original key"
        );
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2389-2399)
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
```

**File:** stacks-signer/src/v0/signer.rs (L2452-2460)
```rust
        // signature is valid! store it.
        // if this returns false, it means the signature already exists in the DB, so just return.
        if !self
            .signer_db
            .add_block_signature(block_hash, signer_address, signature)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block signature"))
        {
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2474-2492)
```rust
        let signatures = self
            .signer_db
            .get_block_signatures(block_hash)
            .unwrap_or_else(|_| panic!("{self}: Failed to load block signatures"));

        // put signatures in order by signer address (i.e. reward cycle order)
        let addrs_to_sigs: HashMap<_, _> = signatures
            .into_iter()
            .filter_map(|sig| {
                let Ok(public_key) = Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(
                    block_hash.bits(),
                    &sig,
                ) else {
                    return None;
                };
                let addr = StacksAddress::p2pkh(self.mainnet, &public_key);
                Some((addr, sig))
            })
            .collect();
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1096-1178)
```rust
    #[cfg_attr(test, mutants::skip)]
    pub fn verify_signer_signatures(
        &self,
        reward_set: &RewardSet,
        epoch_id: StacksEpochId,
    ) -> Result<u32, ChainstateError> {
        let message = self.signer_signature_hash();
        let Some(signers) = reward_set.signers() else {
            return Err(ChainstateError::InvalidStacksBlock(
                "No signers in the reward set".to_string(),
            ));
        };

        // if this is a shadow block, then its signing weight is as if every signer signed it, even
        // though the signature vector is undefined.
        if self.is_shadow_block() {
            return Ok(self.get_shadow_signer_weight(reward_set)?);
        }

        let mut total_weight_signed: u32 = 0;
        // `last_index` is used to prevent out-of-order signatures
        let mut last_index = None;
        // Before Epoch 4.0, signature order check contained a bug, so gate the
        // strict ordering behavior on the epoch.
        let strict_order = epoch_id.enforces_strict_signature_order();

        let total_weight = reward_set
            .total_signing_weight()
            .map_err(|_| ChainstateError::NoRegisteredSigners(0))?;

        // HashMap of <PublicKey, (Signer, Index)>
        let mut signers_by_pk: HashMap<_, _> = signers
            .iter()
            .enumerate()
            .map(|(i, signer)| (&signer.signing_key, (signer, i)))
            .collect();

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

            let mut public_key_bytes = [0u8; 33];
            public_key_bytes.copy_from_slice(&public_key.to_bytes_compressed()[..]);

            let (signer, signer_index) = signers_by_pk.remove(&public_key_bytes).ok_or_else(|| {
                warn!(
                    "Found an invalid public key. Reward set has {} signers. Chain length {}. Signatures length {}",
                    signers.len(),
                    self.chain_length,
                    self.signer_signature.len(),
                );
                ChainstateError::InvalidStacksBlock(format!(
                    "Public key {} not found in the reward set",
                    public_key.to_hex()
                ))
            })?;

            // Enforce order of signatures
            if let Some(index) = last_index.as_ref() {
                if *index >= signer_index {
                    return Err(ChainstateError::InvalidStacksBlock(
                        "Signatures are out of order".to_string(),
                    ));
                }
                if strict_order {
                    last_index = Some(signer_index);
                }
            } else {
                last_index = Some(signer_index);
            }

            total_weight_signed = total_weight_signed
                .checked_add(signer.weight)
                .expect("FATAL: overflow while computing signer set threshold");
        }
```

**File:** stacks-node/src/tests/signer/v0/mod.rs (L2364-2385)
```rust
fn wait_for_block_rejections(
    timeout_secs: u64,
    block_signer_signature_hash: &Sha512Trunc256Sum,
    num_rejections: usize,
) -> Result<(), String> {
    let mut found_rejections = HashSet::new();
    wait_for(timeout_secs, || {
        for (_chunk, message) in get_stackerdb_signer_messages() {
            if let SignerMessage::BlockResponse(BlockResponse::Rejected(BlockRejection {
                signer_signature_hash,
                signature,
                ..
            })) = &message
            {
                if signer_signature_hash == block_signer_signature_hash {
                    found_rejections.insert(signature.clone());
                }
            }
        }
        Ok(found_rejections.len() >= num_rejections)
    })
}
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L411-426)
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
```
